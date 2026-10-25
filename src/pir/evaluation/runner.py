"""Evaluation runner (ARCHITECTURE section 15, ADR-0017; AC-33 to AC-39).

Each `[[repo]]` is scanned once through the real pipeline (`run_scan`); every `[[case]]` of that
repo is then scored against its finding. OSV and Tavily answers come from `eval/recordings/` unless
`--refresh-research` re-records them. Nemotron and the Sandbox run live in a scored run. A case
whose repo errors is recorded as errored and scored as `uncertain`; the other repos still run.

LangSmith (AC-37): the dataset `LANGSMITH_DATASET` gets one example per case; each run is one
experiment (`eval-<UTC timestamp>-<model>`) with one root trace `pir.eval_case` per case and the
feedback `correct`; metrics go into the experiment metadata; the traces are read back with
`client.runs.query()` (selecting `name`, or the API returns only ids) and polled for a few seconds
until every case trace is visible. A short read-back makes the run unscored.

Trace tree: a repo is scanned once, so the scan's trace (`pir.scan` with its step, LLM and tool
runs) is nested under the root trace of the repo's first case. Every case root has a child run
`pir.eval_score`; for the other cases of a repo it names the root trace that holds the scan.
"""

import asyncio
import hashlib
import json
import logging
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from langsmith import Client
from langsmith.run_trees import RunTree
from pydantic import ValidationError

from pir.agent_client import LocalFixtureTarget
from pir.config import ConfigError, Settings, require, required_for
from pir.deps import PipelineDeps, build_deps
from pir.evaluation.cases import CasesError, EvalSuite, load_cases
from pir.evaluation.metrics import PRECISION_BAR, EvalMetrics, bar_met, compute
from pir.github import ValidatedTarget, parse_repo_url
from pir.logs import bind_scan, scan_id_var
from pir.models import Verdict
from pir.models.eval import EvalCase, EvalRepo, EvalRunSummary
from pir.models.report import ScanReport
from pir.osv import OsvClient
from pir.pipeline import github_repo_ref, local_repo_ref, run_scan
from pir.research import AdvisoryResearcher, TavilySearchClient
from pir.spend import Refused, SystemClock
from pir.tracing import make_client

logger = logging.getLogger("pir.evaluation")

CASE_RUN_NAME = "pir.eval_case"
SCORE_RUN_NAME = "pir.eval_score"
READBACK_TIMEOUT_S = 10.0
READBACK_INTERVAL_S = 1.0
SELFCHECK_FAILED_TEXT = "base image check failed"  # ADR-0033: every Sandbox case would fail
EXIT_MET, EXIT_NOT_MET, EXIT_COULD_NOT_RUN = 0, 1, 2
RECORDING_HEADER = "x-pir-recording"

CaseStatus = Literal["scored", "errored", "missing"]


class EvalCouldNotRun(Exception):
    """The run could not start (setup, recordings or LangSmith). The message is for the user."""


# ---- recorded OSV and Tavily answers ------------------------------------------------------


class RecordReplayTransport(httpx.AsyncBaseTransport):
    """Replays recorded responses from `directory`, or (record mode) forwards and records them.

    A recording holds the response only: status, content type and body, never a request header.
    Only HTTP 200 responses are recorded. In replay mode a request with no recording gets a 404
    (so the client reports an ordinary failure) and its key is added to `misses`.
    """

    def __init__(
        self,
        directory: Path,
        *,
        record: bool = False,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.directory = directory
        self.record = record
        self._inner = (
            inner if inner is not None else (httpx.AsyncHTTPTransport() if record else None)
        )
        self.hits = 0
        self.recorded = 0
        self.misses: list[str] = []

    @staticmethod
    def key(method: str, url: str, body: bytes) -> str:
        try:
            normalized = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":"))
        except ValueError:
            normalized = body.decode("utf-8", errors="replace")
        digest = hashlib.sha256(f"{method} {url}\n{normalized}".encode()).hexdigest()
        return f"{method.lower()}-{digest[:20]}"

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        key = self.key(request.method, str(request.url), body)
        path = self._path(key)
        if not self.record:
            return self._replay(key, path, request)
        assert self._inner is not None  # set whenever record is true
        response = await self._inner.handle_async_request(request)
        try:
            await response.aread()
        finally:
            await response.aclose()
        content_type = response.headers.get("content-type", "application/json")
        if response.status_code == 200:
            text = response.content.decode("utf-8")
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "method": request.method,
                "url": str(request.url),
                "status": 200,
                "content_type": content_type,
                "body": text,
            }
            _write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            self.recorded += 1
        return httpx.Response(
            response.status_code, headers={"content-type": content_type}, content=response.content
        )

    def _replay(self, key: str, path: Path, request: httpx.Request) -> httpx.Response:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            content = str(payload["body"]).encode("utf-8")
            headers = {"content-type": str(payload["content_type"])}
            status = int(payload["status"])
        except FileNotFoundError:
            self.misses.append(key)
            logger.warning(
                "recording missing",
                extra={"event": "recording_missing", "service": request.url.host},
            )
            return httpx.Response(
                404,
                headers={RECORDING_HEADER: "missing", "content-type": "application/json"},
                content=b'{"error":"no recording for this request"}',
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.misses.append(key)
            logger.warning("recording unreadable: %s", type(exc).__name__)
            return httpx.Response(500, content=b"")
        self.hits += 1
        return httpx.Response(status, headers=headers, content=content)


# ---- scoring -------------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: EvalCase
    predicted: Verdict | None
    status: CaseStatus
    detail: str | None
    scan_id: str | None
    started: datetime
    ended: datetime

    @property
    def scored_as(self) -> Verdict:
        return self.predicted if self.predicted is not None else "uncertain"

    @property
    def correct(self) -> bool:
        return self.scored_as == self.case.label


@dataclass
class RepoScan:
    report: ScanReport | None
    error: str | None
    scan_id: str
    started: datetime
    ended: datetime


def normalize_package(name: str) -> str:
    out, previous_dash = [], False
    for char in name.lower():
        dash = char in "-_."
        if not (dash and previous_dash):
            out.append("-" if dash else char)
        previous_dash = dash
    return "".join(out)


def score_case(case: EvalCase, scan: RepoScan) -> CaseResult:
    """The case's verdict from its repo's report; an error or a missing finding is `uncertain`."""
    base = {"case": case, "scan_id": scan.scan_id, "started": scan.started, "ended": scan.ended}
    if scan.report is None:
        return CaseResult(predicted=None, status="errored", detail=scan.error, **base)
    package = normalize_package(case.package)
    for finding in scan.report.findings:
        advisory = finding.advisory
        if advisory is None or normalize_package(finding.package) != package:
            continue
        if case.advisory == advisory.id or case.advisory in advisory.aliases:
            return CaseResult(predicted=finding.verdict, status="scored", detail=None, **base)
    return CaseResult(
        predicted=None,
        status="missing",
        detail=f"no finding for {case.package} / {case.advisory} in the report",
        **base,
    )


def _local_commit_sha(path: Path) -> str:
    from pir.cli import local_commit_sha  # cli imports this module lazily; no cycle at import time

    return local_commit_sha(path)


async def _scan_repo(
    deps: PipelineDeps, suite: EvalSuite, repo: EvalRepo, trace_parent: RunTree | None = None
) -> RepoScan:
    """Scan one repo; any failure becomes `RepoScan.error`, never an exception.

    `trace_parent` is the run the scan's trace is nested under (the first case's root trace).
    """
    scan_id = uuid.uuid4().hex
    started = deps.clock.now()
    report: ScanReport | None = None
    error: str | None = None
    token = bind_scan(scan_id)
    try:
        try:
            target: ValidatedTarget | LocalFixtureTarget
            if repo.source.startswith("local:"):
                path = suite.local_path(repo).resolve()
                target = LocalFixtureTarget(path=str(path), commit_sha=_local_commit_sha(path))
                repo_ref = local_repo_ref(target)
            else:
                location, _, sha = repo.source.removeprefix("github:").partition("@")
                coords = parse_repo_url(f"https://github.com/{location}")
                target = await deps.validate(coords, sha)
                if target.commit_sha != sha:
                    raise ValueError("GitHub returned a different commit than the pinned one")
                repo_ref = github_repo_ref(target)
            budget = await deps.meter.reserve(scan_id)
            if isinstance(budget, Refused):
                error = f"spend admission refused: {budget.reason}"
            else:
                try:
                    report = await run_scan(
                        deps,
                        target,
                        budget,
                        repo=repo_ref,
                        scan_id=scan_id,
                        trace_parent=trace_parent,
                    )
                finally:
                    await deps.meter.settle(budget)
        except Exception as exc:  # per-repo isolation (AC-38): record it and run the others
            logger.exception("eval repo failed", extra={"event": "eval_repo_failed"})
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            report = None
    finally:
        scan_id_var.reset(token)
    return RepoScan(report, error, scan_id, started, deps.clock.now())


# ---- LangSmith -----------------------------------------------------------------------------


class ExperimentPublisher:
    """Dataset upsert, one experiment, one root trace per case with feedback `correct`."""

    def __init__(
        self,
        client: Client,
        dataset_name: str,
        experiment: str,
        model: str,
        bar: float,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        readback_timeout_s: float = READBACK_TIMEOUT_S,
        readback_interval_s: float = READBACK_INTERVAL_S,
    ) -> None:
        self.sleep = sleep
        self.monotonic = monotonic
        self.readback_timeout_s = readback_timeout_s
        self.readback_interval_s = readback_interval_s
        self.client = client
        self.dataset_name = dataset_name
        self.experiment = experiment
        self.model = model
        self.bar = bar
        self.dataset_id: Any = None
        self.project_id: Any = None
        self.example_ids: dict[str, Any] = {}
        self.case_runs: dict[str, uuid.UUID] = {}

    @staticmethod
    def _example(suite: EvalSuite, case: EvalCase) -> dict[str, Any]:
        return {
            "inputs": {
                "repo": suite.repo(case.repo).source,
                "package": case.package,
                "advisory": case.advisory,
            },
            "outputs": {"label": case.label},
            "metadata": {"case_id": case.id},
        }

    def _read_examples(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for example in self.client.list_examples(dataset_id=self.dataset_id):
            case_id = (example.metadata or {}).get("case_id")
            if case_id:
                found[case_id] = example
        return found

    def start(self, suite: EvalSuite) -> None:
        """Upsert the dataset (one example per case) and create the experiment."""
        client = self.client
        if client.has_dataset(dataset_name=self.dataset_name):
            dataset = client.read_dataset(dataset_name=self.dataset_name)
        else:
            dataset = client.create_dataset(
                self.dataset_name, description="ProveItReachable labelled reachability cases"
            )
        self.dataset_id = dataset.id
        existing = self._read_examples()
        new: list[dict[str, Any]] = []
        for case in suite.cases:
            wanted = self._example(suite, case)
            current = existing.get(case.id)
            if current is None:
                new.append(wanted)
            elif current.inputs != wanted["inputs"] or current.outputs != wanted["outputs"]:
                client.update_example(
                    current.id, inputs=wanted["inputs"], outputs=wanted["outputs"]
                )
        if new:
            client.create_examples(dataset_id=self.dataset_id, examples=new)
        self.example_ids = {cid: ex.id for cid, ex in self._read_examples().items()}
        absent = [c.id for c in suite.cases if c.id not in self.example_ids]
        if absent:
            raise RuntimeError(f"dataset is missing examples for: {', '.join(absent)}")
        project = client.create_project(
            self.experiment,
            reference_dataset_id=self.dataset_id,
            description="ProveItReachable evaluation run",
            metadata={"model": self.model, "bar": self.bar, "cases": len(suite.cases)},
        )
        self.project_id = project.id

    def open_case(self, case: EvalCase, started: datetime) -> RunTree:
        """Post the root trace of a case; the scan of its repo is nested beneath it."""
        root = RunTree(
            name=CASE_RUN_NAME,
            id=uuid.uuid4(),
            run_type="chain",
            inputs={"case_id": case.id, "package": case.package, "advisory": case.advisory},
            project_name=self.experiment,
            reference_example_id=self.example_ids[case.id],
            start_time=started,
            extra={"metadata": {"case_id": case.id}},
            client=self.client,
        )
        root.post()
        return root

    def record_case(
        self, result: CaseResult, root: RunTree | None = None, scan_root: RunTree | None = None
    ) -> None:
        """Finish the case's root trace (opened here when `root` is None), add its child run
        `pir.eval_score` and the feedback `correct` (1 or 0).

        `scan_root` is the case root that holds the repo's scan when that is not this case.
        """
        case = result.case
        if root is None:
            root = self.open_case(case, result.started)
        scan_holder = scan_root if scan_root is not None else root
        root.add_metadata({"scan_id": result.scan_id, "case_id": case.id})
        score = root.create_child(
            SCORE_RUN_NAME,
            "chain",
            inputs={"case_id": case.id, "label": case.label},
            start_time=result.ended,
            extra={"metadata": {"scan_trace_id": str(scan_holder.id)}},
        )
        score.end(
            outputs={
                "predicted": result.scored_as,
                "correct": result.correct,
                "scan_trace_id": str(scan_holder.id),
            },
            error=result.detail if result.status == "errored" else None,
            end_time=result.ended,
        )
        score.post()
        score.patch()
        root.end(
            outputs={
                "predicted": result.scored_as,
                "label": case.label,
                "status": result.status,
            },
            error=result.detail if result.status == "errored" else None,
            end_time=result.ended,
        )
        root.patch()
        self.case_runs[case.id] = root.id
        self.client.flush()  # the run must exist before its feedback
        self.client.create_feedback(
            root.id,
            "correct",
            score=1 if result.correct else 0,
            comment=f"label={case.label}; predicted={result.scored_as}",
            session_id=self.project_id,  # project_id is rejected alongside a run id
        )

    def finish(self, metrics: EvalMetrics) -> None:
        """Put the run metrics into the experiment metadata and send everything queued."""
        self.client.update_project(
            self.project_id,
            metadata={
                "model": self.model,
                "bar": self.bar,
                "n": metrics.n,
                "precision": metrics.precision,
                "recall": metrics.recall,
                "abstention_rate": metrics.abstention_rate,
                "correct_abstentions": metrics.correct_abstentions,
                "no_path_precision": metrics.no_path_precision,
                "dangerous_misses": metrics.dangerous_misses,
                "errored": len(metrics.errored),
            },
        )
        self.client.flush()

    async def _visible_case_runs(self) -> int:
        """How many of this run's case traces the API lists now (it returns ids only unless
        `selects` is given, so `NAME` is requested)."""
        wanted = {str(run_id) for run_id in self.case_runs.values()}
        seen: set[str] = set()
        async for run in self.client.runs.query(
            project_ids=[str(self.project_id)], is_root=True, selects=["ID", "NAME"]
        ):
            run_id = str(getattr(run, "id", ""))
            if getattr(run, "name", None) == CASE_RUN_NAME and run_id in wanted:
                seen.add(run_id)
        return len(seen)

    async def read_back(self, expected: int) -> int:
        """How many `pir.eval_case` root runs LangSmith returns for the experiment.

        Polls until `expected` are visible or the bounded timeout passes (ingestion is
        asynchronous); returns the last count. A failed query raises.
        """
        deadline = self.monotonic() + self.readback_timeout_s
        while True:
            seen = await self._visible_case_runs()
            if seen >= expected or self.monotonic() >= deadline:
                return seen
            await self.sleep(self.readback_interval_s)


# ---- result rendering and files -------------------------------------------------------------


def limitations(suite: EvalSuite, model: str) -> str:
    n = len(suite.cases)
    synthetic = sum(1 for c in suite.cases if suite.repo(c.repo).source.startswith("local:"))
    share = synthetic / n if n else 0.0
    return (
        f"{n} labelled cases from {len(suite.repos)} repositories; {synthetic} of them "
        f"({share:.0%}) use local synthetic fixtures and {n - synthetic} use pinned real "
        "repositories. Advisory data (OSV and Tavily answers) is recorded and replayed from "
        f"files, not fetched live. Single model ({model}), temperature 0, one run. With this "
        "few cases one case moves precision by several points."
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render_markdown(
    summary: EvalRunSummary,
    metrics: EvalMetrics,
    results: Sequence[CaseResult],
    limitations_text: str,
    unscored: Sequence[str],
    *,
    refreshed: bool,
) -> str:
    verdict = "PASS" if summary.bar_met else "FAIL"
    lines = [
        f"# Evaluation: {verdict} (LR precision {_fmt(summary.precision)}, bar {summary.bar:.2f})",
        "",
        f"- Run date: {summary.run_date.isoformat()}",
        f"- Model: {summary.model}",
        f"- Experiment: {summary.experiment or 'none (no LangSmith)'}",
        f"- Scored run: {'yes' if summary.scored else 'no'}",
        f"- Advisory recordings: {'re-recorded live' if refreshed else 'replayed'}",
    ]
    lines += [f"- Not scored because: {reason}" for reason in unscored]
    lines += [
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| N (cases) | {metrics.n} |",
        f"| LR precision | {_fmt(metrics.precision)} |",
        f"| LR recall | {_fmt(metrics.recall)} |",
        f"| Abstention rate | {_fmt(metrics.abstention_rate)} |",
        f"| Correct abstentions | {metrics.correct_abstentions} |",
        f"| No-path precision | {_fmt(metrics.no_path_precision)} |",
        f"| Dangerous misses | {metrics.dangerous_misses} |",
        f"| Errored cases | {len(metrics.errored)} |",
        "",
        "Confusion matrix (rows: label, columns: predicted)",
        "",
        "| label \\ predicted | likely reachable | no reachable path detected | uncertain |",
        "|---|---|---|---|",
    ]
    for label, row in metrics.confusion.items():
        cells = " | ".join(
            str(row[p]) for p in ("likely reachable", "no reachable path detected", "uncertain")
        )
        lines.append(f"| {label} | {cells} |")
    lines += ["", "| Case | Label | Predicted | Status | Correct |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r.case.id} | {r.case.label} | {r.scored_as} | {r.status} | "
            f"{'yes' if r.correct else 'no'} |"
        )
    errored = [r for r in results if r.status != "scored"]
    if errored:
        lines += ["", "Errored or unmatched cases (scored as uncertain):", ""]
        lines += [f"- {r.case.id}: {r.detail}" for r in errored]
    lines += ["", f"Limitations: {limitations_text}", ""]
    return "\n".join(lines)


def _write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _published_scored(path: Path) -> bool:
    try:
        return EvalRunSummary.model_validate_json(path.read_text(encoding="utf-8")).scored
    except (OSError, ValidationError):
        return False


@dataclass
class EvalRun:
    summary: EvalRunSummary
    metrics: EvalMetrics
    results: list[CaseResult]
    unscored_reasons: list[str]
    aborted: str | None
    markdown: str
    exit_code: int
    readback: int | None = None
    files: list[Path] = field(default_factory=list)


def _write_results(
    results_dir: Path, stamp: str, run: EvalRun, details: dict[str, Any]
) -> list[Path]:
    """`<stamp>.json/.md` always; `latest.*` unless an unscored run would replace a scored one."""
    written = [results_dir / f"{stamp}.json", results_dir / f"{stamp}.md"]
    _write_text(written[0], json.dumps(details, indent=2, sort_keys=True) + "\n")
    _write_text(written[1], run.markdown)
    latest_json = results_dir / "latest.json"
    if run.summary.scored or run.aborted is None and not _published_scored(latest_json):
        _write_text(latest_json, run.summary.model_dump_json(indent=2) + "\n")
        _write_text(results_dir / "latest.md", run.markdown)
        written += [latest_json, results_dir / "latest.md"]
    else:
        logger.info("kept the published latest results; this run is not a scored run")
    return written


# ---- the run -------------------------------------------------------------------------------


def experiment_name(now: datetime, model: str) -> str:
    return f"eval-{now.strftime('%Y%m%dT%H%M%SZ')}-{model.replace('/', '-')}"


async def _abandon_root(
    publisher: "ExperimentPublisher | None", root: RunTree | None, error: str
) -> None:
    """End and patch a posted case root that the run abandoned (not already ended), so LangSmith
    does not show it pending. A failure here is logged: it must not hide the abort."""
    if publisher is None or root is None or root.end_time is not None:
        return

    def end() -> None:
        root.end(error=error)
        root.patch()
        publisher.client.flush()

    try:
        await asyncio.to_thread(end)
    except Exception:
        logger.exception("langsmith publish failed for an aborted case")


async def run_eval(
    suite: EvalSuite,
    deps: PipelineDeps,
    *,
    client: Client | None,
    results_dir: Path,
    experiment: str | None = None,
    recordings: Sequence[RecordReplayTransport] = (),
    refreshed: bool = False,
) -> EvalRun:
    """Scan every repo once, score every case, publish and write the results.

    Raises EvalCouldNotRun when LangSmith setup fails (before any spend).
    """
    settings = deps.settings
    model = settings.nebius_model
    started = deps.clock.now()
    experiment = experiment or experiment_name(started, model)
    # The scans' own traces must land in this experiment, or nowhere when LangSmith is off.
    deps.langsmith = client
    deps.settings = settings.model_copy(
        update={"langsmith_project": experiment, "langsmith_tracing": client is not None}
    )
    reasons: list[str] = []
    if client is None:
        reasons.append("LangSmith is off (--no-langsmith); a scored run needs it")
    if deps.executor.kind == "fake":
        reasons.append("the executor is fake; a scored run uses the real Sandbox")
    publisher: ExperimentPublisher | None = None
    if client is not None:
        publisher = ExperimentPublisher(
            client, settings.langsmith_dataset, experiment, model, PRECISION_BAR
        )
        try:
            await asyncio.to_thread(publisher.start, suite)
        except Exception as exc:
            logger.exception("langsmith setup failed", extra={"event": "eval_langsmith_failed"})
            raise EvalCouldNotRun(f"LangSmith setup failed: {type(exc).__name__}") from exc

    results: list[CaseResult] = []
    aborted: str | None = None
    publish_failures = 0
    for repo in suite.repos:
        cases = [c for c in suite.cases if c.repo == repo.id]
        if not cases:
            continue
        lead_root: RunTree | None = None
        if publisher is not None:
            try:
                lead_root = await asyncio.to_thread(publisher.open_case, cases[0], deps.clock.now())
            except Exception:
                publish_failures += 1
                logger.exception("langsmith publish failed for a case")
        try:
            scan = await _scan_repo(deps, suite, repo, lead_root)
            if scan.report is not None and SELFCHECK_FAILED_TEXT in scan.report.model_dump_json():
                aborted = (
                    f"the Sandbox base image check failed (ADR-0033); stopped at repo {repo.id}"
                )
                await _abandon_root(publisher, lead_root, aborted)
                break
            for position, case in enumerate(cases):
                token = bind_scan(scan.scan_id)
                try:
                    result = score_case(case, scan)
                    logger.info(
                        "eval case scored",
                        extra={
                            "event": "eval_case",
                            "outcome": result.status if result.status != "scored" else "ok",
                        },
                    )
                    results.append(result)
                    if publisher is not None:
                        try:
                            await asyncio.to_thread(
                                publisher.record_case,
                                result,
                                lead_root if position == 0 else None,
                                lead_root if position > 0 else None,
                            )
                        except Exception:
                            publish_failures += 1
                            logger.exception("langsmith publish failed for a case")
                finally:
                    scan_id_var.reset(token)
        except BaseException as exc:  # an aborted run must not leave its case trace pending
            await _abandon_root(publisher, lead_root, f"eval aborted: {type(exc).__name__}")
            raise

    metrics = compute(
        [(r.case.label, r.predicted) for r in results],
        errored=[r.case.id for r in results if r.status != "scored"],
    )
    if aborted:
        reasons.append(aborted)
    if publish_failures:
        reasons.append(f"LangSmith publishing failed for {publish_failures} case(s)")
    misses = sum(len(t.misses) for t in recordings)
    if misses:
        reasons.append(
            f"{misses} recorded OSV or Tavily request(s) missing; run with --refresh-research"
        )
    if not results or len(metrics.errored) == len(results):
        reasons.append("no case produced a verdict")

    readback: int | None = None
    if publisher is not None and not aborted:
        try:
            await asyncio.to_thread(publisher.finish, metrics)
            readback = await publisher.read_back(len(results))
        except Exception as exc:  # the traces cannot be shown to exist: the run is unscored
            logger.warning("langsmith finish or read-back failed: %s", type(exc).__name__)
            reasons.append(f"LangSmith finish or read-back failed ({type(exc).__name__})")
        if readback is not None and readback < len(results):
            reasons.append(
                f"LangSmith read-back found {readback} of {len(results)} case traces after the wait"
            )
    scored = not reasons
    precision_ok = bar_met(metrics.precision)
    summary = EvalRunSummary(
        scored=scored,
        run_date=started.date(),
        model=model,
        experiment=experiment if client is not None else None,
        n=metrics.n,
        errored=len(metrics.errored),
        precision=metrics.precision,
        recall=metrics.recall,
        abstention_rate=metrics.abstention_rate,
        bar=PRECISION_BAR,
        bar_met=scored and precision_ok,
    )
    markdown = render_markdown(
        summary, metrics, results, limitations(suite, model), reasons, refreshed=refreshed
    )
    if readback is not None:
        markdown += f"LangSmith read-back: {readback} of {len(results)} case traces.\n"
    if not scored:
        exit_code = EXIT_COULD_NOT_RUN
    else:
        exit_code = EXIT_MET if summary.bar_met else EXIT_NOT_MET
    run = EvalRun(summary, metrics, results, reasons, aborted, markdown, exit_code, readback)
    details = {
        "summary": json.loads(summary.model_dump_json()),
        "metrics": json.loads(metrics.model_dump_json()),
        "unscored_reasons": reasons,
        "cases": [
            {
                "id": r.case.id,
                "label": r.case.label,
                "predicted": r.scored_as,
                "status": r.status,
                "detail": r.detail,
                "scan_id": r.scan_id,
            }
            for r in results
        ],
        "limitations": limitations(suite, model),
    }
    run.files = _write_results(results_dir, started.strftime("%Y%m%dT%H%M%SZ"), run, details)
    return run


@dataclass
class EvalCommandResult:
    exit_code: int
    text: str


def _has_recordings(directory: Path) -> bool:
    return directory.is_dir() and any(directory.glob("*.json"))


async def run_eval_command(
    settings: Settings,
    *,
    cases_path: Path,
    refresh: bool,
    use_langsmith: bool,
    results_dir: Path,
    recordings_dir: Path,
    record_inner: Mapping[str, httpx.AsyncBaseTransport] | None = None,
) -> EvalCommandResult:
    """The `eval` command: build the real components, run, return the output text and exit code."""

    def could_not_run(message: str) -> EvalCommandResult:
        return EvalCommandResult(EXIT_COULD_NOT_RUN, f"Could not run the evaluation: {message}\n")

    try:
        suite = load_cases(cases_path)
    except CasesError as exc:
        return could_not_run(str(exc))
    needed = required_for(settings, "eval")
    if not use_langsmith:
        needed = [name for name in needed if name != "LANGSMITH_API_KEY"]
    try:
        require(settings, needed)
    except ConfigError as exc:
        return could_not_run(str(exc))
    osv_dir, tavily_dir = recordings_dir / "osv", recordings_dir / "tavily"
    if not refresh and not (_has_recordings(osv_dir) and _has_recordings(tavily_dir)):
        return could_not_run(
            f"no recorded OSV or Tavily answers in {recordings_dir}; "
            "run once with --refresh-research"
        )
    clock = SystemClock()
    started = clock.now()
    experiment = experiment_name(started, settings.nebius_model)
    with tempfile.TemporaryDirectory(prefix="pir-eval-", ignore_cleanup_errors=True) as scratch:
        # A fresh data dir: no cached OSV or research answer may stand in for a recorded one.
        eval_settings = settings.model_copy(
            update={
                "data_dir": scratch,
                "langsmith_tracing": use_langsmith,
                "langsmith_project": experiment,
            }
        )
        client = make_client(eval_settings) if use_langsmith else None
        if use_langsmith and client is None:
            return could_not_run("LANGSMITH_API_KEY is not usable")
        try:
            deps = build_deps(eval_settings, langsmith=client, clock=clock)
        except ConfigError as exc:
            return could_not_run(str(exc))
        inner = record_inner or {}  # tests give fake services to record from
        osv_transport = RecordReplayTransport(osv_dir, record=refresh, inner=inner.get("osv"))
        tavily_transport = RecordReplayTransport(
            tavily_dir, record=refresh, inner=inner.get("tavily")
        )
        deps.osv = OsvClient(eval_settings, transport=osv_transport)
        deps.researcher = AdvisoryResearcher(
            eval_settings,
            search=TavilySearchClient(eval_settings, transport=tavily_transport),
            llm=deps.llm,
            bill_search=refresh,  # a replayed answer made no request, so it costs nothing
        )
        try:
            run = await run_eval(
                suite,
                deps,
                client=client,
                results_dir=results_dir,
                experiment=experiment,
                recordings=[osv_transport, tavily_transport],
                refreshed=refresh,
            )
        except EvalCouldNotRun as exc:
            return could_not_run(str(exc))
        finally:
            await deps.aclose()
    spend = "\n".join(deps.meter.summary_lines())
    verdict = "PASS" if run.summary.bar_met else "FAIL"
    lines = [
        run.markdown.rstrip("\n"),
        "",
        f"Spend:\n{spend}",
        "",
        f"{verdict}: LR precision "
        f"{_fmt(run.summary.precision)} against the {PRECISION_BAR:.2f} bar",
    ]
    if not run.summary.scored:
        lines.append("UNSCORED: " + "; ".join(run.unscored_reasons))
    lines.append("Results written to: " + ", ".join(str(p) for p in run.files))
    return EvalCommandResult(run.exit_code, "\n".join(lines) + "\n")
