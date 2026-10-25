"""The scan pipeline (ARCHITECTURE sections 5 and 10): six steps, one report that always completes.

Steps run in order: resolve, osv, research, analysis, reasoning, report. Each step has a state, the
tools it used (section 6.2 rule) and a `stage_end` log line. A failure of one external call makes
only the affected findings `uncertain` with a reason; a step that throws is marked failed with an
error id and the report is built from what exists (NFR-06). The scan deadline (`SCAN_TIMEOUT_S`)
cancels the work in flight; findings still waiting get `scan_timeout`. The caller owns the spend
reservation: it passes a `ScanBudget` and settles it afterwards.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from langsmith.run_trees import RunTree

from pir import __version__
from pir.agent_client import (
    LocalFixtureTarget,
    ResolvedDependency,
    ResolveResult,
    SandboxFailed,
    SandboxTimeout,
    Workspace,
)
from pir.deps import PipelineDeps
from pir.executor.base import ExecutorSpendDenied
from pir.github import RepoCoordinates, ValidatedTarget
from pir.logs import (
    bind_scan,
    redact,
    register_secrets,
    scan_id_var,
    secrets_from_settings,
    stage_timer,
)
from pir.models import Outcome, ReasonCode, StepName, StepState
from pir.models.api import ProgressEvent
from pir.models.index import AnalysisIndex
from pir.models.reasoning import ReasoningOutcome
from pir.models.report import (
    Advisory,
    Dependency,
    Finding,
    RepoRef,
    Resolution,
    ScanReport,
    StepStatus,
)
from pir.models.research import AdvisoryResearch
from pir.osv import OsvFailure
from pir.reasoning.ast_tool import AstTool
from pir.reasoning.strategy import FindingContext
from pir.report_builder import Candidate, ScanContext, build_finding, build_report, step_tools
from pir.spend import ScanBudget, SpendDenied
from pir.tracing import run_traced

logger = logging.getLogger("pir.pipeline")

STEP_ORDER: tuple[StepName, ...] = ("resolve", "osv", "research", "analysis", "reasoning", "report")
_SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "unknown": 4}
_DENIAL_TEXT = {
    "spend_cap_reached": "spend cap reached",
    "scan_budget_reached": "scan budget reached",
}
_MAX_MESSAGE = 300
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_LOCAL_FIXTURE_LIMITATION = "local fixture, not fetched from GitHub"

_SCAN_LIMITATIONS = [
    "Reachability is judged by static analysis of the repository's own code (imports, calls and "
    "references found with ast); dynamic imports, reflection and runtime configuration are not "
    "examined.",
    "Only dependency manifests at the repository root are examined.",
]

Forced = tuple[ReasonCode, str]


class EventSink(Protocol):
    async def emit(self, event: ProgressEvent) -> None: ...


class _ScanTimeout(TimeoutError):
    """The scan deadline passed while a step was running."""


def github_repo_ref(target: ValidatedTarget) -> RepoRef:
    coords = target.coords
    return RepoRef(
        url=f"https://github.com/{coords.owner}/{coords.name}",
        owner=coords.owner,
        name=coords.name,
        ref_requested=coords.ref,
        default_branch=target.default_branch,
        commit_sha=target.commit_sha,
    )


def local_repo_ref(target: LocalFixtureTarget, coords: RepoCoordinates | None = None) -> RepoRef:
    """The `RepoRef` of a local fixture: the coordinates given, else `local/<directory name>`."""
    if coords is None:
        name = _NAME_CHARS.sub("-", Path(target.path).name)[:100]
        coords = RepoCoordinates(
            owner="local", name=name if name not in ("", ".", "..") else "fixture"
        )
    return RepoRef(
        url=f"https://github.com/{coords.owner}/{coords.name}",
        owner=coords.owner,
        name=coords.name,
        ref_requested=coords.ref,
        default_branch="local",
        commit_sha=target.commit_sha,
    )


def _text(value: object, limit: int = _MAX_MESSAGE) -> str:
    return redact(" ".join(str(value).split()))[:limit]


@dataclass
class _StepRec:
    name: StepName
    state: StepState = "pending"
    tools: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str | None = None

    def status(self) -> StepStatus:
        state: StepState = self.state if self.state in ("done", "failed") else "skipped"
        used = [] if state == "skipped" else step_tools(self.name, self.tools)
        return StepStatus(
            name=self.name,
            state=state,
            tools=used,  # type: ignore[arg-type]
            started_at=self.started_at,
            finished_at=self.finished_at,
            message=self.message,
        )


@dataclass
class _Item:
    """One candidate finding and what the steps learned about it."""

    candidate: Candidate
    research: AdvisoryResearch | None = None
    forced: Forced | None = None
    outcome: ReasoningOutcome | None = None
    seen: frozenset[tuple[str, int]] = frozenset()

    @property
    def advisory(self) -> Advisory | None:
        return self.candidate.advisory

    def identified(self) -> bool:
        return self.research is not None and self.research.identified

    def severity_key(self) -> tuple[int, str, str]:
        adv = self.candidate.advisory
        severity = adv.severity if adv else "unknown"
        return (_SEVERITY_ORDER[severity], self.candidate.package, adv.id if adv else "")


def _sandbox_failure(exc: Exception) -> tuple[ReasonCode, str, bool]:
    """(reason code, plain text, whether a Sandbox operation returned) for a Sandbox exception."""
    if isinstance(exc, SandboxTimeout):
        return "sandbox_timeout", "sandbox timeout", True
    if isinstance(exc, ExecutorSpendDenied):
        return exc.denied.reason, _DENIAL_TEXT[exc.denied.reason], False
    if isinstance(exc, SandboxFailed):
        if exc.step == "selfcheck":
            return "sandbox_failed", "sandbox unavailable: base image check failed", True
        returned = not exc.detail.startswith("sandbox unavailable")
        return "sandbox_failed", _text(f"sandbox failed in {exc.step}: {exc.detail}"), returned
    raise TypeError("not a Sandbox exception")


_SANDBOX_ERRORS = (SandboxTimeout, SandboxFailed, ExecutorSpendDenied)


class _Scan:
    def __init__(
        self,
        deps: PipelineDeps,
        target: ValidatedTarget | LocalFixtureTarget,
        repo: RepoRef,
        budget: ScanBudget,
        scan_id: str,
        sink: EventSink | None,
    ) -> None:
        self.deps = deps
        self.settings = deps.settings
        self.target = target
        self.repo = repo
        self.budget = budget
        self.scan_id = scan_id
        self.sink = sink
        self.seq = 0
        self.steps = {name: _StepRec(name) for name in STEP_ORDER}
        self.deadline = 0.0
        self.timed_out = False
        self.ws: Workspace | None = None
        self.resolution: Resolution | None = None
        self.resolved: list[ResolvedDependency] = []
        self.osv_status: dict[str, str | None] = {}  # "name==version" -> None (checked) or error
        self.items: list[_Item] = []
        self.index: AnalysisIndex | None = None
        self.sandbox_kind = deps.executor.kind == "sandbox"

    # ---- clock, events -----------------------------------------------------------------

    def _now(self) -> datetime:
        return self.deps.clock.now()

    async def _emit(self, event: ProgressEvent) -> None:
        if self.sink is None:
            return
        try:
            await self.sink.emit(event)
        except Exception as exc:  # a broken sink must not fail the scan
            logger.warning("event sink failed: %s", type(exc).__name__)

    async def _emit_step(self, rec: _StepRec) -> None:
        self.seq += 1
        terminal = rec.state in ("done", "failed", "skipped")
        tools = step_tools(rec.name, rec.tools) if terminal and rec.state != "skipped" else []
        await self._emit(
            ProgressEvent(
                seq=self.seq,
                scan_id=self.scan_id,
                type="step",
                step=rec.name,
                state=rec.state,
                message=rec.message,
                at=self._now(),
                outcome=None,
                tools=tools,  # type: ignore[arg-type]
            )
        )

    async def _start(self, rec: _StepRec) -> None:
        rec.state = "running"
        rec.started_at = self._now()
        await self._emit_step(rec)

    async def _finish(self, rec: _StepRec) -> None:
        rec.finished_at = self._now()
        if rec.message is not None:
            rec.message = rec.message[:_MAX_MESSAGE]
        await self._emit_step(rec)

    def _skip(self, rec: _StepRec, message: str) -> None:
        rec.state = "skipped"
        rec.message = message
        rec.tools = []

    async def _skipped_stage(self, rec: _StepRec, message: str) -> None:
        async with stage_timer(rec.name) as timer:
            timer.outcome = "skipped"
            await self._start(rec)
            self._skip(rec, message)
        await self._finish(rec)

    # ---- running a step ----------------------------------------------------------------

    async def _run_step(self, name: StepName, body: Callable[[_StepRec], Awaitable[None]]) -> None:
        rec = self.steps[name]
        await self._start(rec)
        try:
            async with stage_timer(name) as timer:
                cm = asyncio.timeout_at(self.deadline)
                try:
                    async with cm:
                        await body(rec)
                except TimeoutError as exc:
                    if cm.expired():
                        raise _ScanTimeout from exc
                    raise
                if rec.state in ("skipped", "failed"):
                    timer.outcome = rec.state
        except _ScanTimeout:
            self.timed_out = True
            rec.state = "failed"
            rec.message = "scan timeout"
            logger.warning("scan deadline reached during %s", name)
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]
            logger.error(
                "unexpected error in step %s: %s",
                name,
                type(exc).__name__,
                exc_info=True,
                extra={"error_id": error_id, "stage": name},
            )
            rec.state = "failed"
            rec.message = f"Unexpected error (error id {error_id})"
        if rec.state == "running":
            rec.state = "done"
        await self._finish(rec)

    # ---- the whole scan ----------------------------------------------------------------

    async def run(self) -> ScanReport:
        self.deadline = asyncio.get_running_loop().time() + self.settings.scan_timeout_s
        bodies: dict[StepName, Callable[[_StepRec], Awaitable[None]]] = {
            "resolve": self._resolve,
            "osv": self._osv,
            "research": self._research,
            "analysis": self._analysis,
            "reasoning": self._reasoning,
        }
        try:
            for name in STEP_ORDER[:-1]:
                if self.timed_out:
                    await self._skipped_stage(self.steps[name], "scan timeout")
                else:
                    await self._run_step(name, bodies[name])
            return await self._report()
        finally:
            if self.ws is not None:
                await self.deps.agent.close(self.ws)

    # ---- resolve -----------------------------------------------------------------------

    async def _resolve(self, rec: _StepRec) -> None:
        agent = self.deps.agent
        used: set[str] = set()
        try:
            self.ws = await agent.open_workspace(self.target, self.budget)
            used.add("sandbox")
            result = await agent.resolve(self.ws)
        except _SANDBOX_ERRORS as exc:
            _, text, returned = _sandbox_failure(exc)
            if returned:
                used.add("sandbox")
            self.resolution = Resolution(
                method="failed",
                python_version=None,
                lockfile=None,
                compiled_from=None,
                notes=[text],
            )
            rec.state, rec.message = "failed", text
            rec.tools = self._sandbox_tools(used)
            return
        self.resolved = list(result.dependencies)
        self.resolution = _resolution(result)
        used.add({"lockfile": "lockfile", "compiled": "uv"}.get(result.method, ""))
        rec.tools = self._sandbox_tools(used)
        if result.method == "failed":
            rec.state = "failed"
            rec.message = _text(
                result.notes[0] if result.notes else "dependencies were not resolved"
            )

    def _sandbox_tools(self, used: set[str]) -> list[str]:
        """The step-tools rule: `sandbox` only when a real Sandbox operation returned."""
        return [tool for tool in used if tool and (tool != "sandbox" or self.sandbox_kind)]

    # ---- osv ---------------------------------------------------------------------------

    def _provisional(self) -> list[Dependency]:
        return [_dependency(dep, None) for dep in self.resolved]

    async def _osv(self, rec: _StepRec) -> None:
        queryable = [d for d in self.resolved if d.resolved]
        if not queryable:
            self._skip(rec, "no resolved dependencies to check")
            return
        osv = self.deps.osv
        results = await osv.query(self._provisional())
        rec.tools = ["osv"]
        by_key = {f"{d.name}=={d.version}": d for d in queryable}
        failed = 0
        for key, dep in by_key.items():
            found = results.get(key)
            if found is None:
                found = OsvFailure("OSV returned no result for this dependency")
            if isinstance(found, OsvFailure):
                self._osv_failed(dep, found.message)
                failed += 1
                continue
            advisories = await asyncio.gather(*(osv.get_advisory(i) for i in found))
            bad = next((a for a in advisories if isinstance(a, OsvFailure)), None)
            if bad is not None:
                self._osv_failed(dep, bad.message)
                failed += 1
                continue
            self.osv_status[key] = None
            for adv in advisories:
                assert isinstance(adv, Advisory)  # OsvFailure handled above
                cand = Candidate(
                    package=dep.name,
                    version=dep.version or "",
                    advisory=adv,
                    advisory_links=osv.links(adv.id),
                    direct=dep.direct,
                )
                self.items.append(_Item(cand))
        if failed == len(by_key):
            rec.state = "failed"
            rec.message = "OSV could not be consulted; no dependency was checked"
        elif failed:
            rec.message = f"OSV failed for {failed} of {len(by_key)} dependencies"

    def _osv_failed(self, dep: ResolvedDependency, message: str) -> None:
        self.osv_status[f"{dep.name}=={dep.version}"] = _text(message)
        self.items.append(_Item(Candidate(dep.name, dep.version or "", None, direct=dep.direct)))

    # ---- research ----------------------------------------------------------------------

    def _denial(self, pool: str, estimate_usd: float) -> Forced | None:
        """Read the budget: would a small paid call still be allowed? (hand-off from the
        researcher, which reports a spend denial as tavily_failed or nebius_failed.)"""
        decision = self.budget.try_spend(pool, estimate_usd)  # type: ignore[arg-type]
        if isinstance(decision, SpendDenied):
            return decision.reason, _DENIAL_TEXT[decision.reason]
        self.budget.record(decision, 0.0)
        return None

    def _spend_reason(self, failure: ReasonCode | None) -> Forced | None:
        s = self.settings
        if failure == "tavily_failed":
            credits = 1 if s.tavily_search_depth == "basic" else 2
            return self._denial("tavily", credits * s.tavily_price_per_credit_usd)
        if failure == "nebius_failed":
            return self._denial(
                "nebius", s.max_completion_tokens * s.nebius_price_output_per_mtok / 1_000_000
            )
        return None

    async def _research(self, rec: _StepRec) -> None:
        by_advisory: dict[str, list[_Item]] = {}
        for item in self.items:
            if item.advisory is not None:
                by_advisory.setdefault(item.advisory.id, []).append(item)
        if not by_advisory:
            self._skip(rec, "no advisories to research")
            return
        gate = asyncio.Semaphore(max(1, self.settings.finding_concurrency))
        results: list[AdvisoryResearch] = []

        async def one(items: list[_Item]) -> None:
            first = items[0]
            assert first.advisory is not None
            async with gate:
                try:
                    research = await self.deps.researcher.research(
                        first.advisory, first.candidate.package, first.candidate.advisory_links,
                        self.budget,
                    )  # fmt: skip
                except Exception as exc:  # research never raises; isolate the item if it does
                    logger.error("research raised %s", type(exc).__name__, exc_info=True)
                    return
            results.append(research)
            forced = self._spend_reason(research.failure)
            for item in items:
                item.research = research
                item.forced = forced

        await asyncio.gather(*(one(items) for items in by_advisory.values()))
        used = set()
        if any(r.failure != "tavily_failed" for r in results):
            used.add("tavily")
        if any(r.failure is None for r in results):
            used.add("nemotron")
        rec.tools = list(used)

    # ---- analysis ----------------------------------------------------------------------

    def _reasonable(self) -> list[_Item]:
        return [
            i for i in self.items if i.advisory is not None and i.identified() and i.forced is None
        ]

    async def _analysis(self, rec: _StepRec) -> None:
        pending = self._reasonable()
        targets = sorted(
            {
                symbol.split(".")[0]
                for item in pending
                if item.research is not None
                for symbol in item.research.symbols
                if _IDENT_RE.fullmatch(symbol.split(".")[0])
            }
        )
        if not targets or self.ws is None:
            self._skip(rec, "no vulnerable symbol to look for")
            return
        try:
            self.index = await self.deps.agent.index(self.ws, targets)
        except _SANDBOX_ERRORS as exc:
            code, text, returned = _sandbox_failure(exc)
            for item in pending:
                item.forced = (code, text)
            rec.state, rec.message = "failed", text
            rec.tools = self._sandbox_tools({"sandbox"} if returned else set())
            return
        rec.tools = self._sandbox_tools({"sandbox", "ast"})

    # ---- reasoning ---------------------------------------------------------------------

    async def _reasoning(self, rec: _StepRec) -> None:
        index = self.index
        pending = sorted(self._reasonable(), key=_Item.severity_key)
        if index is None or not pending:
            self._skip(rec, "nothing to reason about")
            return
        limit = self.settings.max_findings_reasoned
        for item in pending[limit:]:
            item.forced = (
                "finding_limit_reached",
                f"finding limit reached: only the {limit} most severe findings are examined",
            )
        gate = asyncio.Semaphore(max(1, self.settings.finding_concurrency))

        async def one(item: _Item) -> None:
            research, advisory = item.research, item.advisory
            assert research is not None and advisory is not None
            tops = sorted({s.split(".")[0] for s in research.symbols})
            ctx = FindingContext(
                package=item.candidate.package,
                version=item.candidate.version,
                advisory=advisory,
                symbols=list(research.symbols),
                target_modules=tops,
                direct=item.candidate.direct,
            )
            tool = AstTool(index)
            async with gate:
                try:
                    item.outcome = await self.deps.strategy.run(
                        ctx, tool, self.deps.llm, self.budget
                    )
                except Exception as exc:  # the strategy never raises; isolate the item if it does
                    logger.error("reasoning raised %s", type(exc).__name__, exc_info=True)
                    return
            item.seen = frozenset(tool.seen_locations)

        await asyncio.gather(*(one(item) for item in pending[:limit]))
        rec.tools = ["nemotron", "ast"]

    # ---- report ------------------------------------------------------------------------

    def _dependencies(self) -> list[Dependency]:
        out: list[Dependency] = []
        for dep in self.resolved:
            if not dep.resolved:
                out.append(_dependency(dep, None))
                continue
            key = f"{dep.name}=={dep.version}"
            if key in self.osv_status:
                error = self.osv_status[key]
            else:
                error = (
                    "OSV was not queried: the scan stopped early"
                    if self.timed_out
                    else "OSV query did not complete"
                )
                self.items.append(
                    _Item(
                        Candidate(dep.name, dep.version or "", None, direct=dep.direct),
                        forced=("scan_timeout", "scan timeout") if self.timed_out else None,
                    )
                )
            out.append(_dependency(dep, error, queried=True))
        return out

    def _finding(self, item: _Item) -> Finding:
        forced = item.forced
        needs_more = (
            item.advisory is not None
            and forced is None
            and (item.research is None or (item.identified() and item.outcome is None))
        )
        if self.timed_out and needs_more:
            forced = ("scan_timeout", "scan timeout")
        return build_finding(
            item.candidate,
            item.research,
            item.outcome,
            self.index,
            forced,
            seen_locations=item.seen,
        )

    async def _report(self) -> ScanReport:
        rec = self.steps["report"]
        await self._start(rec)
        async with stage_timer("report"):
            dependencies = self._dependencies()
            seen_ids: set[str] = set()
            findings: list[Finding] = []
            for item in self.items:
                finding = self._finding(item)
                if finding.finding_id not in seen_ids:
                    seen_ids.add(finding.finding_id)
                    findings.append(finding)
            rec.state = "done"
            rec.tools = ["verdict_gate"]
            rec.finished_at = self._now()
            for other in self.steps.values():
                if other.state == "pending":
                    other.state = "skipped"
            report = build_report(self._context(dependencies), findings)
        await self._emit_step(rec)
        await self._emit_done(report.outcome)
        return report

    def _context(self, dependencies: list[Dependency]) -> ScanContext:
        resolution = self.resolution or Resolution(
            method="failed",
            python_version=None,
            lockfile=None,
            compiled_from=None,
            notes=["The scan stopped before dependencies were resolved."],
        )
        limitations = list(_SCAN_LIMITATIONS)
        if isinstance(self.target, LocalFixtureTarget):
            limitations.append(_LOCAL_FIXTURE_LIMITATION)
        unresolved = sum(not d.resolved for d in dependencies)
        if unresolved:
            limitations.append(
                f"{unresolved} dependencies could not be resolved and were not checked."
            )
        return ScanContext(
            scan_id=self.scan_id,
            tool_version=__version__,
            model=self.deps.llm.model,
            generated_at=self._now(),
            repo=self.repo,
            resolution=resolution,
            steps=[self.steps[name].status() for name in STEP_ORDER],
            dependencies=dependencies,
            limitations=limitations,
        )

    async def _emit_done(self, outcome: Outcome) -> None:
        self.seq += 1
        await self._emit(
            ProgressEvent(
                seq=self.seq,
                scan_id=self.scan_id,
                type="done",
                step=None,
                state=None,
                message=None,
                at=self._now(),
                outcome=outcome,
                tools=[],
            )
        )


def _resolution(result: ResolveResult) -> Resolution:
    return Resolution(
        method=result.method,
        python_version=result.python_version,
        lockfile=result.lockfile,
        compiled_from=result.compiled_from,
        notes=list(result.notes),
    )


def _dependency(
    dep: ResolvedDependency, osv_error: str | None, *, queried: bool = False
) -> Dependency:
    """The report's `Dependency`: unresolved ones are never queried (AC-09)."""
    if not dep.resolved:
        status = "not_queried"
        osv_error = None
    elif not queried:
        status = "checked"  # provisional, for the OSV query; the report gets the real status
    else:
        status = "checked" if osv_error is None else "not_checked"
    return Dependency(
        name=dep.name,
        source=dep.source,
        groups=list(dep.groups),
        version=dep.version,
        resolved=dep.resolved,
        unresolved_reason=dep.unresolved_reason,
        direct=dep.direct,
        osv_status=status,  # type: ignore[arg-type]
        osv_error=osv_error,
    )


async def run_scan(
    deps: PipelineDeps,
    target: ValidatedTarget | LocalFixtureTarget,
    budget: ScanBudget,
    *,
    repo: RepoRef | None = None,
    scan_id: str | None = None,
    sink: EventSink | None = None,
    trace_parent: RunTree | None = None,
) -> ScanReport:
    """Run one scan and return its report. Never raises for an external failure.

    `budget` is the scan's reservation (the caller settles it). `repo` defaults to the target's.
    `trace_parent` nests the scan's trace under that run (the evaluation runner's case trace).
    Raises only if the final report cannot be built (a bug); the caller reports an error id.
    """
    scan_id = scan_id or budget.scan_id
    if repo is None:
        repo = (
            github_repo_ref(target)
            if isinstance(target, ValidatedTarget)
            else local_repo_ref(target)
        )
    register_secrets(secrets_from_settings(deps.settings))
    token = bind_scan(scan_id)
    try:
        scan = _Scan(deps, target, repo, budget, scan_id, sink)
        return await run_traced(
            deps.settings, scan_id, scan.run, client=deps.langsmith, parent=trace_parent
        )
    finally:
        scan_id_var.reset(token)
