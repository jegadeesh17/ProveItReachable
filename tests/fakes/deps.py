"""A `PipelineDeps` built entirely from fakes, plus a tiny fixture repository.

The repository pins PyYAML 5.3.1 and calls `yaml.full_load` at app.py line 5, so with the recorded
OSV and Tavily answers and the scripted `FakeLLM` the whole pipeline reaches a `likely reachable`
verdict without any network or Sandbox.
"""

import contextlib
import io
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from fakes.http import RecordingTransport, fixture_response, json_response
from fakes.langsmith import FakeLangSmithClient
from fakes.ledger import MemorySpendLedger, make_state
from fakes.llm import Call, FakeLLM, json_result, tool_result
from pir.agent_client import LocalFixtureTarget, SandboxAgentClient
from pir.config import Settings
from pir.deps import PipelineDeps
from pir.executor.base import ExecResult
from pir.executor.fake import FakeExecutor, _command_key
from pir.logs import JsonFormatter, ScanContextFilter
from pir.models.api import ProgressEvent
from pir.osv import OsvClient
from pir.reasoning.tool_calling import ToolCallingStrategy
from pir.research import AdvisoryResearcher, TavilySearchClient
from pir.spend import Refused, ScanBudget, ScanBudgetLimits, SpendMeter, SystemClock

SHA = "d" * 40
GHSA = "GHSA-8q59-q68h-6hv4"
OTHER_GHSA = "GHSA-aaaa-bbbb-cccc"
NVD_URL = "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"
QUERYBATCH = "POST https://api.osv.dev/v1/querybatch"
VULN = "GET https://api.osv.dev/v1/vulns/"
SEARCH = "POST https://api.tavily.com/search"

APP = """import yaml


def load_config(text):
    return yaml.full_load(text)


if __name__ == "__main__":
    print(load_config("a: 1"))
"""
REPO_FILES = {"requirements.txt": "PyYAML==5.3.1\n", "app.py": APP}

RESEARCH_REPLY = {
    "identified": True,
    "symbols": ["yaml.full_load"],
    "evidence_urls": [NVD_URL],
    "reason": "",
}
VERDICT = {
    "verdict": "likely reachable",
    "confidence": "medium",
    "call_path": [
        {"file": "app.py", "line": 5, "edge": "vulnerable_call", "symbol": "yaml.full_load"}
    ],
    "source_locations": [
        {"file": "app.py", "line": 5, "kind": "call", "snippet": "return yaml.full_load(text)"}
    ],
    "explanation": "load_config passes its argument to yaml.full_load.",
    "limitations": ["Static analysis only."],
}


def write_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    """Write a fixture repository (default: `REPO_FILES`) under `path` and return it."""
    for name, text in (files or REPO_FILES).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return path


def scripted_llm(*, research: Any = RESEARCH_REPLY, verdict: Any = VERDICT, **kw: Any) -> FakeLLM:
    """Research (JSON mode) gets `research`; reasoning: find_call_sites, then submit_verdict."""
    rounds = {"reasoning": 0}

    def step(call: Call):
        if call.json_mode:
            return json_result(research)
        rounds["reasoning"] += 1
        if rounds["reasoning"] == 1:
            return tool_result("find_call_sites", {"symbol": "yaml.full_load"})
        return tool_result("submit_verdict", verdict)

    return FakeLLM([step], **kw)


def osv_routes(ids_by_package: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """querybatch answers per queried package name; vuln details come from the recorded files."""
    ids_by_package = {"pyyaml": [GHSA]} if ids_by_package is None else ids_by_package

    def querybatch(request: httpx.Request) -> httpx.Response:
        queries = json.loads(request.content)["queries"]
        results = [
            {"vulns": [{"id": i} for i in ids_by_package.get(q["package"]["name"], [])]}
            for q in queries
        ]
        return json_response(200, {"results": results})

    routes: dict[str, Any] = {QUERYBATCH: querybatch}
    for advisory_id in (GHSA, OTHER_GHSA):
        routes[VULN + advisory_id] = fixture_response(200, "osv", f"vuln_{advisory_id}.json")
    return routes


def tavily_routes() -> dict[str, Any]:
    return {SEARCH: fixture_response(200, "tavily", "search_pyyaml.json")}


async def no_sleep(_seconds: float) -> None:
    return None


class ListSink:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    async def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)


@dataclass
class Harness:
    deps: PipelineDeps
    settings: Settings
    executor: Any
    llm: FakeLLM
    osv_transport: RecordingTransport
    tavily_transport: RecordingTransport
    ledger: MemorySpendLedger
    langsmith: FakeLangSmithClient | None = None
    repos: list[Path] = field(default_factory=list)

    async def reserve(self, scan_id: str = "a" * 32) -> ScanBudget:
        budget = await self.deps.meter.reserve(scan_id)
        assert not isinstance(budget, Refused), budget
        return budget

    @staticmethod
    def target(repo: Path) -> LocalFixtureTarget:
        return LocalFixtureTarget(path=str(repo), commit_sha=SHA)


def make_harness(
    tmp_path: Path,
    *,
    executor: Any = None,
    agent: Any = None,
    llm: FakeLLM | None = None,
    osv: dict[str, Any] | None = None,
    tavily: dict[str, Any] | None = None,
    langsmith: FakeLangSmithClient | None = None,
    secrets: dict[str, str] | None = None,
    **settings_kw: Any,
) -> Harness:
    values: dict[str, Any] = {
        "nebius_api_key": "nebius-test-key",
        "nebius_project_id": "project-test",
        "tavily_api_key": "tavily-test-key",
        "data_dir": str(tmp_path / "data"),
        "spend_ledger_path": str(tmp_path / "ledger.json"),
        "executor": "fake",
    }
    values.update(secrets or {})
    values.update(settings_kw)
    settings = Settings(_env_file=None, **values)
    clock = SystemClock()
    executor = executor if executor is not None else FakeExecutor()
    llm = llm if llm is not None else scripted_llm()
    osv_transport = RecordingTransport(osv if osv is not None else osv_routes())
    tavily_transport = RecordingTransport(tavily if tavily is not None else tavily_routes())
    ledger = MemorySpendLedger(make_state(clock.now(), {"local": {"nebius": 10.0, "tavily": 5.0}}))
    meter = SpendMeter(
        ledger,
        "local",
        ScanBudgetLimits.from_settings(settings),
        clock,
        scan_timeout_s=settings.scan_timeout_s,
        sleep=no_sleep,
    )
    search = TavilySearchClient(settings, transport=tavily_transport, sleep=no_sleep)
    deps = PipelineDeps(
        settings=settings,
        executor=executor,
        agent=agent if agent is not None else SandboxAgentClient(executor, settings),
        osv=OsvClient(settings, transport=osv_transport, sleep=no_sleep),
        researcher=AdvisoryResearcher(settings, search=search, llm=llm),
        llm=llm,
        strategy=ToolCallingStrategy(settings),
        meter=meter,
        clock=clock,
        validate=_no_github,
        langsmith=langsmith,
    )
    return Harness(
        deps, settings, executor, llm, osv_transport, tavily_transport, ledger, langsmith
    )


async def _no_github(*_args: Any, **_kwargs: Any):
    raise AssertionError("the pipeline never validates a repository itself")


@contextlib.contextmanager
def capture_json_logs(level: int = logging.INFO) -> Iterator[io.StringIO]:
    """Route every log record to a buffer through the production JSON formatter."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ScanContextFilter())
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(level)
    try:
        yield buffer
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def log_lines(buffer: io.StringIO) -> list[dict[str, Any]]:
    """Every captured line parsed as a JSON object (a line that is not JSON fails the test)."""
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


class ScriptedExecutor(FakeExecutor):
    """A FakeExecutor whose named commands (for example "index") raise or return on demand."""

    def __init__(self, overrides: dict[str, Callable | Exception | ExecResult]) -> None:
        super().__init__()
        self.overrides = overrides

    async def run(self, image, argv, **kwargs):
        parsed = _command_key(argv)
        action = self.overrides.get(parsed[0]) if parsed else None
        if action is None:
            return await super().run(image, argv, **kwargs)
        if isinstance(action, Exception):
            raise action
        if isinstance(action, ExecResult):
            return action
        return await action()


def timed_out_result() -> ExecResult:
    return ExecResult(
        exit_code=-1,
        stdout="",
        stderr="",
        timed_out=True,
        truncated=False,
        duration_s=1.0,
        cost_usd=0.0,
        image=None,
    )


def failed_result(stderr: str = "agent crashed") -> ExecResult:
    return ExecResult(
        exit_code=1,
        stdout="",
        stderr=stderr,
        timed_out=False,
        truncated=False,
        duration_s=0.1,
        cost_usd=0.0,
        image=None,
    )
