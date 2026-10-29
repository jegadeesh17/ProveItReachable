"""The LangSmith run tree of a scan (ADR-0038, AC-79; the AC-37 amendment).

The full pipeline runs on the canary fixture with every external call faked: OSV and Tavily answer
from recordings, the Sandbox is the `FakeExecutor`, and the LLM is the real `NebiusLLMClient` over
an `httpx.MockTransport` Token Factory, so `wrap_openai` posts real LLM runs. A real `langsmith`
client with a recording session (`FakeLangSmithClient`) stands in for LangSmith; nothing touches
the network.
"""

import concurrent.futures
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from fakes.deps import (
    GHSA,
    RESEARCH_REPLY,
    VERDICT,
    Harness,
    make_harness,
    osv_routes,
)
from fakes.http import RecordingTransport, json_response
from fakes.langsmith import FakeLangSmithClient, RunNode, ShareableLangSmithClient
from langsmith import traceable, tracing_context
from langsmith.run_trees import RunTree
from test_eval_runner import CASES, EvalFakeLangSmith, as_sandbox, make_suite
from test_eval_runner import harness as eval_harness

from pir import tracing
from pir.agent_client import LocalFixtureTarget
from pir.config import Settings
from pir.llm import NebiusLLMClient
from pir.logs import clear_secrets, register_secrets
from pir.models.report import ScanReport
from pir.pipeline import run_scan
from pir.tracing import advisory_run, finding_run, run_traced, step_run, tool_run, valid_share_url

CANARY = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "canary"
CANARY_SHA = "c" * 40
COMPLETIONS = "POST https://api.tokenfactory.nebius.com/v1/chat/completions"
STEPS = ["resolve", "osv", "research", "analysis", "reasoning", "report"]
STEP_RUNS = [f"pir.step.{name}" for name in STEPS]
TIME_FIELDS = {"scan_id", "started_at", "finished_at", "generated_at", "ran_at"}
FIND_PATHS = {"symbol": "yaml.full_load"}


def _completion(message: dict[str, Any], finish: str) -> httpx.Response:
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    return json_response(200, body)


def _tool_call(name: str, arguments: str, call_id: str) -> httpx.Response:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        ],
    }
    return _completion(message, "tool_calls")


def token_factory(first_arguments: str | None = None) -> RecordingTransport:
    """A fake Token Factory: JSON-mode calls get the research reply; reasoning calls find_paths
    (first with `first_arguments` when given, which may be malformed) and then submit_verdict."""
    state = {"reasoning": 0}

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("response_format") == {"type": "json_object"}:
            return _completion({"role": "assistant", "content": json.dumps(RESEARCH_REPLY)}, "stop")
        state["reasoning"] += 1
        calls_so_far = state["reasoning"]
        if first_arguments is not None and calls_so_far == 1:
            return _tool_call("find_paths", first_arguments, "call_bad")
        if calls_so_far <= (2 if first_arguments is not None else 1):
            return _tool_call("find_paths", json.dumps(FIND_PATHS), f"call_{calls_so_far}")
        return _tool_call("submit_verdict", json.dumps(VERDICT), "call_submit")

    return RecordingTransport({COMPLETIONS: answer})


def traced_harness(
    tmp_path: Path,
    *,
    tracing: bool = True,
    langsmith: FakeLangSmithClient | None = None,
    transport: RecordingTransport | None = None,
    **kw: Any,
) -> Harness:
    settings = Settings(_env_file=None, nebius_api_key="nebius-test-key", langsmith_tracing=tracing)
    llm = NebiusLLMClient(settings, transport=transport or token_factory())
    return make_harness(tmp_path, llm=llm, langsmith=langsmith, langsmith_tracing=tracing, **kw)  # type: ignore[arg-type]


async def scan_canary(h: Harness, scan_id: str = "a" * 32) -> ScanReport:
    budget = await h.reserve(scan_id)
    report = await run_scan(
        h.deps, LocalFixtureTarget(path=str(CANARY), commit_sha=CANARY_SHA), budget
    )
    await h.deps.llm.aclose()  # type: ignore[attr-defined]
    return report


def only_root(fake: FakeLangSmithClient) -> RunNode:
    (root,) = fake.run_tree()
    assert root.name == "pir.scan"
    return root


def step(root: RunNode, name: str) -> RunNode:
    (node,) = [child for child in root.children if child.name == f"pir.step.{name}"]
    return node


# ---- AC-79: the shape of the tree --------------------------------------------------------


async def test_ac79_the_canary_scan_uploads_one_tree_with_a_run_per_step(tmp_path):
    fake = FakeLangSmithClient()
    h = traced_harness(tmp_path, langsmith=fake)

    report = await scan_canary(h)

    root = only_root(fake)
    assert root.child_names == STEP_RUNS  # exactly the six steps, in pipeline order
    assert {child.run_type for child in root.children} == {"chain"}
    # no LLM or tool run is a direct child of pir.scan
    assert not [c for c in root.children if c.run_type in ("llm", "tool")]
    # each step run says how its step ended, matching the report
    assert [step(root, s).outputs for s in STEPS] == [
        {"state": s.state} for s in sorted(report.steps, key=lambda s: STEPS.index(s.name))
    ]
    # every uploaded run belongs to the one tree
    assert len(list(root.walk())) == len(fake.posted)


async def test_ac79_research_has_a_run_per_advisory_holding_the_search_and_the_llm_call(tmp_path):
    fake = FakeLangSmithClient()
    h = traced_harness(tmp_path, langsmith=fake)

    await scan_canary(h)

    research = step(only_root(fake), "research")
    assert research.child_names == ["pir.research.advisory"]  # one advisory (GHSA)
    (advisory,) = research.children
    assert advisory.metadata["advisory_id"] == GHSA
    assert advisory.metadata["from_cache"] is False
    assert [c.run_type for c in advisory.children] == ["tool", "llm"]
    search, llm = advisory.children
    assert search.name == "pir.tool.tavily_search"
    assert "yaml" in search.inputs["query"].lower() or GHSA in search.inputs["query"]
    # the answer is its size and URLs, not the page content
    assert set(search.outputs) == {"result_count", "urls"}
    assert search.outputs["result_count"] == len(search.outputs["urls"]) > 0
    assert llm.name != "pir.tool.tavily_search"


async def test_ac79_reasoning_has_a_run_per_finding_with_llm_runs_and_tool_runs_in_order(tmp_path):
    fake = FakeLangSmithClient()
    h = traced_harness(tmp_path, langsmith=fake)

    report = await scan_canary(h)

    reasoning = step(only_root(fake), "reasoning")
    assert (
        reasoning.child_names
        == ["pir.reasoning.finding"] * len(report.findings)
        == ["pir.reasoning.finding"]
    )
    (finding,) = reasoning.children
    assert finding.metadata["advisory_id"] == GHSA
    assert finding.metadata["package"] == "pyyaml"
    assert finding.metadata["version"]
    # find_paths is a tool run between the two LLM runs; submit_verdict is not a tool run
    assert [c.run_type for c in finding.children] == ["llm", "tool", "llm"]
    tool = finding.children[1]
    assert tool.name == "pir.tool.find_paths"
    assert tool.inputs == FIND_PATHS
    assert tool.outputs["ok"] is True
    assert isinstance(tool.outputs["result_bytes"], int) and tool.outputs["result_bytes"] > 0
    assert set(tool.outputs) == {"ok", "result_bytes"}  # size, not the answer
    assert not any(n.name == "pir.tool.submit_verdict" for n in finding.walk())


async def test_ac79_a_cached_advisory_has_no_search_run(tmp_path):
    first = FakeLangSmithClient()
    h = traced_harness(tmp_path, langsmith=first)
    await scan_canary(h)

    second = FakeLangSmithClient()
    h2 = traced_harness(tmp_path, langsmith=second)  # same data_dir: the 7-day cache is warm
    await scan_canary(h2, "b" * 32)

    (advisory,) = step(only_root(second), "research").children
    assert advisory.metadata["from_cache"] is True
    assert advisory.outputs["from_cache"] is True
    assert [c.run_type for c in advisory.children] == []  # no search and no extraction call


async def test_ac79_skipped_steps_still_get_a_step_run(tmp_path):
    fake = FakeLangSmithClient()
    h = traced_harness(tmp_path, langsmith=fake, osv=osv_routes({"pyyaml": []}))

    report = await scan_canary(h)

    root = only_root(fake)
    assert root.child_names == STEP_RUNS
    assert step(root, "research").outputs == {"state": "skipped"}
    assert step(root, "analysis").outputs == {"state": "skipped"}
    assert step(root, "reasoning").outputs == {"state": "skipped"}
    assert step(root, "report").outputs == {"state": "done"}
    assert step(root, "research").children == []  # nothing researched
    assert not report.findings


async def test_ac79_a_malformed_tool_call_is_not_executed_so_it_has_no_tool_run(tmp_path):
    fake = FakeLangSmithClient()
    h = traced_harness(tmp_path, langsmith=fake, transport=token_factory("{not json"))

    await scan_canary(h)

    (finding,) = step(only_root(fake), "reasoning").children
    tools = [c for c in finding.children if c.run_type == "tool"]
    assert [t.name for t in tools] == ["pir.tool.find_paths"]  # only the valid call ran
    assert [c.run_type for c in finding.children] == ["llm", "llm", "tool", "llm"]


# ---- tracing never changes a report; off posts nothing -----------------------------------


def normalized(report: ScanReport) -> str:
    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in TIME_FIELDS}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return json.dumps(strip(report.model_dump(mode="json")), sort_keys=True)


async def test_ac79_the_report_is_byte_identical_with_tracing_on_and_off(tmp_path):
    fake = FakeLangSmithClient()
    on = await scan_canary(traced_harness(tmp_path / "on", langsmith=fake))
    off = await scan_canary(traced_harness(tmp_path / "off", tracing=False))

    assert fake.posted, "the traced run uploaded runs"
    assert on.findings and on.findings == off.findings
    assert normalized(on) == normalized(off)


async def test_ac79_with_tracing_off_no_run_is_posted_even_beside_an_enabled_client(tmp_path):
    ambient = FakeLangSmithClient()
    h = traced_harness(tmp_path, tracing=False)  # no client given: the scan runs untraced
    with tracing_context(enabled=True, client=ambient):
        report = await scan_canary(h)

    assert report.findings
    assert ambient.payloads == []


def test_ac79_the_helpers_are_no_ops_with_no_run_open():
    ambient = FakeLangSmithClient()
    with tracing_context(enabled=True, client=ambient):
        with step_run("resolve") as a:
            a.set_outputs({"state": "done"})
            a.add_metadata(x=1)
        with advisory_run(GHSA, "pyyaml") as b, finding_run("pyyaml", "5.3.1", GHSA) as c:
            b.set_outputs({"x": 1})
            c.add_metadata(y=2)
        with tool_run("find_paths", {"symbol": "s"}) as d:
            d.set_outputs({"ok": True, "result_bytes": 1})
    assert ambient.payloads == []  # a run is never posted as a root


def test_ac79_a_helper_run_records_an_error_and_lets_it_propagate(tmp_path):
    fake = FakeLangSmithClient()
    with tracing_context(enabled=True, client=fake):
        from langsmith import traceable

        @traceable(name="pir.scan")
        def parent() -> None:
            with pytest.raises(ValueError):  # noqa: PT012
                with tool_run("find_paths", {"symbol": "s"}):
                    raise ValueError("boom")

        parent()
    (root,) = fake.run_tree()
    (tool,) = root.children
    assert tool.name == "pir.tool.find_paths" and tool.run_type == "tool"
    errors = [
        json.loads(data).get("error")
        for method, _url, data in fake._recording.calls
        if method == "PATCH" and data
    ]
    assert any(e and "boom" in e for e in errors)


async def test_ac79_no_secret_and_no_repository_source_is_in_the_tool_runs(tmp_path):
    fake = FakeLangSmithClient()
    await scan_canary(traced_harness(tmp_path, langsmith=fake))

    root = only_root(fake)
    tools = [n for n in root.walk() if n.run_type == "tool"]
    assert [t.name for t in tools] == ["pir.tool.tavily_search", "pir.tool.find_paths"]
    blob = json.dumps([(t.inputs, t.outputs) for t in tools])
    assert "return yaml.full_load" not in blob  # the repository's lines are not copied in
    assert "nebius-test-key" not in fake.text()
    assert "tavily-test-key" not in fake.text()


# ---- AC-37 (amended): under the eval runner the scan is a child of the case ---------------


async def test_ac37_the_eval_scan_is_a_child_of_the_case_and_has_the_six_step_runs(tmp_path):
    from pir.evaluation.runner import CASE_RUN_NAME, run_eval

    suite = make_suite(tmp_path, CASES[:1])
    h = eval_harness(tmp_path)
    as_sandbox(h)
    client = EvalFakeLangSmith()

    await run_eval(suite, h.deps, client=client, results_dir=tmp_path / "r")

    (case_run,) = client.got_case_runs
    assert case_run["name"] == CASE_RUN_NAME == "pir.eval_case"
    runs = client.got_other_run_info
    (scan,) = [r for r in runs if r["name"] == "pir.scan"]
    assert scan["parent_run_id"] == str(case_run["id"])
    steps = [r["name"] for r in runs if r["parent_run_id"] == scan["id"]]
    assert steps == STEP_RUNS
    # the finding and tool runs sit beneath their steps in the same trace
    assert {r["trace_id"] for r in runs} == {str(case_run["id"])}
    reasoning = next(r for r in runs if r["name"] == "pir.step.reasoning")
    finding = [r for r in runs if r["parent_run_id"] == reasoning["id"]]
    assert [r["name"] for r in finding] == ["pir.reasoning.finding"]
    assert [r["name"] for r in runs if r["parent_run_id"] == finding[0]["id"]] == [
        "pir.tool.find_call_sites"
    ]


# ---- ADR-0045: usage in the report and the optional public trace link -----------------------

SHARE_URL = "https://smith.langchain.com/public/0d6f5c1e-0000-4000-8000-000000000001/r"


async def test_adr45_usage_counters_add_up_on_a_fake_scan(tmp_path):
    transport = token_factory()
    h = traced_harness(tmp_path, tracing=False, transport=transport)
    budget = await h.reserve()

    report = await run_scan(
        h.deps, LocalFixtureTarget(path=str(CANARY), commit_sha=CANARY_SHA), budget
    )
    await h.deps.llm.aclose()  # type: ignore[attr-defined]

    usage = report.usage
    assert usage is not None
    llm_requests = len(transport.requests)
    assert usage.nemotron.calls == llm_requests > 0
    assert usage.nemotron.input_tokens == 100 * llm_requests  # the fake returns 100 and 20
    assert usage.nemotron.output_tokens == 20 * llm_requests
    assert usage.nemotron.usd == round(budget.service_usd()["nemotron"], 4) > 0
    assert usage.tavily.searches == len(h.tavily_transport.requests) == 1
    assert usage.tavily.usd == round(budget.service_usd()["tavily"], 4)
    assert usage.sandbox.operations == 0  # the fake executor counts none
    assert usage.osv.queries == report.summary.dependencies_checked > 0
    assert usage.trace_url is None


async def test_adr45_a_cached_advisory_is_not_a_tavily_search(tmp_path):
    h = traced_harness(tmp_path, tracing=False)
    await scan_canary(h)

    h2 = traced_harness(tmp_path, tracing=False)  # same data_dir: the research cache is warm
    report = await scan_canary(h2, "b" * 32)

    assert report.usage is not None
    assert report.usage.tavily.searches == 0
    assert report.usage.osv.queries > 0  # OSV queries count whether cached or not


async def test_adr45_sharing_is_off_by_default_so_trace_url_is_null(tmp_path):
    fake = ShareableLangSmithClient()
    h = traced_harness(tmp_path, langsmith=fake)

    report = await scan_canary(h)

    assert report.usage is not None and report.usage.trace_url is None
    assert fake.shared == []


async def test_adr45_sharing_on_puts_the_root_runs_public_link_on_the_report(tmp_path):
    fake = ShareableLangSmithClient()
    h = traced_harness(tmp_path, langsmith=fake, langsmith_share_traces=True)

    report = await scan_canary(h)

    assert report.usage is not None and report.usage.trace_url == SHARE_URL
    (root,) = fake.run_tree()
    assert [str(run_id) for run_id in fake.shared] == [root.id]  # the root run, once


async def test_adr45_sharing_needs_tracing_to_be_on(tmp_path):
    h = traced_harness(tmp_path, tracing=False, langsmith_share_traces=True)

    report = await scan_canary(h)

    assert report.usage is not None and report.usage.trace_url is None


async def test_adr45_a_scan_nested_under_an_eval_case_run_is_never_shared(tmp_path):
    fake = ShareableLangSmithClient()
    h = traced_harness(tmp_path, langsmith=fake, langsmith_share_traces=True)
    case = RunTree(name="pir.eval_case", run_type="chain", client=fake, project_name="p")
    budget = await h.reserve()

    report = await run_scan(
        h.deps,
        LocalFixtureTarget(path=str(CANARY), commit_sha=CANARY_SHA),
        budget,
        trace_parent=case,
    )
    await h.deps.llm.aclose()  # type: ignore[attr-defined]

    assert fake.shared == []
    assert report.usage is not None and report.usage.trace_url is None


@pytest.mark.parametrize(
    "error",
    [RuntimeError("share refused"), ConnectionError("down")],
    ids=["RuntimeError", "ConnectionError"],
)
async def test_adr45_a_share_error_gives_a_null_link_and_the_scan_still_succeeds(
    tmp_path, caplog, error
):
    fake = ShareableLangSmithClient(error=error)
    h = traced_harness(tmp_path, langsmith=fake, langsmith_share_traces=True)

    report = await scan_canary(h)

    assert report.outcome in ("findings", "clean", "incomplete")
    assert report.usage is not None and report.usage.trace_url is None
    warnings = [r.getMessage() for r in caplog.records if r.name == "pir.tracing"]
    assert any(type(error).__name__ in message for message in warnings)
    assert "share refused" not in "".join(warnings)  # the type only, not the message


async def test_adr45_a_slow_share_times_out_to_a_null_link(tmp_path, monkeypatch):
    monkeypatch.setattr(tracing, "SHARE_TIMEOUT_S", 0.05)
    fake = ShareableLangSmithClient(delay_s=0.4)
    h = traced_harness(tmp_path, langsmith=fake, langsmith_share_traces=True)

    report = await scan_canary(h)

    assert fake.shared  # it was tried
    assert report.usage is not None and report.usage.trace_url is None


@pytest.mark.parametrize(
    "url",
    [
        "http://smith.langchain.com/public/abc/r",  # not https
        "https://smith.langchain.com.evil.example/public/abc/r",  # look-alike host
        "https://evil.example/public/abc/r",
        "https://notsmith.langchain.com/public/abc/r",
        "https://smith.langchain.com/o/abc/projects",  # not a public path
        "https://smith.langchain.com/publicity/abc",
        "https://user:pw@smith.langchain.com/public/abc/r",
        "https://smith.langchain.com:8443/public/abc/r",
        "https://evil.com\\.smith.langchain.com/public/x",  # backslash in the netloc
        "https://smith.langchain.com%2eevil.com/public/x",  # percent in the netloc
        "https://a.b.smith.langchain.com/public/x",  # more than one label in front
        "https://smith.langchain.com /public/x",  # whitespace
        "https://smith.langchain.com/public/x\n",  # control character
        "https://smith.langchain.com/public/x?next=https://evil.example",  # query
        "javascript:alert(1)",
        "",
    ],
)
async def test_adr45_an_invalid_share_link_gives_a_null_link(tmp_path, url):
    fake = ShareableLangSmithClient(url=url)
    h = traced_harness(tmp_path, langsmith=fake, langsmith_share_traces=True)

    report = await scan_canary(h)

    assert fake.shared  # it was tried
    assert report.usage is not None and report.usage.trace_url is None


def test_adr45_valid_share_url_accepts_the_public_hosts():
    assert valid_share_url(SHARE_URL)
    assert valid_share_url("https://eu.smith.langchain.com/public/abc/r")
    assert not valid_share_url(None)


def test_adr45_the_client_omits_runtime_metadata_only_when_sharing_is_on():
    key = "ls-test-key"
    on = Settings(
        _env_file=None, langsmith_tracing=True, langsmith_api_key=key, langsmith_share_traces=True
    )
    off = Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key=key)
    shared_client = tracing.make_client(on)
    plain_client = tracing.make_client(off)
    assert shared_client is not None and plain_client is not None
    assert shared_client._omit_traced_runtime_info is True
    assert plain_client._omit_traced_runtime_info is False
    # one anonymizer covers inputs, outputs, errors and metadata; hide_* never reach errors
    assert shared_client._anonymizer is not None and plain_client._anonymizer is not None
    long_error = "ValueError('x')\n\nTraceback (most recent call last):\n  File x"
    assert shared_client._anonymizer({"error": long_error}) == {"error": "ValueError('x')"}
    assert plain_client._anonymizer({"error": long_error}) == {"error": long_error}


@pytest.fixture(autouse=True)
def _idle_share_worker():
    """A share that outlived its test must not make the next test's share look pending."""
    yield
    pending = tracing._pending
    if pending is not None:
        concurrent.futures.wait([pending], timeout=5)


async def test_adr45_a_timed_out_share_logs_the_run_id_so_it_can_be_unshared(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(tracing, "SHARE_TIMEOUT_S", 0.05)
    fake = ShareableLangSmithClient(delay_s=0.3)
    h = traced_harness(tmp_path, langsmith=fake, langsmith_share_traces=True)

    await scan_canary(h)

    (root,) = fake.run_tree()
    messages = [r.getMessage() for r in caplog.records if r.name == "pir.tracing"]
    assert any("timed out" in m and root.id in m for m in messages)


async def test_adr45_a_share_is_skipped_while_the_previous_one_is_still_running(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(tracing, "SHARE_TIMEOUT_S", 0.05)
    release = threading.Event()
    fake = ShareableLangSmithClient(hold=release)
    first = traced_harness(tmp_path / "one", langsmith=fake, langsmith_share_traces=True)
    second = traced_harness(tmp_path / "two", langsmith=fake, langsmith_share_traces=True)
    try:
        report_one = await scan_canary(first)
        report_two = await scan_canary(second, "b" * 32)
    finally:
        release.set()

    assert report_one.usage is not None and report_one.usage.trace_url is None
    assert report_two.usage is not None and report_two.usage.trace_url is None
    assert len(fake.shared) == 1  # the second scan never reached the client
    assert any("skipped" in r.getMessage() for r in caplog.records if r.name == "pir.tracing")


async def test_adr45_flush_and_share_do_not_run_on_the_default_executor(tmp_path):
    names: list[str] = []

    class Spy(ShareableLangSmithClient):
        def share_run(self, run_id, *, share_id=None):  # type: ignore[no-untyped-def,override]
            names.append(threading.current_thread().name)
            return super().share_run(run_id, share_id=share_id)

    h = traced_harness(tmp_path, langsmith=Spy(), langsmith_share_traces=True)

    report = await scan_canary(h)

    assert report.usage is not None and report.usage.trace_url == SHARE_URL
    assert names and all(name.startswith("pir-share") for name in names)


async def test_adr45_a_failing_run_uploads_no_secret_and_no_traceback_when_sharing(tmp_path):
    secret = "SENTINEL-share-error-5c1d"
    register_secrets({secret})
    try:
        settings = Settings(_env_file=None, langsmith_share_traces=True)

        @traceable(name="pir.tool.boom", run_type="tool")
        def boom() -> None:
            raise RuntimeError(f"Authorization: Bearer {secret}\nrequest failed with {secret}")

        async def body() -> str:
            try:
                boom()
            except RuntimeError:
                pass
            return "done"

        shared = ShareableLangSmithClient()
        plain = FakeLangSmithClient()
        await run_traced(settings, "e" * 32, body, client=shared)
        await run_traced(settings, "f" * 32, body, client=plain)
    finally:
        clear_secrets()

    errors = [p["error"] for p in shared.payloads if p.get("error")]
    assert errors, "the failing child run should have uploaded an error"
    for error in errors:
        assert secret not in error
        assert "Traceback" not in error and "\n" not in error
    # the same failure on a client that is not sharing keeps the traceback (redacted)
    kept = [p["error"] for p in plain.payloads if p.get("error")]
    assert any("Traceback" in error for error in kept)
    assert all(secret not in error for error in kept)
    assert secret not in shared.text() and secret not in plain.text()
