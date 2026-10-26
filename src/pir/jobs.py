"""In-memory scan jobs (ARCHITECTURE section 7.7).

Each job runs as an asyncio task in the single worker. Its event log is append-only; the job is
the `EventSink` the runner emits to. Queued and running jobs are never evicted; finished jobs go
after `JOB_TTL_S`, and at most `MAX_JOBS` finished ones are kept (oldest finished first).
"""

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Literal

from pir import messages
from pir.agent_client import LocalFixtureTarget, SandboxFailed, SandboxTimeout
from pir.deps import PipelineDeps
from pir.executor.base import ExecutorSpendDenied
from pir.github import ValidatedTarget
from pir.logs import bind_scan
from pir.models import Mode, StepName
from pir.models.api import ProgressEvent, ScanStatusResponse
from pir.models.report import Finding, Proof, ScanReport, StepStatus
from pir.pipeline import STEP_ORDER
from pir.reasoning.equivalences import implied_symbols
from pir.replay import EventSink
from pir.spend import Clock, ScanBudget

logger = logging.getLogger("pir.jobs")

JobState = Literal["queued", "running", "completed", "failed"]
Runner = Callable[[EventSink], Awaitable[ScanReport]]


class ScanNotFound(KeyError):
    """No job with that id (never created, or evicted)."""


class Job:
    """One scan: its state, the append-only event log and the report once there is one."""

    def __init__(self, scan_id: str, mode: Mode, created_at: datetime) -> None:
        self.scan_id = scan_id
        self.mode: Mode = mode
        self.created_at = created_at
        self.finished_at: datetime | None = None
        self.state: JobState = "queued"
        self.report: ScanReport | None = None
        self.log: list[ProgressEvent] = []
        self.task: asyncio.Task[None] | None = None
        self._changed = asyncio.Condition()

    @property
    def finished(self) -> bool:
        return self.state in ("completed", "failed")

    async def emit(self, event: ProgressEvent) -> None:
        async with self._changed:
            self.log.append(event)
            self._changed.notify_all()

    async def _finish(self, state: JobState, finished_at: datetime) -> None:
        async with self._changed:
            self.state = state
            self.finished_at = finished_at
            self._changed.notify_all()

    async def stream(self, after_seq: int = 0) -> AsyncIterator[ProgressEvent]:
        """Every stored event with `seq > after_seq`, then live ones, until the job is finished."""
        index = 0
        while True:
            async with self._changed:
                await self._changed.wait_for(lambda: index < len(self.log) or self.finished)
                pending = self.log[index:]
                index = len(self.log)
                done = self.finished
            for event in pending:
                if event.seq > after_seq:
                    yield event
            if done and index >= len(self.log):
                return

    def steps(self) -> list[StepStatus]:
        """The report's steps when there is a report, else the steps folded from the events."""
        if self.report is not None:
            return list(self.report.steps)
        folded: dict[StepName, StepStatus] = {
            name: StepStatus(
                name=name,
                state="pending",
                tools=[],
                started_at=None,
                finished_at=None,
                message=None,
            )
            for name in STEP_ORDER
        }
        for event in list(self.log):
            if event.type != "step" or event.step is None or event.state is None:
                continue
            previous = folded[event.step]
            terminal = event.state in ("done", "failed", "skipped")
            folded[event.step] = StepStatus(
                name=event.step,
                state=event.state,
                tools=list(event.tools),
                started_at=previous.started_at
                if previous.started_at is not None
                else (event.at if event.state == "running" else None),
                finished_at=event.at if terminal else None,
                message=event.message,
            )
        return [folded[name] for name in STEP_ORDER]

    def status(self) -> ScanStatusResponse:
        return ScanStatusResponse(
            scan_id=self.scan_id,
            mode=self.mode,
            state=self.state,
            steps=self.steps(),
            outcome=self.report.outcome if self.report is not None else None,
            created_at=self.created_at,
            finished_at=self.finished_at,
        )


class JobManager:
    def __init__(self, clock: Clock, *, ttl_s: float = 3600, max_jobs: int = 200) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._max_jobs = max_jobs
        self._jobs: dict[str, Job] = {}

    def __len__(self) -> int:
        return len(self._jobs)

    def create(self, mode: Mode, runner: Runner, *, scan_id: str | None = None) -> Job:
        """Register a job and start its task. `scan_id` lets admission reserve spend under the
        same id first. Must be called from the running event loop."""
        self._evict()
        scan_id = scan_id or uuid.uuid4().hex
        if scan_id in self._jobs:
            raise ValueError("a job with that scan id exists")
        job = Job(scan_id, mode, self._clock.now())
        self._jobs[scan_id] = job
        job.task = asyncio.get_running_loop().create_task(self._run(job, runner))
        return job

    def get(self, scan_id: str) -> Job:
        self._evict()
        try:
            return self._jobs[scan_id]
        except KeyError:
            raise ScanNotFound(scan_id) from None

    def events(self, scan_id: str, after_seq: int = 0) -> AsyncIterator[ProgressEvent]:
        return self.get(scan_id).stream(after_seq)

    def running(self, mode: Mode | None = None) -> int:
        """Jobs not finished yet (queued or running), optionally of one mode."""
        return sum(1 for j in self._jobs.values() if not j.finished and mode in (None, j.mode))

    async def shutdown(self) -> None:
        """Cancel unfinished jobs and wait for their tasks."""
        tasks = [j.task for j in self._jobs.values() if j.task is not None and not j.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job: Job, runner: Runner) -> None:
        bind_scan(job.scan_id)  # first, so every line this task writes carries the scan id
        job.state = "running"
        try:
            job.report = await runner(job)
        except asyncio.CancelledError:
            await self._fail(job, "the scan was cancelled")
            raise
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]
            logger.error(
                "scan job failed: %s",
                type(exc).__name__,
                exc_info=True,
                extra={"error_id": error_id},
            )
            await self._fail(job, messages.internal_error(error_id))
            return
        await job._finish("completed", self._clock.now())

    async def _fail(self, job: Job, text: str) -> None:
        await job.emit(
            ProgressEvent(
                seq=(job.log[-1].seq + 1) if job.log else 1,
                scan_id=job.scan_id,
                type="error",
                step=None,
                state=None,
                message=text[:300],
                at=self._clock.now(),
                outcome=None,
                tools=[],
            )
        )
        await job._finish("failed", self._clock.now())

    def _evict(self) -> None:
        now = self._clock.now()
        finished = [j for j in self._jobs.values() if j.finished and j.finished_at is not None]
        for job in finished:
            assert job.finished_at is not None
            if (now - job.finished_at).total_seconds() > self._ttl_s:
                del self._jobs[job.scan_id]
        remaining = sorted(
            (j for j in self._jobs.values() if j.finished and j.finished_at is not None),
            key=lambda j: j.finished_at,  # type: ignore[arg-type, return-value]
        )
        for job in remaining[: max(0, len(remaining) - self._max_jobs)]:
            del self._jobs[job.scan_id]


# ---- call-chain proof (M2, cuttable; ARCHITECTURE section 16, ADR-0039, ADR-0041) ---------------

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PROOF_SANDBOX_TIMEOUT = "sandbox timeout"


def proof_entries(finding: Finding) -> list[str]:
    """The repository steps of the call path, in order: the allowed `ProofRequest.entry` values."""
    return [s.symbol for s in finding.call_path or [] if s.edge != "vulnerable_call"]


def with_proof(report: ScanReport, finding_id: str, proof: Proof) -> ScanReport:
    """`report` with `proof` set on one finding. Nothing else changes, the verdict included."""
    findings = [
        f.model_copy(update={"proof": proof}) if f.finding_id == finding_id else f
        for f in report.findings
    ]
    return report.model_copy(update={"findings": findings})


def _module_matches(target: str, symbol: str) -> bool:
    return target == symbol or target.startswith(symbol + ".")


async def run_proof(
    deps: PipelineDeps,
    target: ValidatedTarget | LocalFixtureTarget,
    budget: ScanBudget,
    report: ScanReport,
    finding: Finding,
    entry: str,
) -> Proof:
    """Prove (or not) that the finding's call path is reached, in the Sandbox.

    Fetches the pinned commit again (image A), indexes it to find the call the repository makes at
    the path's last step, installs the resolved pins from wheels only (image P; a pin without a
    wheel is downloaded there and built offline into image P'), then runs the entry with that
    call stubbed. Never raises for a Sandbox, timeout or spend problem: each ends as a
    `not_proven` Proof with its reason, so the scan, the job and the verdict are unaffected.
    """
    last = (finding.call_path or [None])[-1]
    stubbed = last.symbol if last is not None else ""

    def result(status: Literal["proven", "not_proven"], reason: str, call: str | None) -> Proof:
        return Proof(
            status=status,
            reason=reason,
            ran_at=deps.clock.now(),
            entry=entry,
            target=stubbed,
            recorded_call=call,
        )

    def not_proven(reason: str) -> Proof:
        return result("not_proven", reason, None)

    if last is None or finding.vulnerable_symbol is None:
        return not_proven("the finding has no call path to prove")
    symbols = finding.vulnerable_symbol.symbols
    tops = sorted({s.split(".")[0] for s in symbols if _IDENT_RE.fullmatch(s.split(".")[0])})
    workspace = None
    try:
        workspace = await deps.agent.open_workspace(target, budget)
        index = await deps.agent.index(workspace, tops)
        site = next(
            (t for t in index.target_sites if (t.file, t.line) == (last.file, last.line)), None
        )
        if site is None:
            return not_proven("the vulnerable call is not in the index of the pinned commit")
        stubbed = site.target
        implied = implied_symbols(site, finding.package, finding.version)
        by_default = bool(implied & set(symbols)) and not any(
            _module_matches(site.target, symbol) for symbol in symbols
        )
        pins = [
            (d.name, d.version) for d in report.dependencies if d.resolved and d.version is not None
        ]
        installed = await deps.agent.proof_install(workspace, pins)
        if installed.unfetched:
            missing = ", ".join(u.spec for u in installed.unfetched)
            return not_proven(
                f"uncertain: no wheel for {missing}; its source package could not be downloaded"
            )
        if installed.sdists:  # a source package is built offline; proof-run starts from the result
            installed = await deps.agent.proof_build(workspace, installed)
            if installed.failed:
                names = ", ".join(f.spec.replace("==", " ") for f in installed.failed)
                return not_proven(f"uncertain: could not build {names} from source")
        run = await deps.agent.proof_run(
            workspace,
            installed,
            entry=entry,
            target=stubbed,
            site=f"{site.file}:{site.line}" if by_default else None,
        )
        return result(run.status, run.reason, run.recorded_call)
    except SandboxTimeout:
        return not_proven(PROOF_SANDBOX_TIMEOUT)
    except ExecutorSpendDenied:
        return not_proven("not run: the spend limit was reached")
    except SandboxFailed as exc:
        logger.warning("proof failed in %s: %s", exc.step, exc.detail)
        return not_proven(f"the Sandbox could not complete the proof ({exc.step})")
    except Exception as exc:  # a proof never fails the job: log the type and report it
        logger.error("proof raised %s", type(exc).__name__, exc_info=True)
        return not_proven("the proof could not be completed")
    finally:
        if workspace is not None:
            await deps.agent.close(workspace)
