"""The call-chain proof through the job runner and the HTTP route (M2-TASK-09).

AC-65 (gated route and config), AC-66, AC-67, AC-68 and AC-82 (`run_proof` on the proof fixtures,
with `FakeExecutor` returning recorded `ExecResult`s; the `proof-run` result is the real harness's
output from a local subprocess), AC-69 and AC-70 (what the job asks the executor to run), request
validation and admission of the route. The verdict never changes.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes.deps import SHA, Harness, make_harness, write_repo
from fakes.proof import REPOS, fixture, line_of, recorded_exec_result
from test_api import VALIDATED, client, error_of, finish, make_api, post_scan

from pir.agent_client import LocalFixtureTarget
from pir.api import routes
from pir.executor.base import ExecResult, ExecutorSpendDenied, ImageRef
from pir.executor.fake import FakeExecutor
from pir.github import RepoNotFound
from pir.jobs import proof_entries, run_proof, with_proof
from pir.models.api import AppConfigResponse, ScanAccepted
from pir.models.report import CallPathStep, Finding, Proof, ScanReport
from pir.pipeline import run_scan
from pir.sandbox_agent import protocol
from pir.spend import SpendDenied

ENTRY_MAIN = "main:<module>"


def result_line(payload: object, **kw) -> ExecResult:
    base = {
        "exit_code": 0,
        "stdout": protocol.encode_result(payload),
        "stderr": "",
        "timed_out": False,
        "truncated": False,
        "duration_s": 0.1,
        "cost_usd": 0.0,
        "image": None,
    }
    return ExecResult(**{**base, **kw})


def installed(*specs: str, no_wheel: tuple[str, ...] = ()) -> ExecResult:
    return result_line({"installed": list(specs), "no_wheel": list(no_wheel)})


def path_of(file: str, entry_symbol: str, entry_line: int, site_line: int, target: str):
    return [
        CallPathStep(file=file, line=entry_line, edge="entry", symbol=entry_symbol),
        CallPathStep(file=file, line=site_line, edge="vulnerable_call", symbol=target),
    ]


def proof_report(report: ScanReport, *, version: str, symbols: list[str], call_path) -> ScanReport:
    """The scan's report with its one finding rewritten to a path the proof fixtures can follow."""
    finding = report.findings[0]
    finding = finding.model_copy(
        update={
            "version": version,
            "call_path": call_path,
            "vulnerable_symbol": finding.vulnerable_symbol.model_copy(update={"symbols": symbols}),
        }
    )
    deps = [d.model_copy(update={"version": version}) for d in report.dependencies]
    return report.model_copy(update={"findings": [finding], "dependencies": deps})


class Scenario:
    """A harness whose FakeExecutor returns `recorded` for the proof commands."""

    def __init__(self, tmp_path: Path, recorded: dict[str, ExecResult], executor=None) -> None:
        self.executor = executor or FakeExecutor(recorded=recorded)
        self.h: Harness = make_harness(tmp_path / "h", executor=self.executor)
        self.tmp_path = tmp_path

    async def report(self, **kw) -> ScanReport:
        repo = write_repo(self.tmp_path / "scanned")
        budget = await self.h.reserve("a" * 32)
        scanned = await run_scan(self.h.deps, self.h.target(repo), budget)
        self.executor.calls.clear()  # only the proof's own commands are of interest
        return proof_report(scanned, **kw)

    async def prove(self, name: str, report: ScanReport, entry: str) -> Proof:
        target = LocalFixtureTarget(path=str(REPOS / name / "repo"), commit_sha=SHA)
        budget = await self.h.reserve("b" * 32)
        return await run_proof(self.h.deps, target, budget, report, report.findings[0], entry)

    def commands(self) -> list[str]:
        return [c.command.split("agent.pyz ")[1].split(" ")[0] for c in self.executor.calls]

    def call(self, command: str):
        (found,) = [c for c in self.executor.calls if f"agent.pyz {command} " in c.command]
        return found, protocol.parse_args_b64(found.argv[-1])


def main_block_report_kw(name: str, file_line: int) -> dict:
    return {
        "version": "5.3.1",
        "symbols": ["yaml.full_load", "yaml.FullLoader"],
        "call_path": path_of("main.py", ENTRY_MAIN, 1, file_line, "yaml.load"),
    }


def assert_verdict_unchanged(report: ScanReport, proof: Proof) -> ScanReport:
    updated = with_proof(report, report.findings[0].finding_id, proof)
    before, after = report.findings[0], updated.findings[0]
    assert after.proof == proof
    assert after.model_copy(update={"proof": None}) == before.model_copy(update={"proof": None})
    assert (after.verdict, after.confidence) == (before.verdict, before.confidence)
    assert updated.model_copy(update={"findings": report.findings}) == report
    return updated


# ---- AC-82: the PyRL-shaped main block through the job runner ---------------------------------


async def test_ac82_main_block_entry_is_proven_through_the_job_with_the_exact_line(tmp_path):
    site = line_of(fixture("proof_main_block")[0] / "main.py", "config = yaml.load(stream)")
    harness_output = recorded_exec_result(
        "proof_main_block", ENTRY_MAIN, "yaml.load", site=f"main.py:{site}"
    )
    assert harness_output.duration_s < 10  # the subprocess ends in seconds, not at a timeout
    s = Scenario(
        tmp_path,
        {"proof-install": installed("pyyaml==5.3.1"), "proof-run": harness_output},
    )
    report = await s.report(**main_block_report_kw("proof_main_block", site))

    proof = await s.prove("proof_main_block", report, ENTRY_MAIN)

    assert proof.status == "proven"
    assert proof.recorded_call == f"yaml.load called from main.py:{site}"
    assert (proof.entry, proof.target) == (ENTRY_MAIN, "yaml.load")
    # the job asked for the call the repository makes, limited to the equivalence's exact line
    run_call, run_args = s.call("proof-run")
    assert run_args == {"entry": ENTRY_MAIN, "target": "yaml.load", "site": f"main.py:{site}"}
    assert (run_call.network, run_call.save) == (False, False)  # AC-70
    install_call, install_args = s.call("proof-install")
    assert (install_call.network, install_call.save) == (True, True)  # AC-70
    assert install_args == {"pins": [{"name": "pyyaml", "version": "5.3.1"}]}
    assert s.commands() == ["unpack", "index", "proof-install", "proof-run"]
    assert_verdict_unchanged(report, proof)


async def test_ac82_the_job_passes_no_site_when_the_call_is_the_symbol_itself(tmp_path):
    repo, _ = fixture("proof_reached")
    site = line_of(repo / "app" / "handler.py", "yaml.unsafe_load(payload)")
    entry = "app.handler:handle"
    output = recorded_exec_result("proof_reached", entry, "yaml.unsafe_load")
    s = Scenario(tmp_path, {"proof-install": installed("pyyaml==6.0.2"), "proof-run": output})
    report = await s.report(
        version="6.0.2",
        symbols=["yaml.unsafe_load"],
        call_path=path_of("app/handler.py", entry, 6, site, "yaml.unsafe_load"),
    )

    proof = await s.prove("proof_reached", report, entry)

    assert (proof.status, proof.recorded_call) == (
        "proven",
        f"yaml.unsafe_load called from app/handler.py:{site}",
    )  # AC-66
    assert s.call("proof-run")[1] == {"entry": entry, "target": "yaml.unsafe_load"}
    assert s.call("proof-install")[1] == {"pins": [{"name": "pyyaml", "version": "6.0.2"}]}
    assert_verdict_unchanged(report, proof)


async def test_ac82_a_required_argparse_argument_is_not_proven_and_the_job_completes(tmp_path):
    site = line_of(fixture("proof_main_argparse")[0] / "main.py", "config = yaml.load(stream)")
    output = recorded_exec_result(
        "proof_main_argparse", ENTRY_MAIN, "yaml.load", site=f"main.py:{site}"
    )
    s = Scenario(tmp_path, {"proof-install": installed("pyyaml==5.3.1"), "proof-run": output})
    report = await s.report(**main_block_report_kw("proof_main_argparse", site))

    proof = await s.prove("proof_main_argparse", report, ENTRY_MAIN)

    assert proof.status == "not_proven" and proof.recorded_call is None
    assert proof.reason == (
        "entry needs command-line arguments; the proof supplies none "
        "(exit code 2 before reaching yaml.load)"
    )
    assert_verdict_unchanged(report, proof)


async def test_ac82_a_script_that_exits_first_is_not_proven_with_its_exit_code(tmp_path):
    site = line_of(fixture("proof_main_exit")[0] / "main.py", "config = yaml.load(stream)")
    output = recorded_exec_result(
        "proof_main_exit", ENTRY_MAIN, "yaml.load", site=f"main.py:{site}"
    )
    s = Scenario(tmp_path, {"proof-install": installed("pyyaml==5.3.1"), "proof-run": output})
    report = await s.report(**main_block_report_kw("proof_main_exit", site))

    proof = await s.prove("proof_main_exit", report, ENTRY_MAIN)

    assert proof.status == "not_proven"
    assert proof.reason == "entry exited with code 0 before reaching yaml.load"
    assert_verdict_unchanged(report, proof)


# ---- AC-67, AC-68: not reached, timeout, no wheel, other failures -----------------------------


async def test_ac67_not_reached_is_not_proven_with_a_reason_and_the_verdict_is_unchanged(tmp_path):
    repo, _ = fixture("proof_not_reached")
    site = line_of(repo / "app" / "handler.py", "yaml.unsafe_load(payload)")
    entry = "app.handler:handle"
    output = recorded_exec_result("proof_not_reached", entry, "yaml.unsafe_load")
    s = Scenario(tmp_path, {"proof-install": installed("pyyaml==6.0.2"), "proof-run": output})
    report = await s.report(
        version="6.0.2",
        symbols=["yaml.unsafe_load"],
        call_path=path_of("app/handler.py", entry, 6, site, "yaml.unsafe_load"),
    )

    proof = await s.prove("proof_not_reached", report, entry)

    assert proof.status == "not_proven"
    assert proof.reason == "entry finished without reaching yaml.unsafe_load"
    assert report.findings[0].verdict == "likely reachable"
    assert_verdict_unchanged(report, proof)


@pytest.fixture
def reached_scenario(tmp_path):
    def build(recorded: dict[str, ExecResult], executor=None) -> Scenario:
        return Scenario(tmp_path, recorded, executor)

    return build


async def reached_report(s: Scenario) -> ScanReport:
    repo, _ = fixture("proof_reached")
    site = line_of(repo / "app" / "handler.py", "yaml.unsafe_load(payload)")
    return await s.report(
        version="6.0.2",
        symbols=["yaml.unsafe_load"],
        call_path=path_of("app/handler.py", "app.handler:handle", 6, site, "yaml.unsafe_load"),
    )


async def test_ac68_a_timed_out_proof_run_is_not_proven_sandbox_timeout(reached_scenario):
    timed_out = ExecResult(
        exit_code=-1,
        stdout="",
        stderr="",
        timed_out=True,
        truncated=False,
        duration_s=60.0,
        cost_usd=0.0,
        image=None,
    )
    s = reached_scenario({"proof-install": installed("pyyaml==6.0.2"), "proof-run": timed_out})
    report = await reached_report(s)
    proof = await s.prove("proof_reached", report, "app.handler:handle")
    assert (proof.status, proof.reason) == ("not_proven", "sandbox timeout")
    assert_verdict_unchanged(report, proof)


async def test_ac68_a_timed_out_install_is_also_a_sandbox_timeout(reached_scenario):
    timed_out = ExecResult(
        exit_code=-1,
        stdout="",
        stderr="",
        timed_out=True,
        truncated=False,
        duration_s=300.0,
        cost_usd=0.0,
        image=None,
    )
    s = reached_scenario({"proof-install": timed_out})
    proof = await s.prove("proof_reached", await reached_report(s), "app.handler:handle")
    assert (proof.status, proof.reason) == ("not_proven", "sandbox timeout")
    assert "proof-run" not in s.commands()


async def test_ac68_a_pin_without_a_wheel_is_listed_and_proof_run_is_skipped(reached_scenario):
    s = reached_scenario({"proof-install": installed(no_wheel=("pyyaml==6.0.2",))})
    report = await reached_report(s)
    proof = await s.prove("proof_reached", report, "app.handler:handle")
    assert proof.status == "not_proven" and proof.recorded_call is None
    assert proof.reason == "uncertain: no wheel for pyyaml==6.0.2; source builds are not run"
    assert s.commands() == ["unpack", "index", "proof-install"]  # proof-run never ran
    assert_verdict_unchanged(report, proof)


async def test_a_sandbox_failure_is_not_proven_and_never_raises(reached_scenario):
    failed = ExecResult(
        exit_code=1,
        stdout="",
        stderr="pir-agent: failure: SECRET",
        timed_out=False,
        truncated=False,
        duration_s=1.0,
        cost_usd=0.0,
        image=None,
    )
    s = reached_scenario({"proof-install": installed("pyyaml==6.0.2"), "proof-run": failed})
    proof = await s.prove("proof_reached", await reached_report(s), "app.handler:handle")
    assert proof.status == "not_proven"
    assert proof.reason == "the Sandbox could not complete the proof (proof-run)"
    assert "SECRET" not in proof.reason  # sandbox text is never copied into a reason


async def test_a_proof_spend_denial_is_not_proven_not_a_failed_job(tmp_path):
    class Denying(FakeExecutor):
        async def run(self, image, argv, **kwargs):
            if "proof-install" in argv:
                raise ExecutorSpendDenied(SpendDenied("nebius", "scan_budget_reached"))
            return await super().run(image, argv, **kwargs)

    s = Scenario(tmp_path, {}, executor=Denying())
    proof = await s.prove("proof_reached", await reached_report(s), "app.handler:handle")
    assert (proof.status, proof.reason) == ("not_proven", "not run: the spend limit was reached")


async def test_a_call_that_is_not_in_the_index_is_not_proven(reached_scenario):
    s = reached_scenario({})
    report = await s.report(
        version="6.0.2",
        symbols=["yaml.unsafe_load"],
        call_path=path_of("app/handler.py", "app.handler:handle", 6, 99, "yaml.unsafe_load"),
    )
    proof = await s.prove("proof_reached", report, "app.handler:handle")
    assert proof.status == "not_proven"
    assert proof.reason == "the vulnerable call is not in the index of the pinned commit"
    assert s.commands() == ["unpack", "index"]


async def test_an_unexpected_error_is_logged_and_reported_not_raised(reached_scenario, caplog):
    class Broken(FakeExecutor):
        async def run(self, image, argv, **kwargs):
            if "index" in argv[-3]:
                raise RuntimeError("boom")
            return await super().run(image, argv, **kwargs)

    s = reached_scenario({}, executor=Broken())
    proof = await s.prove("proof_reached", await reached_report(s), "app.handler:handle")
    assert (proof.status, proof.reason) == ("not_proven", "the proof could not be completed")


async def test_the_workspace_images_are_discarded_after_the_proof(reached_scenario):
    output = recorded_exec_result("proof_reached", "app.handler:handle", "yaml.unsafe_load")
    s = reached_scenario({"proof-install": installed("pyyaml==6.0.2"), "proof-run": output})
    await s.prove("proof_reached", await reached_report(s), "app.handler:handle")
    p_image = s.call("proof-run")[0].image
    assert isinstance(p_image, ImageRef) and not os.path.exists(p_image.ref)


def test_proof_entries_are_the_repository_steps_only():
    steps = path_of("main.py", ENTRY_MAIN, 1, 10, "yaml.load")
    finding = Finding.model_construct(call_path=steps)
    assert proof_entries(finding) == [ENTRY_MAIN]
    assert proof_entries(Finding.model_construct(call_path=None)) == []


def test_with_proof_changes_only_the_named_finding():
    proof = Proof(
        status="not_proven",
        reason="r",
        ran_at=datetime(2026, 10, 26, tzinfo=UTC),
        entry="a:b",
        target="t",
        recorded_call=None,
    )
    one = Finding.model_construct(finding_id="one", proof=None)
    two = Finding.model_construct(finding_id="two", proof=None)
    report = ScanReport.model_construct(findings=[one, two])
    updated = with_proof(report, "two", proof)
    assert [f.proof for f in updated.findings] == [None, proof]
    assert report.findings == [one, two]  # the original is untouched


# ---- AC-65 and the route ----------------------------------------------------------------------

PROOF_ON = {"feature_proof": True}


def make_proof_api(tmp_path, monkeypatch, *, executor: str = "sandbox", **settings):
    """`make_api` with the route's settings: `make_harness` keeps its `executor` argument for the
    executor object, so EXECUTOR is set on the context's settings afterwards (deps stay fake)."""
    api = make_api(tmp_path, monkeypatch, **settings)
    api.ctx.settings = api.ctx.settings.model_copy(update={"executor": executor})
    return api


async def scanned(api, c):
    """Run a scan on the API and rewrite its finding to a likely reachable path with an entry."""
    accepted = await post_scan(c)
    scan_id = await finish(api, accepted)
    job = api.ctx.jobs.get(scan_id)
    report = proof_report(
        job.report,
        version="5.3.1",
        symbols=["yaml.full_load", "yaml.FullLoader"],
        call_path=path_of("main.py", ENTRY_MAIN, 1, 10, "yaml.load"),
    )
    job.report = report
    return scan_id, report.findings[0]


def proof_url(scan_id: str, finding_id: str) -> str:
    return f"/api/scans/{scan_id}/findings/{finding_id}/proof"


NOT_FOUND = "Not Found"


@pytest.mark.parametrize(
    "settings",
    [
        {"executor": "sandbox"},  # FEATURE_PROOF=false (the default)
        {"executor": "fake", "feature_proof": True},  # EXECUTOR=fake
        {"executor": "fake"},
    ],
)
async def test_ac65_the_route_is_404_and_config_says_proof_false_unless_both_are_on(
    tmp_path, monkeypatch, settings
):
    api = make_proof_api(tmp_path, monkeypatch, **settings)
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)
        jobs_before = len(api.ctx.jobs)
        response = await c.post(proof_url(scan_id, finding.finding_id), json={})
        config = await c.get("/api/config")

    assert response.status_code == 404 and response.text == NOT_FOUND
    assert "content-security-policy" in response.headers  # the headers cover the 404 too
    assert len(api.ctx.jobs) == jobs_before and api.ctx.live_in_flight == 0
    assert AppConfigResponse.model_validate_json(config.content).features["proof"] is False
    assert api.h.executor.calls == [] or all("proof" not in c.command for c in api.h.executor.calls)


async def test_ac65_with_both_on_config_reports_proof_true(tmp_path, monkeypatch):
    api = make_proof_api(tmp_path, monkeypatch, **PROOF_ON)
    async with client(api.app) as c:
        config = await c.get("/api/config")
    assert AppConfigResponse.model_validate_json(config.content).features["proof"] is True


@pytest.fixture
def proof_api(tmp_path, monkeypatch):
    api = make_proof_api(tmp_path, monkeypatch, **PROOF_ON)
    calls: list[dict] = []

    async def fake_run_proof(deps, target, budget, report, finding, entry):
        calls.append({"target": target, "finding": finding, "entry": entry, "report": report})
        return Proof(
            status="proven",
            reason="yaml.load was reached from " + entry,
            ran_at=api.ctx.clock.now(),
            entry=entry,
            target="yaml.load",
            recorded_call="yaml.load called from main.py:10",
        )

    monkeypatch.setattr(routes, "run_proof", fake_run_proof)
    api.proof_calls = calls
    return api


async def test_a_proof_is_accepted_runs_as_a_proof_job_and_sets_proof_on_the_stored_report(
    proof_api,
):
    api = proof_api
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)
        response = await c.post(proof_url(scan_id, finding.finding_id))  # no body: default entry

        assert response.status_code == 202
        accepted = ScanAccepted.model_validate_json(response.content)
        assert accepted.mode == "proof" and accepted.scan_id != scan_id
        await api.ctx.jobs.get(accepted.scan_id).task
        stored = ScanReport.model_validate_json(
            (await c.get(f"/api/scans/{scan_id}/report")).content
        )
        proof_job = ScanReport.model_validate_json((await c.get(accepted.report_url)).content)
        status = (await c.get(accepted.status_url)).json()

    (call,) = api.proof_calls
    assert call["entry"] == ENTRY_MAIN
    assert call["target"] is VALIDATED
    assert api.validate_calls[-1] == (VALIDATED.coords, SHA)  # the scanned commit, re-validated
    assert stored.findings[0].proof is not None
    assert stored.findings[0].proof.recorded_call == "yaml.load called from main.py:10"
    assert stored.findings[0].verdict == finding.verdict == "likely reachable"  # unchanged
    assert proof_job == stored
    assert status["mode"] == "proof" and status["state"] == "completed"
    assert api.ctx.live_in_flight == 0 and api.h.ledger.state.reservations == []  # settled


async def test_an_entry_may_name_another_step_of_the_call_path(proof_api):
    api = proof_api
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)
        report = api.ctx.jobs.get(scan_id).report
        longer = [
            CallPathStep(file="main.py", line=1, edge="entry", symbol=ENTRY_MAIN),
            CallPathStep(file="main.py", line=4, edge="call", symbol="main:run"),
            CallPathStep(file="main.py", line=10, edge="vulnerable_call", symbol="yaml.load"),
        ]
        api.ctx.jobs.get(scan_id).report = with_call_path(report, longer)
        response = await c.post(proof_url(scan_id, finding.finding_id), json={"entry": "main:run"})
        await finish(api, response)
    assert response.status_code == 202 and api.proof_calls[0]["entry"] == "main:run"


def with_call_path(report: ScanReport, call_path) -> ScanReport:
    finding = report.findings[0].model_copy(update={"call_path": call_path})
    return report.model_copy(update={"findings": [finding]})


@pytest.mark.parametrize(
    "body",
    [
        b'{"entry": "other:func"}',  # not a step of the call path
        b'{"entry": "yaml.load"}',  # the vulnerable step is not an entry
        b'{"entry": ""}',
        b'{"entry": 5}',
        b'{"entry": null, "extra": 1}',
        b"[1]",
        b"not json",
    ],
)
async def test_an_invalid_entry_or_body_is_request_invalid_and_starts_nothing(proof_api, body):
    api = proof_api
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)
        jobs_before = len(api.ctx.jobs)
        response = await c.post(proof_url(scan_id, finding.finding_id), content=body)
    assert response.status_code == 422 and error_of(response).code == "request_invalid"
    assert len(api.ctx.jobs) == jobs_before and api.proof_calls == []
    assert api.ctx.live_in_flight == 0 and api.h.ledger.state.reservations == []


async def test_a_finding_that_is_not_likely_reachable_is_request_invalid(proof_api):
    api = proof_api
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)
        job = api.ctx.jobs.get(scan_id)
        uncertain = job.report.findings[0].model_copy(
            update={"verdict": "uncertain", "confidence": "low", "call_path": None}
        )
        job.report = job.report.model_copy(update={"findings": [uncertain]})
        response = await c.post(proof_url(scan_id, finding.finding_id))
    assert response.status_code == 422 and error_of(response).code == "request_invalid"
    assert api.proof_calls == []


async def test_unknown_scan_and_unknown_finding_have_their_own_errors(proof_api):
    api = proof_api
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)
        missing_scan = await c.post(proof_url("f" * 32, finding.finding_id))
        missing_finding = await c.post(proof_url(scan_id, "0" * 16))
    assert (missing_scan.status_code, error_of(missing_scan).code) == (404, "scan_not_found")
    assert (missing_finding.status_code, error_of(missing_finding).code) == (
        404,
        "finding_not_found",
    )


async def test_a_scan_without_a_report_yet_is_report_not_ready(tmp_path, monkeypatch):
    api = make_proof_api(tmp_path, monkeypatch, gate=asyncio.Event(), **PROOF_ON)
    async with client(api.app) as c:
        accepted = await post_scan(c)
        pending = ScanAccepted.model_validate_json(accepted.content).scan_id
        response = await c.post(proof_url(pending, "0" * 16))
        await api.ctx.jobs.shutdown()
    assert (response.status_code, error_of(response).code) == (409, "report_not_ready")


async def test_proof_admission_uses_the_scan_limits(tmp_path, monkeypatch):
    api = make_proof_api(tmp_path, monkeypatch, rate_limit_per_client_hour=1, **PROOF_ON)
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)  # the scan used the client's one live start
        limited = await c.post(proof_url(scan_id, finding.finding_id))
    assert limited.status_code == 429 and error_of(limited).code == "rate_limited"
    assert "Retry-After" in limited.headers


async def test_proof_admission_refuses_when_the_repository_is_gone_and_releases_everything(
    proof_api,
):
    api = proof_api
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)

        async def gone(coords, ref):
            raise RepoNotFound()

        api.h.deps.validate = gone
        response = await c.post(proof_url(scan_id, finding.finding_id))
    assert response.status_code == 404 and error_of(response).code == "repo_not_found"
    assert api.ctx.live_in_flight == 0 and api.h.ledger.state.reservations == []
    assert api.proof_calls == []


async def test_proof_is_refused_when_live_scans_are_off(proof_api):
    api = proof_api
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)
        api.ctx.deps = None  # LIVE_SCANS_ENABLED=false builds no deps
        response = await c.post(proof_url(scan_id, finding.finding_id))
    assert response.status_code == 503 and error_of(response).code == "live_scans_paused"
    assert api.proof_calls == []


async def test_the_real_runner_with_the_fake_executor_ends_not_proven_not_failed(
    tmp_path, monkeypatch
):
    api = make_proof_api(tmp_path, monkeypatch, **PROOF_ON)  # the fake refuses the GitHub fetch
    async with client(api.app) as c:
        scan_id, finding = await scanned(api, c)
        response = await c.post(proof_url(scan_id, finding.finding_id))
        proof_id = await finish(api, response)
        job = api.ctx.jobs.get(proof_id)
        stored = (await c.get(f"/api/scans/{scan_id}/report")).json()

    assert job.state == "completed" and job.report is not None
    proof = stored["findings"][0]["proof"]
    assert proof["status"] == "not_proven"
    assert proof["reason"] == "the Sandbox could not complete the proof (fetch)"
    assert stored["findings"][0]["verdict"] == "likely reachable"
    json.dumps(stored)  # the stored report stays serialisable
