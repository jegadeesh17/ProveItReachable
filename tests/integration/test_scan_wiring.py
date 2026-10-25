"""Scan wiring through the real adapters: CLI -> build_deps -> OSV, Tavily, Nebius clients ->
pipeline -> verdict gate -> report -> schemas/report.schema.json, and the file spend ledger;
and the eval command's record and replay of OSV and Tavily answers.

Only the network edge is faked (tests/fakes/edge.py answers the HTTP calls) and the executor is
the FakeExecutor that `--executor fake` selects. Everything else is the production code. Each
test checks the report against the published JSON Schema with tests/fakes/schema_subset.py.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fakes.deps import APP, GHSA, NVD_URL, VERDICT, write_repo
from fakes.edge import NEBIUS_HOST, OSV_HOST, TAVILY_HOST, NetworkEdge
from fakes.schema_subset import validate_document

from pir import messages
from pir.cli import main
from pir.config import Settings
from pir.models.report import ScanReport

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "report.schema.json").read_text(encoding="utf-8"))
STEPS = ["resolve", "osv", "research", "analysis", "reasoning", "report"]
SECRETS = {
    "NEBIUS_API_KEY": "sk-sentinel-nebius-9d41",
    "TAVILY_API_KEY": "tvly-sentinel-9d41",
}
# A bare Pipfile has no lock, so its packages are never resolved: no compile, no OSV query.
BARE_PIPFILE = '[packages]\nrequests = "*"\n'


@pytest.fixture(autouse=True)
def cli_env(tmp_path, monkeypatch):
    """Run in an empty directory (no .env), with sentinel keys and a file ledger under tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "project-test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPEND_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    # Production back-off between retries is half a second; the wiring tests do not wait for it.
    for module in ("pir.osv", "pir.research", "pir.llm"):
        monkeypatch.setattr(f"{module}._BACKOFF_S", 0)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def edge(monkeypatch) -> NetworkEdge:
    network = NetworkEdge()
    network.install(monkeypatch)
    return network


def scan(*args: str) -> int:
    return main(["scan", "--executor", "fake", *args])


def report_of(path: Path) -> dict[str, Any]:
    """The report must validate against the published schema and the strict model."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert validate_document(data, SCHEMA) == []
    ScanReport.model_validate_json(text)
    return data


def ledger_of(tmp_path: Path) -> dict[str, Any]:
    """The ledger file is {"revision": n, "state": {...}}; this returns the state."""
    return json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))["state"]


def nebius_spent(tmp_path: Path) -> float:
    return ledger_of(tmp_path)["spenders"]["local"]["pools"]["nebius"]["spent_usd"]


def only_finding(data: dict[str, Any]) -> dict[str, Any]:
    [finding] = data["findings"]
    return finding


# ---- the happy path -----------------------------------------------------------------------


@pytest.mark.slow
def test_a_scan_through_the_real_wiring_writes_a_schema_valid_report(tmp_path, edge, capsys):
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    captured = capsys.readouterr()
    data = report_of(out)
    assert edge.unexpected == []
    assert [step["name"] for step in data["steps"]] == STEPS
    assert data["outcome"] == "findings"
    finding = only_finding(data)
    assert finding["verdict"] == "likely reachable" and finding["package"] == "pyyaml"
    assert NVD_URL in {link["url"] for link in finding["advisory_links"]}
    # AC-10 on the wire: the batch names the resolved pin, then the advisory is fetched by id.
    [batch] = [r for r in edge.requests if r.url.path == "/v1/querybatch"]
    [query] = json.loads(batch.content)["queries"]
    assert query["package"]["name"] == "pyyaml" and query["version"] == "5.3.1"
    assert f"/v1/vulns/{GHSA}" in edge.paths(OSV_HOST)
    assert edge.paths(TAVILY_HOST) == ["/search"]
    assert len(edge.paths(NEBIUS_HOST)) == 3  # one research call, then two reasoning rounds
    # The file ledger holds the settled spend and no open reservation after the scan.
    assert nebius_spent(tmp_path) > 0
    assert ledger_of(tmp_path)["reservations"] == []
    assert "spend nebius: used $" in captured.err


@pytest.mark.slow
def test_no_advisories_is_a_clean_scan_that_never_reaches_research_or_nebius(tmp_path, edge):
    edge.osv_ids = {}
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    data = report_of(out)
    assert edge.unexpected == []
    assert data["outcome"] == "clean" and data["findings"] == []
    assert edge.hosts() == {OSV_HOST}
    assert nebius_spent(tmp_path) == 0


@pytest.mark.slow
def test_an_unlocked_pipfile_is_unresolved_and_never_sent_to_osv(tmp_path, edge):
    repo = write_repo(tmp_path / "repo", {"Pipfile": BARE_PIPFILE, "app.py": APP})
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    data = report_of(out)
    assert edge.unexpected == []
    assert data["outcome"] == "incomplete"  # unresolved dependencies make a scan incomplete
    [dependency] = data["dependencies"]
    assert dependency["name"] == "requests"
    assert dependency["resolved"] is False and dependency["osv_status"] == "not_queried"
    assert edge.paths(OSV_HOST) == []


# ---- failures at the network edge -------------------------------------------------------


@pytest.mark.slow
def test_an_osv_outage_is_retried_twice_and_is_never_reported_as_clean(tmp_path, edge):
    edge.osv_status = 500
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    data = report_of(out)
    assert data["outcome"] != "clean"
    assert len(edge.paths(OSV_HOST)) == 3  # one attempt and two retries (AC-26)
    finding = only_finding(data)
    assert finding["kind"] == "not_checked" and finding["reason_code"] == "osv_failed"
    assert edge.paths(TAVILY_HOST) == []


@pytest.mark.slow
def test_a_tavily_outage_after_one_retry_marks_the_finding_uncertain(tmp_path, edge):
    edge.tavily_status = 500
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    finding = only_finding(report_of(out))
    assert finding["verdict"] == "uncertain" and finding["reason_code"] == "tavily_failed"
    assert len(edge.paths(TAVILY_HOST)) == 2  # the search and one retry (AC-24)
    assert edge.paths(NEBIUS_HOST) == []


@pytest.mark.slow
def test_a_nebius_outage_after_one_retry_marks_the_finding_uncertain(tmp_path, edge):
    edge.nebius_status = 500
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    finding = only_finding(report_of(out))
    assert finding["verdict"] == "uncertain" and finding["reason_code"] == "nebius_failed"
    assert len(edge.paths(NEBIUS_HOST)) == 2  # the call and one retry (AC-25)


# ---- the verdict gate, end to end --------------------------------------------------------

GATE_CASES = {
    "a call path step outside the index": (
        {**VERDICT, "call_path": [{**VERDICT["call_path"][0], "file": "ghost.py"}]},
        "unsupported_reachability_claim",
    ),
    "a no-path verdict that the index contradicts": (
        {
            "verdict": "no reachable path detected",
            "confidence": "medium",
            "call_path": None,
            "source_locations": [],
            "explanation": "The model saw no call.",
            "limitations": ["Static analysis only."],
        },
        "contradicted_by_index",
    ),
}


@pytest.mark.parametrize("verdict", list(GATE_CASES.values()), ids=list(GATE_CASES))
@pytest.mark.slow
def test_the_verdict_gate_downgrades_a_claim_the_index_does_not_support(tmp_path, edge, verdict):
    model_verdict, reason_code = verdict
    edge.verdict = model_verdict
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    data = report_of(out)
    finding = only_finding(data)
    assert finding["verdict"] == "uncertain" and finding["reason_code"] == reason_code
    assert len(edge.paths(NEBIUS_HOST)) == 3  # the model was reached over the wire


# ---- invalid input: refused before any request -----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.com/owner/repo",
        "http://github.com/owner/repo",
        "https://api.github.com/repos/owner/repo",
        "https://github.com:8443/owner/repo",
    ],
)
def test_an_invalid_repository_url_exits_2_and_makes_no_request(url, edge, capsys):
    assert scan(url, "--local", "unused") == 2

    assert capsys.readouterr().err.strip() == messages.message("invalid_url")
    assert edge.requests == []


@pytest.mark.parametrize("ref", ["a/./b", "%2e%2e", "a/", "0" * 41])
def test_an_invalid_ref_exits_2_and_makes_no_request(ref, edge, capsys, tmp_path):
    assert scan("https://github.com/owner/repo", "--ref", ref, "--local", str(tmp_path)) == 2

    assert "the ref is not valid" in capsys.readouterr().err
    assert edge.requests == []


# ---- spend: the file ledger across scans, and the refusals ------------------------------


@pytest.mark.slow
def test_the_file_ledger_carries_spend_from_one_scan_to_the_next(tmp_path, edge, monkeypatch):
    # The allowance is written into the ledger when it is created, so set it before scan 1.
    monkeypatch.setenv("SPEND_CAP_NEBIUS_USD", "0.5")
    repo = write_repo(tmp_path / "repo")

    assert scan("--local", str(repo), "--out", str(tmp_path / "one.json")) == 0
    first = nebius_spent(tmp_path)
    assert scan("--local", str(repo), "--out", str(tmp_path / "two.json")) == 0
    second = nebius_spent(tmp_path)
    assert second > first > 0
    assert ledger_of(tmp_path)["reservations"] == []


@pytest.mark.slow
def test_a_scan_is_refused_without_any_request_when_the_floor_exceeds_the_allowance(
    tmp_path, edge, monkeypatch, capsys
):
    monkeypatch.setenv("SPEND_CAP_NEBIUS_USD", "0.5")
    repo = write_repo(tmp_path / "repo")
    assert scan("--local", str(repo), "--out", str(tmp_path / "one.json")) == 0
    # Spent plus the minimum start now exceeds the 0.5 allowance: the next scan is paused.
    monkeypatch.setenv("SCAN_MIN_START_USD", "0.5")
    requests_before = len(edge.requests)
    capsys.readouterr()

    assert scan("--local", str(repo), "--out", str(tmp_path / "two.json")) == 2

    assert messages.message("live_scans_paused") in capsys.readouterr().err
    assert len(edge.requests) == requests_before
    assert not (tmp_path / "two.json").exists()


@pytest.mark.slow
def test_an_unwritable_ledger_pauses_the_scan_before_any_request(
    tmp_path, edge, capsys, monkeypatch
):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    monkeypatch.setenv("SPEND_LEDGER_PATH", str(blocker / "ledger.json"))
    repo = write_repo(tmp_path / "repo")

    assert scan("--local", str(repo)) == 2

    assert messages.message("live_scans_paused") in capsys.readouterr().err
    assert edge.requests == []


@pytest.mark.slow
@pytest.mark.parametrize(
    ("cap_variable", "denied_host", "pool"),
    [
        ("SPEND_CAP_NEBIUS_USD", NEBIUS_HOST, "nebius"),
        ("SPEND_CAP_TAVILY_USD", TAVILY_HOST, "tavily"),
    ],
    ids=["nebius", "tavily"],
)
def test_an_allowance_too_small_for_one_call_stops_that_call_before_it_is_sent(
    tmp_path, edge, monkeypatch, cap_variable, denied_host, pool
):
    # The hold for the first call exceeds the allowance, so the meter denies it before any HTTP.
    monkeypatch.setenv(cap_variable, "0.00005")
    monkeypatch.setenv("SCAN_MIN_START_USD", "0")
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    data = report_of(out)
    assert edge.unexpected == []
    assert edge.paths(denied_host) == []
    finding = only_finding(data)
    assert finding["verdict"] == "uncertain" and finding["reason_code"] == "spend_cap_reached"
    state = ledger_of(tmp_path)
    assert state["reservations"] == []
    assert state["spenders"]["local"]["pools"][pool]["spent_usd"] == 0


# ---- secrets ------------------------------------------------------------------------------


@pytest.mark.slow
def test_no_secret_reaches_the_report_the_output_the_ledger_or_the_logs(tmp_path, edge, capsys):
    edge.tavily_status = 500  # a failing call puts an error on the log path
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    captured = capsys.readouterr()
    assert '"logger"' in captured.err  # the JSON log lines were written, so the check is real
    blobs = [
        captured.out,
        captured.err,
        out.read_text(encoding="utf-8"),
        (tmp_path / "ledger.json").read_text(encoding="utf-8"),
    ]
    for name, secret in SECRETS.items():
        assert not any(secret in blob for blob in blobs), f"{name} leaked"


# ---- logs (AC-73, AC-74, NFR-12) through the CLI ----------------------------------------


def _json_log_lines(err: str) -> list[dict[str, Any]]:
    """The JSON log lines in stderr. The spend summary is a console line, not a log line."""
    return [json.loads(line) for line in err.splitlines() if line.startswith("{")]


@pytest.mark.slow
def test_the_json_log_lines_of_a_fake_scan_carry_one_scan_id_and_stage_fields(
    tmp_path, edge, capsys
):
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    assert scan("--local", str(repo), "--out", str(out)) == 0

    data = report_of(out)
    records = _json_log_lines(capsys.readouterr().err)
    assert records, "the scan wrote no JSON log lines"
    assert {r["scan_id"] for r in records} == {data["scan_id"]}
    stage_ends = [r for r in records if r["event"] == "stage_end"]
    assert [r["stage"] for r in stage_ends] == STEPS
    for record in stage_ends:
        assert isinstance(record["duration_ms"], int) and record["duration_ms"] >= 0
        assert record["outcome"] in {"ok", "failed", "skipped", "timeout"}


@pytest.mark.slow
@pytest.mark.xfail(
    strict=True,
    reason="product bug: in fake mode the agent handlers in sandbox_agent/resolve.py, index.py "
    "and fetch.py call log() without the captured stream, so plain 'pir-agent:' lines reach "
    "the scan's stderr (breaks NFR-12 and AC-73)",
)
def test_every_stderr_line_of_a_fake_scan_except_the_spend_summary_is_json(tmp_path, edge, capsys):
    repo = write_repo(tmp_path / "repo")

    assert scan("--local", str(repo), "--out", str(tmp_path / "report.json")) == 0

    lines = capsys.readouterr().err.splitlines()
    plain = [line for line in lines if line.strip() and not line.startswith(("{", "spend "))]
    assert plain == []


# ---- eval through the CLI and the real wiring (record, then replay) ---------------------

EVAL_CASES = """[[repo]]
id = "app"
source = "local:eval/fixtures/app"

[[case]]
id = "app-yaml"
repo = "app"
package = "pyyaml"
advisory = "GHSA-8q59-q68h-6hv4"
label = "likely reachable"
rationale = "load_config passes its argument to yaml.full_load."
"""


@pytest.mark.slow
def test_the_eval_command_records_then_replays_the_research_answers(
    tmp_path, edge, monkeypatch, capsys
):
    monkeypatch.setenv("EXECUTOR", "fake")  # the Sandbox is not reachable in a fast test
    write_repo(tmp_path / "eval" / "fixtures" / "app")
    cases = tmp_path / "eval" / "cases.toml"
    cases.write_text(EVAL_CASES, encoding="utf-8")
    argv = ["eval", "--cases", str(cases), "--no-langsmith"]

    # Not scored without LangSmith, so the exit code is 2; the output still reports the run.
    assert main([*argv, "--refresh-research"]) == 2
    assert "re-recorded live" in capsys.readouterr().out
    recordings = tmp_path / "eval" / "recordings"
    assert list((recordings / "osv").glob("*.json"))
    assert list((recordings / "tavily").glob("*.json"))
    assert edge.unexpected == []
    assert {OSV_HOST, TAVILY_HOST, NEBIUS_HOST} <= edge.hosts()

    edge.requests.clear()
    assert main(argv) == 2
    assert "replayed" in capsys.readouterr().out
    # OSV and Tavily are answered from the recordings; only the model is called again.
    assert edge.hosts() == {NEBIUS_HOST}


def test_the_eval_command_refuses_an_undefined_repo_without_any_request(tmp_path, edge, capsys):
    write_repo(tmp_path / "eval" / "fixtures" / "app")
    cases = tmp_path / "eval" / "cases.toml"
    cases.write_text(EVAL_CASES.replace('repo = "app"', 'repo = "nope"'), encoding="utf-8")

    assert main(["eval", "--cases", str(cases), "--no-langsmith"]) == 2

    assert "undefined repo" in capsys.readouterr().out
    assert edge.requests == []


# ---- the installed entry point, as a subprocess (slow) ----------------------------------


@pytest.mark.slow
def test_python_m_pir_schema_and_check_config_run_as_a_subprocess(tmp_path):
    env = dict(os.environ)
    for name in Settings.model_fields:  # no configuration from the developer's shell
        env.pop(name.upper(), None)
    env.pop("LANGSMITH_API_KEY", None)
    base = [sys.executable, "-m", "pir"]

    schema = subprocess.run(
        [*base, "schema"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert schema.returncode == 0, schema.stderr
    committed = (ROOT / "schemas" / "report.schema.json").read_bytes().decode("utf-8")
    assert schema.stdout.replace("\r\n", "\n") == committed.replace("\r\n", "\n")

    check = subprocess.run(
        [*base, "check-config", "--for", "scan"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert check.returncode == 1
    assert "NEBIUS_API_KEY" in check.stdout
    assert "sk-" not in check.stdout  # names only, never a value
