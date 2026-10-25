"""The scan pipeline with all fakes (M1-TASK-11).

AC-01 (step order, final states, tools rule), AC-09, AC-11, AC-24 to AC-27, AC-30, AC-53,
NFR-06 (a failing external call never stops the report), ADR-0033 (selfcheck fails closed),
ADR-0034 (ExecutorSpendDenied), MAX_FINDINGS_REASONED, the scan deadline, unexpected exceptions,
discarded images, and AC-73 / AC-74 (JSON log lines and trace correlation).
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from fakes.deps import (
    GHSA,
    NVD_URL,
    OTHER_GHSA,
    QUERYBATCH,
    SEARCH,
    ListSink,
    ScriptedExecutor,
    capture_json_logs,
    failed_result,
    make_harness,
    osv_routes,
    scripted_llm,
    timed_out_result,
    write_repo,
)
from fakes.langsmith import FakeLangSmithClient
from fakes.llm import FakeLLM, json_result

from pir.agent_client import SandboxFailed
from pir.executor.base import ExecutorSpendDenied, ExecutorUnavailable
from pir.llm import LLMError
from pir.models.report import ScanReport
from pir.pipeline import run_scan
from pir.spend import ScanBudget, SpendDenied

STEPS = ["resolve", "osv", "research", "analysis", "reasoning", "report"]


async def scan(h, tmp_path: Path, files: dict[str, str] | None = None, budget=None, sink=None):
    repo = write_repo(tmp_path / "repo", files)
    budget = budget or await h.reserve()
    report = await run_scan(h.deps, h.target(repo), budget, sink=sink)
    assert (
        ScanReport.model_validate_json(report.model_dump_json()) == report
    )  # schema-valid (NFR-06)
    return report


def states(report) -> dict[str, str]:
    return {s.name: s.state for s in report.steps}


def tools(report) -> dict[str, list[str]]:
    return {s.name: list(s.tools) for s in report.steps}


# ---- AC-01, AC-30 ----------------------------------------------------------------------


async def test_a_local_fixture_scan_says_it_was_not_fetched_from_github(tmp_path):
    # M1-FIX-02: `scan --local` reports a made-up GitHub URL, so the report must say so
    report = await scan(make_harness(tmp_path), tmp_path)
    assert "local fixture, not fetched from GitHub" in report.limitations


async def test_ac01_steps_run_in_order_with_final_states_and_the_tools_rule(tmp_path):
    h = make_harness(tmp_path)
    sink = ListSink()
    report = await scan(h, tmp_path, sink=sink)

    assert [s.name for s in report.steps] == STEPS
    assert all(s.state == "done" for s in report.steps)
    assert tools(report) == {
        "resolve": ["lockfile"],
        "osv": ["osv"],
        "research": ["tavily", "nemotron"],
        "analysis": ["ast"],
        "reasoning": ["nemotron", "ast"],
        "report": ["verdict_gate"],
    }
    assert not any("sandbox" in s.tools for s in report.steps)  # FakeExecutor never yields it
    assert report.outcome == "findings"
    assert [f.verdict for f in report.findings] == ["likely reachable"]
    assert report.model == "nvidia/nemotron-fake"
    # events: one running and one terminal event per step, then done, numbered 1, 2, 3 ...
    assert [e.seq for e in sink.events] == list(range(1, len(sink.events) + 1))
    assert [(e.step, e.state) for e in sink.events if e.type == "step"] == [
        (name, state) for name in STEPS for state in ("running", "done")
    ]
    assert sink.events[-1].type == "done" and sink.events[-1].outcome == "findings"


async def test_ac30_analysis_goes_through_executor_run_with_the_network_off(tmp_path):
    h = make_harness(tmp_path)
    await scan(h, tmp_path)
    index_calls = [c for c in h.executor.calls if " index " in c.command]
    assert len(index_calls) == 1
    assert index_calls[0].network is False


async def test_images_are_discarded_at_the_end(tmp_path):
    h = make_harness(tmp_path)
    await scan(h, tmp_path)
    # every image the scan ran from (the prepared base and the saved repo image A) is gone
    refs = {c.image.ref for c in h.executor.calls if " index " in c.command}
    assert refs and all(not os.path.exists(ref) for ref in refs)


async def test_a_failing_event_sink_does_not_stop_the_scan(tmp_path):
    class Broken:
        async def emit(self, event):
            raise RuntimeError("sink down")

    h = make_harness(tmp_path)
    report = await scan(h, tmp_path, sink=Broken())
    assert report.outcome == "findings"


# ---- AC-11, AC-09 ----------------------------------------------------------------------


async def test_ac11_no_advisories_is_clean_with_no_findings(tmp_path):
    h = make_harness(tmp_path, osv=osv_routes({}))
    report = await scan(h, tmp_path)
    assert report.findings == [] and report.outcome == "clean"
    assert states(report) == {
        "resolve": "done",
        "osv": "done",
        "research": "skipped",
        "analysis": "skipped",
        "reasoning": "skipped",
        "report": "done",
    }
    assert tools(report)["research"] == tools(report)["analysis"] == []
    assert h.tavily_transport.requests == []  # nothing to research, nothing to pay for


async def test_ac09_unresolved_dependencies_are_never_sent_to_osv(tmp_path):
    lock = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "locks" / "uv"
    files = {
        name: (lock / name).read_text(encoding="utf-8") for name in ("pyproject.toml", "uv.lock")
    }
    h = make_harness(tmp_path, osv=osv_routes({}))
    report = await scan(h, tmp_path, files)

    by_name = {d.name: d for d in report.dependencies}
    assert by_name["gitdep"].resolved is False
    assert by_name["gitdep"].osv_status == "not_queried" and by_name["gitdep"].version is None
    assert by_name["requests"].resolved and by_name["requests"].osv_status == "checked"
    queried = {q["package"]["name"] for q in h.osv_transport.json_bodies()[0]["queries"]}
    assert "gitdep" not in queried and "requests" in queried
    assert report.outcome == "incomplete"  # an unresolved dependency is never "clean"
    assert report.summary.dependencies_unresolved == 1


# ---- AC-24 to AC-27 --------------------------------------------------------------------


async def test_ac24_tavily_failure_makes_only_that_finding_uncertain(tmp_path):
    h = make_harness(tmp_path, tavily={SEARCH: httpx.Response(500, json={})})
    report = await scan(h, tmp_path)
    (finding,) = report.findings
    assert (finding.verdict, finding.reason_code) == ("uncertain", "tavily_failed")
    assert states(report)["reasoning"] == "skipped"
    assert report.outcome == "findings"


async def test_ac25_nebius_failure_makes_only_that_finding_uncertain(tmp_path):
    llm = FakeLLM([LLMError("http", "model call failed")])
    h = make_harness(tmp_path, llm=llm)
    report = await scan(h, tmp_path)
    (finding,) = report.findings
    assert (finding.verdict, finding.reason_code) == ("uncertain", "nebius_failed")


async def test_ac25_nebius_failure_in_reasoning_keeps_other_findings(tmp_path):
    ids = {"pyyaml": [GHSA, OTHER_GHSA]}
    rounds = {"n": 0}

    def step(call):
        if call.json_mode:
            return json_result(
                {"identified": True, "symbols": ["yaml.full_load"], "evidence_urls": [NVD_URL]}
            )
        rounds["n"] += 1
        raise LLMError("timeout", "model call timed out")

    h = make_harness(tmp_path, osv=osv_routes(ids), llm=FakeLLM([step]), finding_concurrency=1)
    report = await scan(h, tmp_path)
    assert len(report.findings) == 2
    assert {f.reason_code for f in report.findings} == {"nebius_failed"}


async def test_ac26_osv_failure_gives_not_checked_entries_and_never_clean(tmp_path):
    h = make_harness(tmp_path, osv={QUERYBATCH: httpx.Response(500, json={})})
    report = await scan(h, tmp_path)

    (dep,) = report.dependencies
    assert dep.osv_status == "not_checked" and dep.osv_error
    (finding,) = report.findings
    assert finding.kind == "not_checked" and finding.verdict == "uncertain"
    assert finding.reason_code == "osv_failed"
    assert finding.advisory is None and finding.advisory_links == []
    assert report.outcome != "clean" and states(report)["osv"] == "failed"
    assert states(report)["research"] == "skipped"


async def test_ac27_sandbox_index_timeout_makes_findings_uncertain_sandbox_timeout(tmp_path):
    h = make_harness(tmp_path, executor=ScriptedExecutor({"index": timed_out_result()}))
    report = await scan(h, tmp_path)
    (finding,) = report.findings
    assert (finding.verdict, finding.reason_code, finding.reason) == (
        "uncertain",
        "sandbox_timeout",
        "sandbox timeout",
    )
    assert states(report)["analysis"] == "failed" and states(report)["reasoning"] == "skipped"
    assert h.llm.calls and not any(not c.json_mode for c in h.llm.calls)  # no reasoning call


@pytest.mark.parametrize(
    "action",
    [failed_result(), ExecutorUnavailable("sandbox down")],
    ids=["bad exit", "executor unavailable"],
)
async def test_ac27_other_index_failures_are_sandbox_failed(tmp_path, action):
    h = make_harness(tmp_path, executor=ScriptedExecutor({"index": action}))
    report = await scan(h, tmp_path)
    (finding,) = report.findings
    assert (finding.verdict, finding.reason_code) == ("uncertain", "sandbox_failed")
    assert states(report)["analysis"] == "failed"


async def test_sandbox_failure_in_resolve_leaves_dependencies_unknown_and_skips_osv(tmp_path):
    h = make_harness(tmp_path, executor=ScriptedExecutor({"unpack": failed_result()}))
    report = await scan(h, tmp_path)
    assert states(report)["resolve"] == "failed"
    assert report.resolution.method == "failed"
    assert report.findings == [] and report.outcome == "incomplete"
    assert h.osv_transport.requests == []  # no OSV queries
    assert {s.name for s in report.steps if s.state == "skipped"} == set(STEPS[1:5])


async def test_sandbox_timeout_in_resolve_is_reported_as_a_timeout(tmp_path):
    h = make_harness(tmp_path, executor=ScriptedExecutor({"unpack": timed_out_result()}))
    report = await scan(h, tmp_path)
    resolve = report.steps[0]
    assert resolve.state == "failed" and resolve.message == "sandbox timeout"
    assert report.outcome == "incomplete"


async def test_compile_that_the_fake_executor_cannot_run_fails_resolve_not_the_scan(tmp_path):
    files = {"pyproject.toml": '[project]\nname = "x"\nversion = "1"\ndependencies = ["pyyaml"]\n'}
    h = make_harness(tmp_path)
    report = await scan(h, tmp_path, files)
    assert states(report)["resolve"] == "failed" and report.outcome == "incomplete"


# ---- AC-53 and ADR-0034 ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "text"),
    [("spend_cap_reached", "spend cap reached"), ("scan_budget_reached", "scan budget reached")],
)
async def test_ac53_spend_denial_during_research_names_the_reason(tmp_path, reason, text):
    h = make_harness(tmp_path)
    budget = ScanBudget("a" * 32, {"nebius": 1.0, "tavily": 0.0}, {"tavily": reason})
    report = await scan(h, tmp_path, budget=budget)
    (finding,) = report.findings
    assert (finding.verdict, finding.reason_code, finding.reason) == ("uncertain", reason, text)
    assert h.tavily_transport.requests == []  # denied before any request


async def test_ac53_nebius_denial_during_research_names_the_reason(tmp_path):
    h = make_harness(tmp_path, llm=scripted_llm(cost_usd=0.5))
    budget = ScanBudget("a" * 32, {"nebius": 0.001, "tavily": 1.0}, {"nebius": "spend_cap_reached"})
    report = await scan(h, tmp_path, budget=budget)
    (finding,) = report.findings
    assert (finding.reason_code, finding.reason) == ("spend_cap_reached", "spend cap reached")


async def test_ac53_spend_denial_in_reasoning_marks_the_finding_uncertain(tmp_path):
    h = make_harness(tmp_path, llm=scripted_llm(cost_usd=0.4))
    # the research call fits the reservation; the reasoning calls do not
    budget = ScanBudget("a" * 32, {"nebius": 0.5, "tavily": 1.0}, {"nebius": "spend_cap_reached"})
    report = await scan(h, tmp_path, budget=budget)
    (finding,) = report.findings
    assert (finding.verdict, finding.reason_code) == ("uncertain", "spend_cap_reached")


@pytest.mark.parametrize("command", ["unpack", "index"])
@pytest.mark.parametrize(
    ("reason", "text"),
    [("spend_cap_reached", "spend cap reached"), ("scan_budget_reached", "scan budget reached")],
)
async def test_adr0034_executor_spend_denied_maps_by_reason(tmp_path, command, reason, text):
    denied = ExecutorSpendDenied(SpendDenied("nebius", reason))
    h = make_harness(tmp_path, executor=ScriptedExecutor({command: denied}))
    report = await scan(h, tmp_path)
    if command == "index":
        (finding,) = report.findings
        assert (finding.verdict, finding.reason_code, finding.reason) == ("uncertain", reason, text)
        assert states(report)["analysis"] == "failed"
    else:  # the first Sandbox step, in resolve: no repo, no dependencies
        assert report.steps[0].state == "failed" and report.steps[0].message == text
        assert report.findings == [] and report.outcome == "incomplete"


# ---- ADR-0033: selfcheck fails closed ----------------------------------------------------


class SelfcheckFailingAgent:
    """Stands in for a SandboxAgentClient whose selfcheck failed in open_workspace."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def open_workspace(self, target, budget):
        self.calls.append("open_workspace")
        raise SandboxFailed("selfcheck", "base image does not match the reviewed fingerprint")

    async def resolve(self, ws):  # pragma: no cover - must not be reached
        self.calls.append("resolve")
        raise AssertionError("resolve ran after a failed selfcheck")

    async def index(self, ws, targets):  # pragma: no cover - must not be reached
        self.calls.append("index")
        raise AssertionError("index ran after a failed selfcheck")

    async def close(self, ws):  # pragma: no cover - there is no workspace
        self.calls.append("close")


async def test_adr0033_selfcheck_failure_runs_no_further_sandbox_step(tmp_path):
    agent = SelfcheckFailingAgent()
    h = make_harness(tmp_path, agent=agent)
    report = await scan(h, tmp_path)

    assert agent.calls == ["open_workspace"]
    assert h.executor.calls == []
    resolve = report.steps[0]
    assert resolve.state == "failed" and "sandbox unavailable" in (resolve.message or "")
    assert report.resolution.method == "failed"
    assert report.findings == [] and report.outcome == "incomplete"


# ---- limits, deadline, unexpected errors -------------------------------------------------


async def test_max_findings_reasoned_marks_the_least_severe_finding_limit_reached(tmp_path):
    ids = {"pyyaml": [GHSA, OTHER_GHSA]}
    h = make_harness(tmp_path, osv=osv_routes(ids), max_findings_reasoned=1)
    report = await scan(h, tmp_path)
    by_id = {f.advisory.id: f for f in report.findings if f.advisory}
    assert by_id[GHSA].verdict == "likely reachable"  # critical: reasoned first
    assert by_id[OTHER_GHSA].reason_code == "finding_limit_reached"


async def test_scan_deadline_marks_unfinished_findings_scan_timeout(tmp_path, monkeypatch):
    """The 4th step (analysis) hits the deadline: in-flight work is cancelled."""
    calls = {"n": 0}
    real_timeout = asyncio.timeout

    def fake_timeout_at(_deadline):
        calls["n"] += 1
        return real_timeout(0.05 if calls["n"] == 4 else None)

    async def never_finishes():
        await asyncio.sleep(30)

    monkeypatch.setattr(asyncio, "timeout_at", fake_timeout_at)
    h = make_harness(tmp_path, executor=ScriptedExecutor({"index": never_finishes}))
    with capture_json_logs() as logs:
        report = await scan(h, tmp_path)

    (finding,) = report.findings
    assert (finding.verdict, finding.reason_code) == ("uncertain", "scan_timeout")
    assert states(report)["analysis"] == "failed" and report.steps[3].message == "scan timeout"
    assert states(report)["reasoning"] == "skipped" and states(report)["report"] == "done"
    ends = {
        line["stage"]: line["outcome"]
        for line in map(json.loads, logs.getvalue().splitlines())
        if line.get("event") == "stage_end"
    }
    assert ends["analysis"] == "timeout" and ends["reasoning"] == "skipped"


async def test_unexpected_exception_fails_the_step_with_an_error_id_and_the_report_completes(
    tmp_path, monkeypatch
):
    h = make_harness(tmp_path)

    async def boom(deps):
        raise RuntimeError("osv exploded")

    monkeypatch.setattr(h.deps.osv, "query", boom)
    with capture_json_logs() as logs:
        report = await scan(h, tmp_path)

    osv = report.steps[1]
    assert osv.state == "failed" and "error id" in (osv.message or "")
    error_id = (osv.message or "").rsplit(" ", 1)[1].rstrip(")")
    lines = [json.loads(line) for line in logs.getvalue().splitlines()]
    assert any(line.get("error_id") == error_id and "exc" in line for line in lines)
    (dep,) = report.dependencies
    assert dep.osv_status == "not_checked" and report.outcome != "clean"
    assert [s.name for s in report.steps] == STEPS


async def test_research_that_raises_only_loses_its_own_finding(tmp_path, monkeypatch):
    h = make_harness(tmp_path)

    async def boom(*args, **kwargs):
        raise RuntimeError("researcher bug")

    monkeypatch.setattr(h.deps.researcher, "research", boom)
    report = await scan(h, tmp_path)
    (finding,) = report.findings
    assert finding.verdict == "uncertain"


# ---- AC-73 and AC-74 ---------------------------------------------------------------------


async def test_ac73_ac74_log_lines_are_json_with_scan_id_and_stage_fields(tmp_path):
    fake_ls = FakeLangSmithClient()
    h = make_harness(tmp_path, langsmith=fake_ls)
    budget = await h.reserve("b" * 32)
    repo = write_repo(tmp_path / "repo")
    with capture_json_logs() as logs:
        report = await run_scan(h.deps, h.target(repo), budget, scan_id="b" * 32)

    lines = [json.loads(line) for line in logs.getvalue().splitlines() if line.strip()]  # AC-73
    assert lines and all(isinstance(line, dict) for line in lines)
    assert all(line["scan_id"] == "b" * 32 for line in lines)  # AC-74
    ends = [line for line in lines if line.get("event") == "stage_end"]
    assert [line["stage"] for line in ends] == STEPS
    for line in ends:
        assert isinstance(line["duration_ms"], int) and line["duration_ms"] >= 0
        assert line["outcome"] in {"ok", "failed", "skipped", "timeout"}
    # trace correlation: the fake LangSmith root run carries the same scan_id
    (root,) = fake_ls.root_runs()
    assert root["extra"]["metadata"]["scan_id"] == "b" * 32 == report.scan_id
    started = [line for line in lines if line.get("event") == "scan_started"]
    assert len(started) == 1 and started[0]["run_id"] == root["id"]


async def test_scan_runs_untraced_when_tracing_is_off(tmp_path):
    h = make_harness(tmp_path)  # no LangSmith client, LANGSMITH_TRACING unset
    with capture_json_logs() as logs:
        await scan(h, tmp_path)
    started = [json.loads(line) for line in logs.getvalue().splitlines() if "scan_started" in line]
    assert started and started[0]["run_id"] is None
