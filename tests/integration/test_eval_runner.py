"""The evaluation runner with fakes: FakeExecutor on local fixtures, FakeLLM, fake LangSmith.

AC-34 (printed and saved metrics, limitations), AC-35 (through the runner), AC-36 (exit codes),
AC-37 (LangSmith dataset, experiment, traces, feedback, read-back), AC-38 (errored case isolation),
AC-39 (latest.json and the figure), recorded OSV and Tavily answers.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from fakes.deps import (
    GHSA,
    OTHER_GHSA,
    RESEARCH_REPLY,
    VERDICT,
    Harness,
    capture_json_logs,
    log_lines,
    make_harness,
    osv_routes,
    tavily_routes,
    write_repo,
)
from fakes.http import RecordingTransport
from fakes.langsmith import FakeLangSmithClient
from fakes.llm import Call, FakeLLM, json_result, tool_result

from pir.cli import main
from pir.evaluation import runner
from pir.evaluation.cases import load_cases
from pir.evaluation.metrics import load_eval_figure
from pir.evaluation.runner import (
    CASE_RUN_NAME,
    RecordReplayTransport,
    experiment_name,
    normalize_package,
    run_eval,
    run_eval_command,
)
from pir.github import RepoNotFound
from pir.models.eval import EvalRunSummary

LR = "likely reachable"
NP = "no reachable path detected"
UNC = "uncertain"
NOW = datetime(2026, 10, 27, 8, 30, 15, tzinfo=UTC)
STAMP = "20261027T083015Z"


class FrozenClock:
    def now(self) -> datetime:
        return NOW


def llm_always_reachable() -> FakeLLM:
    """Research finds yaml.full_load; reasoning calls find_call_sites then submits the verdict."""

    def step(call: Call):
        if call.json_mode:
            return json_result(RESEARCH_REPLY)
        if any(m.role == "tool" for m in call.messages):
            return tool_result("submit_verdict", VERDICT)
        return tool_result("find_call_sites", {"symbol": "yaml.full_load"})

    return FakeLLM([step])


class EvalFakeLangSmith(FakeLangSmithClient):
    """The fake client with the dataset, example, project, run and feedback calls recorded."""

    def __init__(self, fail_feedback: bool = False) -> None:
        super().__init__()
        self.got_datasets: dict[str, SimpleNamespace] = {}
        self.got_examples: list[SimpleNamespace] = []
        self.got_projects: list[dict[str, Any]] = []
        self.got_updated_projects: list[dict[str, Any]] = []
        self.got_case_runs: list[dict[str, Any]] = []
        self.got_other_runs: list[str] = []
        self.got_other_run_info: list[dict[str, Any]] = []
        self.got_case_updates: list[dict[str, Any]] = []
        self.got_feedback: list[dict[str, Any]] = []
        self.fail_feedback = fail_feedback
        self.visible_after_queries = 1  # the query call number from which case runs are listed
        self.fail_query = False

    def has_dataset(self, *, dataset_name=None, dataset_id=None) -> bool:
        return dataset_name in self.got_datasets

    def read_dataset(self, *, dataset_name=None, dataset_id=None, **kw):
        return self.got_datasets[dataset_name]

    def create_dataset(self, dataset_name, **kw):
        self.got_datasets[dataset_name] = SimpleNamespace(
            id=f"ds-{len(self.got_datasets)}", name=dataset_name
        )
        return self.got_datasets[dataset_name]

    def list_examples(self, dataset_id=None, **kw):
        return list(self.got_examples)

    def create_examples(self, *, dataset_id=None, examples=None, **kw):
        for item in examples or []:
            self.got_examples.append(
                SimpleNamespace(
                    id=str(uuid.UUID(int=len(self.got_examples) + 1)),  # real ids are UUIDs
                    inputs=item["inputs"],
                    outputs=item["outputs"],
                    metadata=item["metadata"],
                )
            )

    def update_example(self, example_id, **kw):
        for example in self.got_examples:
            if example.id == example_id:
                example.inputs, example.outputs = kw["inputs"], kw["outputs"]

    def create_project(self, project_name, **kw):
        self.got_projects.append({"name": project_name, **kw})
        return SimpleNamespace(id="proj-1", name=project_name)

    def update_project(self, project_id, **kw):
        self.got_updated_projects.append({"id": project_id, **kw})

    def create_run(self, name, inputs, run_type, **kw):
        if name == CASE_RUN_NAME:
            self.got_case_runs.append({"name": name, "inputs": inputs, "run_type": run_type, **kw})
        else:
            self.got_other_runs.append(name)  # the pipeline's own scan traces
            parent = kw.get("parent_run_id")
            self.got_other_run_info.append(
                {
                    "name": name,
                    "id": str(kw.get("id")),
                    "parent_run_id": str(parent) if parent else None,
                    "trace_id": str(kw.get("trace_id")),
                }
            )

    def update_run(self, run_id, **kw):
        known = {str(r["id"]) for r in self.got_case_runs}
        known |= {i["id"] for i in self.got_other_run_info}
        if str(run_id) in known:
            self.got_case_updates.append({"run_id": str(run_id), **kw})

    def create_feedback(self, run_id, key="unnamed", **kw):
        if self.fail_feedback:
            raise RuntimeError("feedback rejected")
        self.got_feedback.append({"run_id": run_id, "key": key, **kw})

    def list_runs(self, *args, **kwargs):
        raise AssertionError("list_runs is banned: use client.runs.query()")

    def stored_runs(self, query):
        if self.fail_query:
            raise RuntimeError("query rejected")
        if len(self.query_calls) < self.visible_after_queries:
            return []  # ingestion has not caught up yet
        rows = [{"id": str(r["id"]), "name": r["name"]} for r in self.got_case_runs]
        rows += [
            {"id": i["id"], "name": i["name"]}
            for i in self.got_other_run_info
            if i["parent_run_id"] is None
        ]
        return rows


def make_suite(tmp_path: Path, cases: list[tuple[str, str, str, str]]):
    """cases: (case id, repo id, advisory, label). Every repo is the one-file PyYAML fixture."""
    root = tmp_path / "project"
    (root / "eval").mkdir(parents=True)
    repos = sorted({repo for _, repo, _, _ in cases})
    lines = []
    for repo in repos:
        write_repo(root / "eval" / "fixtures" / repo)
        lines.append(f'[[repo]]\nid = "{repo}"\nsource = "local:eval/fixtures/{repo}"\n')
    for cid, repo, advisory, label in cases:
        lines.append(
            f'[[case]]\nid = "{cid}"\nrepo = "{repo}"\npackage = "PyYAML"\n'
            f'advisory = "{advisory}"\nlabel = "{label}"\nrationale = "test"\n'
        )
    (root / "eval" / "cases.toml").write_text("\n".join(lines), encoding="utf-8")
    return load_cases(root / "eval" / "cases.toml")


def harness(tmp_path: Path, **kw) -> Harness:
    h = make_harness(tmp_path / "h", llm=kw.pop("llm", llm_always_reachable()), **kw)
    h.deps.clock = FrozenClock()
    return h


def as_sandbox(h: Harness) -> None:
    """The runner only reads `executor.kind` to tell a real Sandbox run from a fake one."""
    h.deps.executor = SimpleNamespace(kind="sandbox")


CASES = [
    ("c-tp", "repo-a", GHSA, LR),  # predicted LR, labelled LR: true positive
    ("c-fp", "repo-b", GHSA, NP),  # predicted LR, labelled no-path: false positive
    ("c-err", "repo-c", GHSA, LR),  # its repo errors: scored as uncertain
    ("c-miss", "repo-a", OTHER_GHSA, UNC),  # no such finding: scored as uncertain
]


async def test_ac34_ac35_ac38_metrics_limitations_and_an_errored_repo(tmp_path, monkeypatch):
    suite = make_suite(tmp_path, CASES)
    h = harness(tmp_path)
    real_run_scan = runner.run_scan

    async def flaky(deps, target, budget, **kw):
        if "repo-c" in target.path:
            raise RuntimeError("the scan blew up")
        return await real_run_scan(deps, target, budget, **kw)

    monkeypatch.setattr("pir.evaluation.runner.run_scan", flaky)
    results = tmp_path / "results"

    run = await run_eval(suite, h.deps, client=None, results_dir=results)

    by_id = {r.case.id: r for r in run.results}
    assert {i: r.status for i, r in by_id.items()} == {
        "c-tp": "scored",
        "c-fp": "scored",
        "c-err": "errored",
        "c-miss": "missing",
    }
    assert by_id["c-tp"].predicted == LR and by_id["c-fp"].predicted == LR
    assert by_id["c-err"].predicted is None and "RuntimeError" in (by_id["c-err"].detail or "")
    assert by_id["c-err"].scored_as == UNC
    m = run.metrics
    assert (m.n, m.precision, m.recall) == (4, 0.5, 0.5)
    assert m.abstention_rate == 0.5 and m.correct_abstentions == 1
    assert m.errored == ["c-err", "c-miss"]
    assert run.summary.errored == 2  # AC-38: the remaining repos still ran
    # AC-34: printed text and saved files carry the figures and the limitations statement
    for needle in ("precision 0.50", "N (cases) | 4", "Abstention rate | 0.50", "recall"):
        assert needle.lower() in run.markdown.lower()
    assert "Errored cases | 2" in run.markdown
    assert "c-err: RuntimeError" in run.markdown
    for needle in (
        "4 labelled cases",
        "3 repositories",
        "synthetic",
        "recorded",
        "Single model",
        "temperature 0",
    ):
        assert needle in run.markdown
    saved = json.loads((results / f"{STAMP}.json").read_text(encoding="utf-8"))
    assert saved["metrics"]["precision"] == 0.5 and saved["summary"]["errored"] == 2
    assert (results / f"{STAMP}.md").read_text(encoding="utf-8") == run.markdown
    assert "Limitations:" in run.markdown


async def test_ac38_a_repo_that_cannot_be_scanned_never_stops_the_others(tmp_path):
    suite = make_suite(tmp_path, [("ok", "repo-a", GHSA, LR)])
    path = suite.root / "eval" / "cases.toml"
    github = chr(10).join(
        [
            "[[repo]]",
            'id = "gh"',
            f'source = "github:octo/demo@{"a" * 40}"',
            "",
            "[[case]]",
            'id = "bad"',
            'repo = "gh"',
            'package = "PyYAML"',
            f'advisory = "{GHSA}"',
            'label = "likely reachable"',
            'rationale = "test"',
            "",
            "",
        ]
    )
    path.write_text(github + path.read_text(encoding="utf-8"), encoding="utf-8")
    h = harness(tmp_path)

    async def not_found(*_args):
        raise RepoNotFound("gone")

    h.deps.validate = not_found

    run = await run_eval(load_cases(path), h.deps, client=None, results_dir=tmp_path / "r")

    statuses = {r.case.id: r.status for r in run.results}
    assert statuses == {"bad": "errored", "ok": "scored"}  # the failing repo ran first
    assert run.metrics.errored == ["bad"]


async def test_ac38_each_case_logs_with_the_scan_id_of_its_scan(tmp_path):
    suite = make_suite(tmp_path, [("a1", "repo-a", GHSA, LR), ("b1", "repo-b", GHSA, LR)])
    h = harness(tmp_path)

    with capture_json_logs() as buffer:
        run = await run_eval(suite, h.deps, client=None, results_dir=tmp_path / "r")

    lines = [x for x in log_lines(buffer) if x.get("event") == "eval_case"]
    assert len(lines) == 2
    ids = {r.case.id: r.scan_id for r in run.results}
    assert ids["a1"] != ids["b1"]
    assert [x["scan_id"] for x in lines] == [ids["a1"], ids["b1"]]


# ---- AC-36: exit codes and the published files ------------------------------------------------


async def test_ac36_exit_0_when_the_bar_is_met_and_latest_files_are_written(tmp_path):
    suite = make_suite(tmp_path, [("c1", "repo-a", GHSA, LR)])
    h = harness(tmp_path)
    as_sandbox(h)
    client = EvalFakeLangSmith()
    results = tmp_path / "results"

    run = await run_eval(suite, h.deps, client=client, results_dir=results)

    assert run.summary.scored and run.summary.bar_met and run.summary.precision == 1.0
    assert run.exit_code == 0
    published = EvalRunSummary.model_validate_json((results / "latest.json").read_text("utf-8"))
    assert published == run.summary
    assert (results / "latest.md").read_text(encoding="utf-8").startswith("# Evaluation: PASS")
    figure = load_eval_figure(results / "latest.json")  # AC-39
    assert figure is not None and figure.precision == 1.0 and figure.n == 1


async def test_ac36_exit_1_when_the_bar_is_not_met(tmp_path):
    suite = make_suite(tmp_path, [("c1", "repo-b", GHSA, NP)])
    h = harness(tmp_path)
    as_sandbox(h)

    run = await run_eval(suite, h.deps, client=EvalFakeLangSmith(), results_dir=tmp_path / "r")

    assert run.summary.scored and not run.summary.bar_met and run.summary.precision == 0.0
    assert run.exit_code == 1
    assert run.markdown.startswith("# Evaluation: FAIL")


async def test_ac36_no_lr_prediction_does_not_meet_the_bar(tmp_path):
    suite = make_suite(tmp_path, [("c1", "repo-a", GHSA, LR)])
    h = harness(
        tmp_path,
        llm=FakeLLM(
            [
                json_result(
                    {**RESEARCH_REPLY, "identified": False, "symbols": [], "evidence_urls": []}
                )
            ]
        ),
    )
    as_sandbox(h)

    run = await run_eval(suite, h.deps, client=EvalFakeLangSmith(), results_dir=tmp_path / "r")

    assert run.summary.precision is None
    assert (run.summary.scored, run.summary.bar_met, run.exit_code) == (True, False, 1)


async def test_ac36_exit_2_and_scored_false_without_langsmith(tmp_path):
    suite = make_suite(tmp_path, [("c1", "repo-a", GHSA, LR)])
    h = harness(tmp_path)
    as_sandbox(h)
    results = tmp_path / "results"

    run = await run_eval(suite, h.deps, client=None, results_dir=results)

    assert not run.summary.scored and run.exit_code == 2
    assert run.summary.experiment is None and not run.summary.bar_met
    assert "--no-langsmith" in run.markdown
    written = EvalRunSummary.model_validate_json((results / "latest.json").read_text("utf-8"))
    assert written.scored is False  # no figure is published for it
    assert load_eval_figure(results / "latest.json") is None


async def test_ac36_a_fake_executor_run_is_never_scored(tmp_path):
    suite = make_suite(tmp_path, [("c1", "repo-a", GHSA, LR)])
    h = harness(tmp_path)

    run = await run_eval(suite, h.deps, client=EvalFakeLangSmith(), results_dir=tmp_path / "r")

    assert not run.summary.scored and run.exit_code == 2
    assert "executor is fake" in run.markdown


async def test_ac39_an_unscored_run_keeps_an_already_published_scored_latest(tmp_path):
    suite = make_suite(tmp_path, [("c1", "repo-a", GHSA, LR)])
    results = tmp_path / "results"
    h = harness(tmp_path / "one")
    as_sandbox(h)
    first = await run_eval(suite, h.deps, client=EvalFakeLangSmith(), results_dir=results)
    before = (results / "latest.json").read_text(encoding="utf-8")

    second = await run_eval(suite, harness(tmp_path / "two").deps, client=None, results_dir=results)

    assert first.summary.scored and not second.summary.scored
    assert (results / "latest.json").read_text(encoding="utf-8") == before
    assert (results / f"{STAMP}.json").exists()


async def test_ac36_the_sandbox_selfcheck_failure_stops_the_run_unscored(tmp_path, monkeypatch):
    suite = make_suite(tmp_path, [("c1", "repo-a", GHSA, LR), ("c2", "repo-b", GHSA, LR)])
    h = harness(tmp_path)
    as_sandbox(h)
    calls = []

    async def selfcheck_failed(deps, target, budget, **kw):
        calls.append(target.path)
        report = await real(deps, target, budget, **kw)
        return report.model_copy(
            update={
                "limitations": [*report.limitations, "sandbox unavailable: base image check failed"]
            }
        )

    real = runner.run_scan
    monkeypatch.setattr(runner, "run_scan", selfcheck_failed)
    run = await run_eval(suite, h.deps, client=EvalFakeLangSmith(), results_dir=tmp_path / "r")

    assert len(calls) == 1  # stopped before the second repo
    assert run.aborted and "ADR-0033" in run.aborted
    assert run.exit_code == 2 and not run.summary.scored
    assert not (tmp_path / "r" / "latest.json").exists()


# ---- AC-37: LangSmith -------------------------------------------------------------------------


async def test_ac37_dataset_experiment_traces_feedback_and_read_back(tmp_path):
    suite = make_suite(tmp_path, CASES[:2])
    h = harness(tmp_path)
    as_sandbox(h)
    client = EvalFakeLangSmith()

    run = await run_eval(suite, h.deps, client=client, results_dir=tmp_path / "r")

    assert list(client.got_datasets) == [h.settings.langsmith_dataset]
    assert [e.metadata["case_id"] for e in client.got_examples] == ["c-tp", "c-fp"]  # one per case
    first = client.got_examples[0]
    assert first.inputs == {
        "repo": "local:eval/fixtures/repo-a",
        "package": "PyYAML",
        "advisory": GHSA,
    }
    assert first.outputs == {"label": LR}
    assert len(client.got_projects) == 1  # one experiment
    experiment = client.got_projects[0]
    assert experiment["name"] == experiment_name(NOW, h.settings.nebius_model)
    assert experiment["name"] == f"eval-{STAMP}-nvidia-nemotron-3-super-120b-a12b"
    assert experiment["reference_dataset_id"] == "ds-0"
    assert run.summary.experiment == experiment["name"]
    # one root trace per case, linked to its example, each with feedback "correct"
    assert [r["inputs"]["case_id"] for r in client.got_case_runs] == ["c-tp", "c-fp"]
    assert [str(r["reference_example_id"]) for r in client.got_case_runs] == [
        e.id for e in client.got_examples
    ]
    assert all(r["session_name"] == experiment["name"] for r in client.got_case_runs)
    assert [(f["key"], f["score"]) for f in client.got_feedback] == [("correct", 1), ("correct", 0)]
    assert [f["run_id"] for f in client.got_feedback] == [r["id"] for r in client.got_case_runs]
    scan_ids = {r.case.id: r.scan_id for r in run.results}
    first_update = next(
        u for u in client.got_case_updates if u["run_id"] == str(client.got_case_runs[0]["id"])
    )
    assert first_update["extra"]["metadata"]["scan_id"] == scan_ids["c-tp"]
    assert first_update["extra"]["metadata"]["case_id"] == "c-tp"
    assert first_update["outputs"]["predicted"] == LR
    # the feedback names the experiment project (no session_id deprecation warning)
    assert all(f["project_id"] == "proj-1" for f in client.got_feedback)
    # the pipeline's own scan traces go to the same experiment project
    assert client.got_other_runs, "the scans were traced"
    # metrics are the experiment metadata
    assert client.got_updated_projects[0]["metadata"]["precision"] == 0.5
    assert client.got_updated_projects[0]["metadata"]["n"] == 2
    # read back with runs.query, never list_runs
    assert client.query_calls == [
        {"project_ids": ["proj-1"], "is_root": True, "selects": ["ID", "NAME"]}
    ]
    assert run.readback == 2
    assert "LangSmith read-back: 2 of 2" in run.markdown


async def test_ac37_a_second_run_updates_the_dataset_instead_of_duplicating_it(tmp_path):
    suite = make_suite(tmp_path, CASES[:2])
    client = EvalFakeLangSmith()
    for name in ("one", "two"):
        h = harness(tmp_path / name)
        as_sandbox(h)
        await run_eval(suite, h.deps, client=client, results_dir=tmp_path / name / "r")
        client.got_case_runs.clear()

    assert len(client.got_datasets) == 1
    assert len(client.got_examples) == 2  # not 4
    assert len(client.got_projects) == 2  # a new experiment per run


async def test_ac37_a_failing_feedback_call_makes_the_run_unscored(tmp_path):
    suite = make_suite(tmp_path, CASES[:1])
    h = harness(tmp_path)
    as_sandbox(h)

    run = await run_eval(
        suite, h.deps, client=EvalFakeLangSmith(fail_feedback=True), results_dir=tmp_path / "r"
    )

    assert not run.summary.scored and run.exit_code == 2
    assert "LangSmith publishing failed for 1 case" in run.markdown


def test_ac37_the_runner_never_uses_list_runs():
    source = Path(__file__).resolve().parents[2] / "src" / "pir" / "evaluation" / "runner.py"
    text = source.read_text(encoding="utf-8")
    assert "runs.query(" in text and "list_runs" not in text
    pyproject = (source.parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    assert "langsmith.Client.list_runs" in pyproject  # ruff banned-api


# ---- recorded OSV and Tavily answers ----------------------------------------------------------


async def post(transport: httpx.AsyncBaseTransport, url: str, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport) as client:
        return await client.post(url, json=payload, headers={"Authorization": "Bearer secret-key"})


async def test_recordings_are_replayed_without_the_network_and_hold_no_request_header(tmp_path):
    inner = httpx.MockTransport(lambda r: httpx.Response(200, json={"echo": json.loads(r.content)}))
    recorder = RecordReplayTransport(tmp_path / "rec", record=True, inner=inner)

    live = await post(recorder, "https://api.tavily.com/search", {"query": "q", "b": 1})

    assert recorder.recorded == 1
    stored = (tmp_path / "rec").glob("*.json")
    text = "".join(p.read_text(encoding="utf-8") for p in stored)
    assert "secret-key" not in text and "Authorization" not in text
    replayer = RecordReplayTransport(tmp_path / "rec")
    again = await post(replayer, "https://api.tavily.com/search", {"b": 1, "query": "q"})
    assert (
        again.json() == live.json() == {"echo": {"query": "q", "b": 1}}
    )  # key order is irrelevant
    assert (replayer.hits, replayer.misses) == (1, [])


async def test_a_request_without_a_recording_is_a_404_and_a_counted_miss(tmp_path):
    replayer = RecordReplayTransport(tmp_path / "empty")

    response = await post(replayer, "https://api.osv.dev/v1/querybatch", {"queries": []})

    assert response.status_code == 404
    assert len(replayer.misses) == 1 and replayer.hits == 0


async def test_only_successful_responses_are_recorded(tmp_path):
    inner = httpx.MockTransport(lambda r: httpx.Response(500, json={"error": "x"}))
    recorder = RecordReplayTransport(tmp_path / "rec", record=True, inner=inner)

    response = await post(recorder, "https://api.tavily.com/search", {"query": "q"})

    assert response.status_code == 500 and recorder.recorded == 0
    assert not list((tmp_path / "rec").glob("*.json"))


async def test_missing_recordings_make_the_run_unscored(tmp_path):
    suite = make_suite(tmp_path, [("c1", "repo-a", GHSA, LR)])
    h = harness(tmp_path)
    as_sandbox(h)
    missing = RecordReplayTransport(tmp_path / "none")
    await post(missing, "https://api.osv.dev/v1/querybatch", {"queries": []})

    run = await run_eval(
        suite, h.deps, client=EvalFakeLangSmith(), results_dir=tmp_path / "r", recordings=[missing]
    )

    assert not run.summary.scored and run.exit_code == 2
    assert "1 recorded OSV or Tavily request(s) missing" in run.markdown


# ---- the eval command ---------------------------------------------------------------------------


def command_env(tmp_path, monkeypatch, h: Harness) -> Path:
    suite = make_suite(tmp_path, [("c1", "repo-a", GHSA, LR)])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pir.evaluation.runner.build_deps", lambda *a, **kw: h.deps)
    return suite.root / "eval" / "cases.toml"


async def run_command(settings, cases: Path, **kw):
    return await run_eval_command(
        settings,
        cases_path=cases,
        refresh=kw.get("refresh", False),
        use_langsmith=kw.get("use_langsmith", False),
        results_dir=cases.parent / "results",
        recordings_dir=cases.parent / "recordings",
        record_inner=kw.get("record_inner"),
    )


async def test_the_command_records_then_replays_osv_and_tavily(tmp_path, monkeypatch):
    first = harness(tmp_path / "one")
    cases = command_env(tmp_path, monkeypatch, first)
    inner = {"osv": RecordingTransport(osv_routes()), "tavily": RecordingTransport(tavily_routes())}

    recorded = await run_command(first.settings, cases, refresh=True, record_inner=inner)

    osv_files = list((cases.parent / "recordings" / "osv").glob("*.json"))
    tavily_files = list((cases.parent / "recordings" / "tavily").glob("*.json"))
    assert osv_files and tavily_files
    assert "re-recorded live" in recorded.text and "Spend:" in recorded.text
    assert "precision 1.00" in recorded.text.lower()

    second = harness(tmp_path / "two", osv={}, tavily={})  # any live request would fail the test
    monkeypatch.setattr("pir.evaluation.runner.build_deps", lambda *a, **kw: second.deps)
    replayed = await run_command(second.settings, cases)

    assert "replayed" in replayed.text
    assert "precision 1.00" in replayed.text.lower()
    assert "missing" not in replayed.text.lower()
    # --no-langsmith: not scored, so the exit code is 2 and the PASS/FAIL line is still printed
    assert replayed.exit_code == 2
    assert "UNSCORED" in replayed.text and "PASS: LR precision 1.00" not in replayed.text
    assert "FAIL: LR precision 1.00" in replayed.text  # bar_met is False for an unscored run


async def test_the_command_could_not_run_without_recordings(tmp_path, monkeypatch):
    h = harness(tmp_path)
    cases = command_env(tmp_path, monkeypatch, h)

    result = await run_command(h.settings, cases)

    assert result.exit_code == 2
    assert "--refresh-research" in result.text


async def test_the_command_could_not_run_with_a_bad_cases_file(tmp_path, monkeypatch):
    h = harness(tmp_path)
    cases = command_env(tmp_path, monkeypatch, h)
    cases.write_text("[[case]]\nid = 'x'\n", encoding="utf-8")

    result = await run_command(h.settings, cases)

    assert result.exit_code == 2 and "Could not run the evaluation" in result.text


async def test_the_command_needs_the_langsmith_key_unless_no_langsmith(tmp_path, monkeypatch):
    h = harness(tmp_path)
    cases = command_env(tmp_path, monkeypatch, h)

    result = await run_command(h.settings, cases, use_langsmith=True)

    assert result.exit_code == 2 and "LANGSMITH_API_KEY" in result.text


def test_the_cli_exits_2_when_it_cannot_run(tmp_path, monkeypatch, capsys):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    monkeypatch.chdir(tmp_path)
    for name in ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID", "TAVILY_API_KEY", "LANGSMITH_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    code = main(["eval", "--cases", str(tmp_path / "absent" / "cases.toml"), "--no-langsmith"])

    root.handlers[:] = handlers
    root.setLevel(level)
    assert code == 2
    assert "Could not run the evaluation" in capsys.readouterr().out


def test_package_names_are_compared_in_normalized_form():
    assert normalize_package("PyYAML") == normalize_package("pyyaml") == "pyyaml"
    assert normalize_package("Foo_Bar.baz") == "foo-bar-baz"


# ---- M1-FIX-02: read-back, trace tree, unscored reasons ---------------------------------------


class FakeSleeper:
    """Injectable clock and sleep for the read-back poll: sleeping advances the clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def with_sleeper(monkeypatch, sleeper: FakeSleeper) -> None:
    real_init = runner.ExperimentPublisher.__init__

    def init(self, *args, **kw):
        real_init(self, *args, sleep=sleeper.sleep, monotonic=sleeper.monotonic, **kw)

    monkeypatch.setattr(runner.ExperimentPublisher, "__init__", init)


async def test_ac37_read_back_requests_names_and_the_fake_hides_them_otherwise():
    # the fake returns ids only unless `selects` names more, as the real API does
    client = EvalFakeLangSmith()
    client.got_case_runs.append({"id": "r1", "name": CASE_RUN_NAME})
    publisher = runner.ExperimentPublisher(client, "ds", "exp", "m", 0.8)
    publisher.project_id = "proj-1"
    publisher.case_runs = {"c": "r1"}  # type: ignore[dict-item]

    assert await publisher.read_back(1) == 1
    assert "NAME" in client.query_calls[0]["selects"]

    rows = [r async for r in client.runs.query(project_ids=["proj-1"], is_root=True)]
    assert rows and not hasattr(rows[0], "name")  # no selects: ids only


async def test_ac37_read_back_polls_until_the_case_traces_are_visible(tmp_path, monkeypatch):
    suite = make_suite(tmp_path, CASES[:2])
    h = harness(tmp_path)
    as_sandbox(h)
    client = EvalFakeLangSmith()
    client.visible_after_queries = 3  # listed from the third query on
    sleeper = FakeSleeper()
    with_sleeper(monkeypatch, sleeper)

    run = await run_eval(suite, h.deps, client=client, results_dir=tmp_path / "r")

    assert run.readback == 2 and run.summary.scored
    assert len(client.query_calls) == 3 and len(sleeper.slept) == 2


async def test_ac37_a_short_read_back_after_the_wait_makes_the_run_unscored(tmp_path, monkeypatch):
    suite = make_suite(tmp_path, CASES[:2])
    h = harness(tmp_path)
    as_sandbox(h)
    client = EvalFakeLangSmith()
    client.visible_after_queries = 10**6  # never visible
    sleeper = FakeSleeper()
    with_sleeper(monkeypatch, sleeper)
    results = tmp_path / "r"
    seed = harness(tmp_path / "seed")  # a scored latest.json from an earlier run must survive
    as_sandbox(seed)
    first = await run_eval(suite, seed.deps, client=EvalFakeLangSmith(), results_dir=results)
    assert first.summary.scored
    before = (results / "latest.json").read_text(encoding="utf-8")

    run = await run_eval(suite, h.deps, client=client, results_dir=results)

    assert run.readback == 0
    assert not run.summary.scored and run.exit_code == 2 and not run.summary.bar_met
    assert any("read-back found 0 of 2" in reason for reason in run.unscored_reasons)
    assert sum(sleeper.slept) <= runner.READBACK_TIMEOUT_S + runner.READBACK_INTERVAL_S
    assert (results / "latest.json").read_text(encoding="utf-8") == before


async def test_ac37_a_failing_read_back_query_makes_the_run_unscored(tmp_path):
    suite = make_suite(tmp_path, CASES[:1])
    h = harness(tmp_path)
    as_sandbox(h)
    client = EvalFakeLangSmith()
    client.fail_query = True

    run = await run_eval(suite, h.deps, client=client, results_dir=tmp_path / "r")

    assert run.readback is None and not run.summary.scored and run.exit_code == 2
    assert any("read-back failed" in reason for reason in run.unscored_reasons)


async def test_ac37_each_case_root_trace_has_children_and_holds_or_names_the_scan(tmp_path):
    # c-tp and c-miss share repo-a; c-fp is repo-b
    suite = make_suite(tmp_path, [CASES[0], CASES[1], CASES[3]])
    h = harness(tmp_path)
    as_sandbox(h)
    client = EvalFakeLangSmith()

    run = await run_eval(suite, h.deps, client=client, results_dir=tmp_path / "r")

    assert run.readback == 3
    roots = {r["inputs"]["case_id"]: str(r["id"]) for r in client.got_case_runs}
    assert set(roots) == {"c-tp", "c-fp", "c-miss"}
    # the scan of each repo is a child run of the first case of that repo, in the same trace
    scans = [i for i in client.got_other_run_info if i["name"] == "pir.scan"]
    assert sorted(i["parent_run_id"] for i in scans) == sorted([roots["c-tp"], roots["c-fp"]])
    for info in scans:
        assert info["trace_id"] == info["parent_run_id"]  # same trace as the case root
    assert {i["trace_id"] for i in client.got_other_run_info} <= set(roots.values())
    # every case root has the child run pir.eval_score; the other case names the scan's root
    scores = [i for i in client.got_other_run_info if i["name"] == runner.SCORE_RUN_NAME]
    assert sorted(i["parent_run_id"] for i in scores) == sorted(roots.values())
    miss_score = next(i for i in scores if i["parent_run_id"] == roots["c-miss"])
    update = next(u for u in client.got_case_updates if u["run_id"] == miss_score["id"])
    assert update["outputs"]["scan_trace_id"] == roots["c-tp"]
