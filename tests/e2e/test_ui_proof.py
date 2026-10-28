"""Call-chain proof controls in the UI.

AC-65 (no control unless `features.proof` is true), AC-66 (proven shows the recorded call), AC-67
(not proven shows the reason; the verdict is not upgraded), AC-68 (`uncertain: ...` and
`sandbox timeout` reasons render as not proven), AC-83 (the source-package reasons). The fixture
tests check proof.json and config.json; the browser tests run against the mock adapter
(`?api=mock&proof=<mode>`) and against the real HTTP adapter on the fake backend, where `run_proof`
is replaced because the real one needs a Sandbox.
"""

# ruff: noqa: E501 (the embedded browser scripts read better on one line)

import asyncio
import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from e2e.conftest import REPO_URL, start_server
from e2e.test_ui_flows import expand_all, overflow
from pir.api import routes
from pir.jobs import PROOF_SANDBOX_TIMEOUT
from pir.models.api import AppConfigResponse
from pir.models.report import CallPathStep, Proof, ScanReport

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "web" / "fixtures"
MOCK_JS = (ROOT / "web" / "js" / "api" / "mock.js").read_text(encoding="utf-8")
JOBS_PY = (ROOT / "src" / "pir" / "jobs.py").read_text(encoding="utf-8")

# The whole module is slow: the e2e conftest's autouse fixture swaps `routes.run_scan` for the rest of the
# session, which would break the fast integration tests, so nothing here may run in the fast command.
pytestmark = pytest.mark.slow

PROVE = "Prove call chain"
PYYAML_ID = "f4509c8a78e1169b"  # the proven fixture finding (pyyaml 5.3.1)
JINJA_ID = "a0246c3e801fb5de"  # the not proven fixture finding (jinja2 2.11.2)

# What the backend writes in `Proof.reason` when the proof could not run to the end (src/pir/jobs.py).
NO_WHEEL = "uncertain: no wheel for pyyaml==5.3.1; its source package could not be downloaded"
BUILD_FAILED = "uncertain: could not build pyyaml 5.3.1 from source"
SPEND = "not run: the spend limit was reached"
INCONCLUSIVE = {
    "no-wheel": NO_WHEEL,
    "build-failed": BUILD_FAILED,
    "timeout": "sandbox timeout",
    "spend": SPEND,
}


def read_report(name: str) -> dict:
    return json.loads((FIXTURES / "reports" / f"{name}.json").read_text("utf-8"))


# ---- fixtures (fast) -------------------------------------------------------------------------


def test_ac65_proof_fixture_validates_against_scan_report():
    report = ScanReport.model_validate_json(
        (FIXTURES / "reports" / "proof.json").read_text("utf-8")
    )
    by_id = {f.finding_id: f for f in report.findings}
    assert by_id[PYYAML_ID].proof is not None and by_id[JINJA_ID].proof is not None
    assert [f.proof for f in report.findings if f.finding_id not in (PYYAML_ID, JINJA_ID)] == [
        None
    ] * 4


def test_ac67_proof_fixture_is_the_reachable_fixture_plus_proofs_and_no_verdict_differs():
    proof, reachable = read_report("proof"), read_report("reachable")
    assert [f["verdict"] for f in proof["findings"]] == [
        f["verdict"] for f in reachable["findings"]
    ]
    stripped = copy.deepcopy(proof)
    for finding in stripped["findings"]:
        finding.pop("proof", None)
    for finding in reachable["findings"]:
        finding.pop("proof", None)
    assert stripped == reachable  # the Markdown and event fixtures of `reachable` still fit


def test_ac66_ac67_fixture_outcomes_proven_has_the_recorded_call_not_proven_has_none():
    report = ScanReport.model_validate_json(
        (FIXTURES / "reports" / "proof.json").read_text("utf-8")
    )
    by_id = {f.finding_id: f for f in report.findings}
    proven, unproven = by_id[PYYAML_ID], by_id[JINJA_ID]
    sink = proven.call_path[-1]
    assert proven.proof.status == "proven"
    assert (
        proven.proof.recorded_call == f"{proven.proof.target} called from {sink.file}:{sink.line}"
    )
    assert proven.proof.entry == proven.call_path[0].symbol
    assert unproven.proof.status == "not_proven" and unproven.proof.recorded_call is None
    assert unproven.proof.reason


def test_ac65_config_fixture_ships_with_the_proof_feature_off():
    config = AppConfigResponse.model_validate_json((FIXTURES / "config.json").read_text("utf-8"))
    assert config.features["proof"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(status="maybe"),
        lambda p: p.pop("reason"),
        lambda p: p.update(ran_at="yesterday"),
        lambda p: p.update(unexpected=True),
    ],
)
def test_ac65_an_invalid_proof_in_the_fixture_is_rejected(mutate):
    report = read_report("proof")
    finding = next(f for f in report["findings"] if f["finding_id"] == PYYAML_ID)
    mutate(finding["proof"])
    with pytest.raises(ValidationError):
        ScanReport.model_validate_json(json.dumps(report))


def test_ac68_the_mock_uses_the_reasons_the_backend_writes():
    """A drift guard: the mock's reason templates are the backend's wording (src/pir/jobs.py)."""
    assert PROOF_SANDBOX_TIMEOUT == "sandbox timeout" and '"sandbox timeout"' in MOCK_JS
    for fragment in (
        "uncertain: no wheel for ",
        "; its source package could not be downloaded",
        "uncertain: could not build ",
        " from source",
        "not run: the spend limit was reached",
    ):
        assert fragment in JOBS_PY and fragment in MOCK_JS, fragment
    for reason in (NO_WHEEL, BUILD_FAILED):
        assert Proof(
            status="not_proven",
            reason=reason,
            ran_at=datetime(2026, 10, 25, 10, tzinfo=UTC),
            entry="app.main:cli",
            target="yaml.load",
            recorded_call=None,
        )


# ---- helpers ---------------------------------------------------------------------------------


def mock_page(server, ui_factory, query: str, *, width: int = 1280):
    ui = ui_factory(server.url, width=width)
    ui.page.goto(f"{server.url}/?api=mock&scenario=reachable&jump=report{query}")
    ui.page.locator("#report .funnel").wait_for()
    expand_all(ui.page)
    return ui


def finding_card(page, finding_id: str):
    return page.locator(f"#finding-{finding_id}")


def verdicts(page) -> dict:
    """Everything the verdict is made of on screen: each card's verdict and chip, and the tally."""
    return page.evaluate(
        """() => ({
          cards: [...document.querySelectorAll('.finding')].map((c) => [c.dataset.verdict, c.querySelector('.flag').innerText]),
          tally: document.querySelector('#report .funnel').innerText,
          summary: document.querySelector('#report .tally') ? document.querySelector('#report .tally').innerText : '',
        })"""
    )


def prove_button(card):
    return card.get_by_role("button", name=PROVE)


def proof_block(card):
    return card.locator(".ev-proof")


# ---- AC-65: no control unless the feature is on ----------------------------------------------


def test_ac65_the_mock_shows_no_proof_control_by_default(server, ui_factory):
    ui = mock_page(server, ui_factory, "")
    assert ui.page.get_by_role("button", name=re.compile("prove", re.I)).count() == 0
    assert (
        ui.page.locator(".ev-proof").count() == 0
        and "Call-chain proof" not in ui.page.locator("#report").inner_text()
    )
    assert ui.problems() == []


def test_ac65_an_unknown_mock_proof_mode_leaves_the_feature_off(server, ui_factory):
    ui = mock_page(server, ui_factory, "&proof=../reports/proof")
    assert ui.page.get_by_role("button", name=re.compile("prove", re.I)).count() == 0


def test_ac65_the_real_backend_with_proof_off_renders_no_control_and_404s_the_route(
    server, ui_factory
):
    ui = ui_factory(server.url).goto()
    ui.scan(REPO_URL.format(name="demo"))
    ui.wait_report()
    expand_all(ui.page)
    assert (
        ui.page.evaluate("fetch('/api/config').then(r => r.json())")["features"]["proof"] is False
    )
    assert ui.page.get_by_role("button", name=re.compile("prove", re.I)).count() == 0
    assert ui.page.locator(".ev-proof").count() == 0
    finding_id = ui.page.locator(".finding").first.get_attribute("id").removeprefix("finding-")
    response = httpx.post(
        f"{server.url}/api/scans/{ui.scan_id()}/findings/{finding_id}/proof", json={}
    )
    assert response.status_code == 404
    assert ui.problems() == []


# ---- AC-66, AC-67, AC-68 on the mock adapter ---------------------------------------------------


def test_ac66_mock_proven_shows_the_recorded_call_and_leaves_the_verdict(server, ui_factory):
    ui = mock_page(server, ui_factory, "&proof=on")
    before = verdicts(ui.page)
    card = finding_card(ui.page, PYYAML_ID)
    assert proof_block(card).get_attribute("data-proof-state") == "idle"
    prove_button(card).click()
    block = proof_block(card)
    ui.page.locator(f"#finding-{PYYAML_ID} .ev-proof[data-proof='proven']").wait_for()
    assert block.locator(".proof-status").inner_text().startswith("proven")
    text = block.inner_text()
    assert "yaml.load called from app/config.py:12" in text
    assert "app.main:cli" in text and "yaml.load was reached from app.main:cli" in text
    assert (
        prove_button(card).count() == 0
        and card.get_by_role("button", name="Prove again").count() == 1
    )
    assert verdicts(ui.page) == before
    assert card.get_attribute("data-verdict") == "likely reachable"
    assert ui.problems() == []


def test_ac67_mock_not_proven_shows_the_reason_and_the_verdict_is_not_upgraded(server, ui_factory):
    ui = mock_page(server, ui_factory, "&proof=on")
    before = verdicts(ui.page)
    card = finding_card(ui.page, JINJA_ID)
    prove_button(card).click()
    ui.page.locator(f"#finding-{JINJA_ID} .ev-proof[data-proof='not_proven']").wait_for()
    block = proof_block(card)
    assert block.locator(".proof-status").inner_text().startswith("not proven")
    assert "entry finished without reaching jinja2.utils.urlize" in block.inner_text()
    assert "Recorded call" not in block.inner_text()
    assert block.get_attribute("data-proof-kind") == "not_reached"
    assert verdicts(ui.page) == before
    assert ui.problems() == []


@pytest.mark.parametrize("mode", list(INCONCLUSIVE))
def test_ac68_ac83_mock_uncertain_and_timeout_reasons_render_as_not_proven(
    server, ui_factory, mode
):
    ui = mock_page(server, ui_factory, f"&proof={mode}")
    before = verdicts(ui.page)
    card = finding_card(ui.page, PYYAML_ID)
    prove_button(card).click()
    ui.page.locator(f"#finding-{PYYAML_ID} .ev-proof[data-proof='not_proven']").wait_for()
    block = proof_block(card)
    assert block.locator(".proof-status").inner_text().startswith("not proven")
    assert block.locator(".proof-reason").inner_text() == INCONCLUSIVE[mode]
    assert block.get_attribute("data-proof-kind") == "uncertain"
    assert "says nothing either way" in block.inner_text()
    assert "Recorded call" not in block.inner_text()
    assert verdicts(ui.page) == before  # never upgraded, never downgraded
    assert ui.problems() == []


def test_ac68_not_proven_is_visibly_distinct_from_proven(server, ui_factory):
    ui = mock_page(server, ui_factory, "&proof=on")
    prove_button(finding_card(ui.page, PYYAML_ID)).click()
    prove_button(finding_card(ui.page, JINJA_ID)).click()
    ui.page.locator(".ev-proof[data-proof='proven']").wait_for()
    ui.page.locator(".ev-proof[data-proof='not_proven']").wait_for()
    style = """(el) => { const s = getComputedStyle(el); return [s.borderTopStyle, s.borderLeftWidth, s.backgroundColor, el.querySelector('.proof-mark svg use').getAttribute('href')]; }"""
    proven = ui.page.locator(".ev-proof[data-proof='proven']").evaluate(style)
    unproven = ui.page.locator(".ev-proof[data-proof='not_proven']").evaluate(style)
    assert proven[0] == "solid" and unproven[0] == "dashed"  # shape of the edge
    assert proven[2] != unproven[2]  # the fill
    assert proven[3] != unproven[3]  # the mark (a check, not a dash)
    assert (
        "not proven"
        in ui.page.locator(".ev-proof[data-proof='not_proven'] .proof-status").inner_text()
    )


def test_mock_a_refused_proof_request_shows_the_api_message_beside_the_control_and_can_retry(
    server, ui_factory
):
    ui = mock_page(server, ui_factory, "&proof=error")
    before = verdicts(ui.page)
    card = finding_card(ui.page, PYYAML_ID)
    prove_button(card).click()
    alert = card.locator(".ev-proof [role=alert]")
    alert.wait_for()
    assert "Rate limited" in alert.inner_text()
    assert card.locator(".ev-proof").get_attribute("data-proof-state") == "error"
    assert card.get_by_role("button", name="Try again").count() == 1
    assert card.locator(".ev-proof[data-proof]").count() == 0  # nothing claims a result
    assert verdicts(ui.page) == before


def test_mock_proof_control_works_from_the_keyboard_and_keeps_focus(server, ui_factory):
    ui = mock_page(server, ui_factory, "&proof=on")
    card = finding_card(ui.page, PYYAML_ID)
    button = prove_button(card)
    button.focus()
    assert ui.page.evaluate("document.activeElement.textContent") == PROVE
    ui.page.keyboard.press("Enter")
    ui.page.locator(f"#finding-{PYYAML_ID} .ev-proof[data-proof='proven']").wait_for()
    assert ui.page.evaluate("document.activeElement.textContent") == "Prove again"
    outline = ui.page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline != "none"  # a visible focus ring


@pytest.mark.parametrize("width", [390, 1280])
def test_mock_proof_blocks_fit_the_screen(server, ui_factory, width):
    ui = mock_page(server, ui_factory, "&proof=no-wheel", width=width)
    assert overflow(ui.page) == []
    for card_id in (PYYAML_ID, JINJA_ID):
        prove_button(finding_card(ui.page, card_id)).click()
    ui.page.locator(".ev-proof[data-proof]").nth(1).wait_for()
    assert overflow(ui.page) == []
    assert ui.page.get_by_role("button", name="Prove again").first.bounding_box()["height"] >= 44


# ---- the real HTTP adapter on the fake backend ---------------------------------------------------


class FakeProofs:
    """Stands in for `run_proof` (the real one runs the Sandbox): returns what a test sets."""

    def __init__(self) -> None:
        self.status = "proven"
        self.reason: str | None = None
        self.delay = 0.0
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, deps, target, budget, report, finding, entry):
        self.calls.append((finding.finding_id, entry))
        await asyncio.sleep(self.delay)
        sink = finding.call_path[-1]
        proven = self.status == "proven"
        return Proof(
            status=self.status,
            reason=self.reason
            or (
                f"{sink.symbol} was reached from {entry}"
                if proven
                else "entry finished without reaching " + sink.symbol
            ),
            ran_at=deps.clock.now(),
            entry=entry,
            target=sink.symbol,
            recorded_call=f"{sink.symbol} called from {sink.file}:{sink.line}" if proven else None,
        )


@pytest.fixture
def fake_proofs(monkeypatch):
    fake = FakeProofs()
    monkeypatch.setattr(routes, "run_proof", fake)
    return fake


def _proof_on(server):
    ctx = server.app.state.ctx
    ctx.settings = ctx.settings.model_copy(update={"executor": "sandbox"})
    return server


@pytest.fixture(scope="module")
def proof_server(tmp_path_factory):
    s = _proof_on(start_server(tmp_path_factory.mktemp("proof"), feature_proof=True))
    yield s
    s.stop()


@pytest.fixture(scope="module")
def proof_polling_server(tmp_path_factory):
    """The progress feed is off, so the proof job is followed by polling its status."""
    s = _proof_on(
        start_server(
            tmp_path_factory.mktemp("proofpoll"), feature_proof=True, feature_progress_feed=False
        )
    )
    yield s
    s.stop()


ENTRY = "app:<module>"


def _entry_step() -> dict:
    return {"file": "app.py", "line": 1, "edge": "entry", "symbol": ENTRY}


def _add_entry(report: dict) -> None:
    for finding in report["findings"]:
        if finding["verdict"] == "likely reachable":
            finding["call_path"] = [_entry_step(), *finding["call_path"]]


def scanned(server, ui_factory, **kw):
    """Scan the demo repository. Its finding's call path is only the vulnerable call, which has no
    entry to prove from, so an entry step is put in front of it, on the server and in the page."""
    ui = ui_factory(server.url, **kw).goto()
    ui.patch_report(_add_entry)
    ui.scan(REPO_URL.format(name="demo"))
    ui.wait_report()
    job = server.app.state.ctx.jobs.get(ui.scan_id())
    findings = [
        f.model_copy(update={"call_path": [CallPathStep(**_entry_step()), *f.call_path]})
        if f.verdict == "likely reachable"
        else f
        for f in job.report.findings
    ]
    job.report = job.report.model_copy(update={"findings": findings})
    expand_all(ui.page)
    return ui


def stored_finding(server, ui) -> dict:
    report = httpx.get(f"{server.url}/api/scans/{ui.scan_id()}/report", timeout=10).json()
    return report["findings"][0]


def test_ac65_the_backend_with_proof_on_gets_a_control_on_the_likely_reachable_card(
    proof_server, ui_factory, fake_proofs
):
    ui = scanned(proof_server, ui_factory)
    card = ui.page.locator(".finding[data-verdict='likely reachable']").first
    assert prove_button(card).count() == 1
    assert prove_button(card).get_attribute("aria-disabled") is None
    assert ui.problems() == []


def test_ac66_the_backend_proof_proven_shows_the_recorded_call_and_is_stored(
    proof_server, ui_factory, fake_proofs
):
    ui = scanned(proof_server, ui_factory)
    before = verdicts(ui.page)
    card = ui.page.locator(".finding[data-verdict='likely reachable']").first
    requests = []
    ui.page.on(
        "request",
        lambda r: (
            requests.append((r.method, r.url, r.post_data)) if r.url.endswith("/proof") else None
        ),
    )
    fake_proofs.delay = 0.6
    prove_button(card).click()
    # while it runs: the button says so and does not start a second run, and the body says what is happening
    assert card.get_by_role("button", name="Proving…").get_attribute("aria-disabled") == "true"
    assert "Running in a sandbox" in card.locator(".ev-proof").inner_text()
    card.locator(".ev-proof[data-proof='proven']").wait_for()
    block = card.locator(".ev-proof")
    assert block.locator(".proof-status").inner_text().startswith("proven")
    recorded = stored_finding(proof_server, ui)["proof"]
    assert recorded["status"] == "proven" and recorded["recorded_call"] in block.inner_text()
    assert len(fake_proofs.calls) == 1 and fake_proofs.calls[0][1] == recorded["entry"]
    assert [m for m, _u, _b in requests] == ["POST"]
    assert requests[0][1].endswith(
        f"/api/scans/{ui.scan_id()}/findings/{stored_finding(proof_server, ui)['finding_id']}/proof"
    )
    assert (
        stored_finding(proof_server, ui)["verdict"] == "likely reachable"
    )  # unchanged on the server
    assert verdicts(ui.page) == before
    assert ui.problems() == []


@pytest.mark.parametrize(
    ("status", "reason", "kind"),
    [
        ("not_proven", "entry finished without reaching yaml.full_load", "not_reached"),
        ("not_proven", "sandbox timeout", "uncertain"),
        ("not_proven", NO_WHEEL, "uncertain"),
        ("not_proven", BUILD_FAILED, "uncertain"),
        ("not_proven", SPEND, "uncertain"),
        ("not_proven", "the Sandbox could not complete the proof (proof-install)", "uncertain"),
    ],
)
def test_ac67_ac68_ac83_the_backend_proof_not_proven_shows_its_reason(
    proof_server, ui_factory, fake_proofs, status, reason, kind
):
    fake_proofs.status, fake_proofs.reason = status, reason
    ui = scanned(proof_server, ui_factory)
    before = verdicts(ui.page)
    card = ui.page.locator(".finding[data-verdict='likely reachable']").first
    prove_button(card).click()
    card.locator(".ev-proof[data-proof='not_proven']").wait_for()
    block = card.locator(".ev-proof")
    assert block.locator(".proof-reason").inner_text() == reason
    assert block.get_attribute("data-proof-kind") == kind
    assert block.locator(".proof-status").inner_text().startswith("not proven")
    assert "Recorded call" not in block.inner_text()
    assert verdicts(ui.page) == before
    assert stored_finding(proof_server, ui)["verdict"] == "likely reachable"
    assert ui.problems() == []


def test_ac66_the_proof_job_is_followed_by_polling_when_the_progress_feed_is_off(
    proof_polling_server, ui_factory, fake_proofs
):
    ui = scanned(proof_polling_server, ui_factory)
    card = ui.page.locator(".finding[data-verdict='likely reachable']").first
    prove_button(card).click()
    card.locator(".ev-proof[data-proof='proven']").wait_for(timeout=15_000)
    assert not any(u.endswith("/events") for u in ui.requests)  # polled, never streamed
    assert len(fake_proofs.calls) == 1
    assert ui.problems() == []


def test_the_backend_refusing_a_proof_shows_its_message_and_a_retry_works(
    proof_server, ui_factory, fake_proofs
):
    ui = scanned(proof_server, ui_factory)
    card = ui.page.locator(".finding[data-verdict='likely reachable']").first
    ui.fail(
        re.compile(r"/api/scans/[^/]+/findings/[^/]+/proof$"),
        429,
        "rate_limited",
        "Rate limited: you can start another live scan in 40 min. Demo replays still work.",
        **{"Retry-After": "2400"},
    )
    prove_button(card).click()
    card.locator(".ev-proof [role=alert]").wait_for()
    assert (
        "Rate limited: you can start another live scan in 40 min."
        in card.locator(".ev-proof").inner_text()
    )
    assert fake_proofs.calls == []
    ui.page.unroute(re.compile(r"/api/scans/[^/]+/findings/[^/]+/proof$"))
    card.get_by_role("button", name="Try again").click()
    card.locator(".ev-proof[data-proof='proven']").wait_for()
    assert len(fake_proofs.calls) == 1


def test_ac60_markup_in_a_proof_reason_renders_as_text(proof_server, ui_factory, fake_proofs):
    payload = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>'
    fake_proofs.status, fake_proofs.reason = "not_proven", payload
    ui = scanned(proof_server, ui_factory)
    card = ui.page.locator(".finding[data-verdict='likely reachable']").first
    prove_button(card).click()
    card.locator(".ev-proof[data-proof='not_proven']").wait_for()
    assert card.locator(".proof-reason").inner_text() == payload
    assert card.locator(".ev-proof img, .ev-proof script").count() == 0
    ui.page.wait_for_timeout(200)
    assert ui.page.evaluate("window.__pwned") is None
    assert ui.problems() == []
