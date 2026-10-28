"""The HTTP API through `httpx.ASGITransport` with fakes (section 8).

Covers AC-02 to AC-06 (API side), AC-32, AC-43, AC-50, AC-51, AC-61 (server part), AC-76, the
config and schema routes, static serving and the `serve` command.
"""

import asyncio
import json
import logging
import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fakes.deps import SHA, Harness, capture_json_logs, log_lines, make_harness, write_repo
from fakes.ledger import make_state
from fakes.schema_subset import validate_document

from pir import __version__, messages
from pir.api import routes
from pir.api.app import create_app
from pir.cli import main
from pir.github import (
    GitHubUnavailable,
    NotPythonProject,
    RepoCoordinates,
    RepoNotFound,
    RepoTooLarge,
    ValidatedTarget,
)
from pir.markdown import render_markdown
from pir.models.api import AppConfigResponse, ErrorResponse, HealthResponse, ScanAccepted
from pir.models.report import ScanReport

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SCHEMA = ROOT / "schemas" / "report.schema.json"
URL = "https://github.com/octo/demo"
VALIDATED = ValidatedTarget(
    coords=RepoCoordinates(owner="octo", name="demo"),
    default_branch="main",
    commit_sha=SHA,
    files_total=3,
    bytes_total=1000,
    py_files=1,
    manifests=["requirements.txt"],
)
LATEST = {
    "scored": True,
    "run_date": "2026-10-25",
    "model": "nvidia/nemotron-3-super-120b-a12b",
    "experiment": "eval-x",
    "n": 14,
    "errored": 0,
    "precision": 1.0,
    "recall": 0.5,
    "abstention_rate": 0.2,
    "bar": 0.8,
    "bar_met": True,
}


def make_api(tmp_path, monkeypatch, *, gate: asyncio.Event | None = None, **settings_kw):
    """An app on fakes: GitHub validation is a stub; the scan itself runs the real pipeline on a
    local fixture repository (FakeLLM, recorded OSV and Tavily answers, FakeExecutor)."""
    h = make_harness(tmp_path / "h", **settings_kw)
    repo = write_repo(tmp_path / "repo")
    validate_calls: list[tuple[RepoCoordinates, str | None]] = []

    async def validate(coords, ref):
        validate_calls.append((coords, ref))
        return VALIDATED

    h.deps.validate = validate
    real_run_scan = routes.run_scan

    async def fixture_scan(deps, target, budget, **kwargs):
        if gate is not None:
            await gate.wait()
        return await real_run_scan(deps, Harness.target(repo), budget, **kwargs)

    monkeypatch.setattr(routes, "run_scan", fixture_scan)
    app = create_app(h.deps, web_dir=WEB, eval_path=tmp_path / "latest.json")
    return SimpleNamespace(h=h, app=app, ctx=app.state.ctx, validate_calls=validate_calls)


def client(app, host: str = "203.0.113.9") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(host, 4000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def api(tmp_path, monkeypatch):
    return make_api(tmp_path, monkeypatch)


def error_of(response: httpx.Response):
    return ErrorResponse.model_validate_json(response.content).error


async def post_scan(c: httpx.AsyncClient, url: str = URL, **extra) -> httpx.Response:
    return await c.post("/api/scans", json={"repo_url": url, **extra})


async def finish(api, response: httpx.Response) -> str:
    scan_id = ScanAccepted.model_validate_json(response.content).scan_id
    await api.ctx.jobs.get(scan_id).task
    return scan_id


def assert_nothing_started(api) -> None:
    assert len(api.ctx.jobs) == 0
    assert api.ctx.live_in_flight == 0
    assert api.h.ledger.state.reservations == []


# ---- admission: URL, intake errors (AC-02 to AC-06) ----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "http://github.com/octo/demo",
        "https://github.com.evil.com/octo/demo",
        "https://user@github.com/octo/demo",
        "https://github.com:8443/octo/demo",
        "https://127.0.0.1/octo/demo",  # AC-06
        "https://api.github.com/repos/octo/demo",  # AC-06
        "https://github.com/octo/demo?x=1",
        "https://github.com/octo/%64emo",
        "",
        "https://github.com/octo/" + "a" * 300,
    ],
)
async def test_ac02_ac06_invalid_url_is_400_and_starts_nothing(api, url):
    async with client(api.app) as c:
        response = await post_scan(c, url)

    assert response.status_code == 400
    err = error_of(response)
    assert err.code == "invalid_url"
    assert err.message == messages.message("invalid_url")
    assert api.validate_calls == []  # no GitHub call
    assert api.h.ledger.loads == 0  # the meter was not touched
    assert_nothing_started(api)


async def test_ac03_repo_not_found(api):
    async def missing(coords, ref):
        raise RepoNotFound

    api.h.deps.validate = missing
    async with client(api.app) as c:
        response = await post_scan(c)

    assert response.status_code == 404
    assert error_of(response).message == "Repository not found or not public."
    assert_nothing_started(api)


async def test_ac04_repo_too_large_message_states_the_caps(api):
    async def big(coords, ref):
        raise RepoTooLarge(max_mb=50, max_files=5000, mb=120.5, files=9000)

    api.h.deps.validate = big
    async with client(api.app) as c:
        response = await post_scan(c)

    assert response.status_code == 413
    err = error_of(response)
    assert err.code == "repo_too_large"
    assert err.message == messages.repo_too_large(max_mb=50, max_files=5000, mb=120.5, files=9000)
    assert "50 MB" in err.message and "5000 files" in err.message
    assert err.details["max_mb"] == 50 and err.details["max_files"] == 5000
    assert_nothing_started(api)


@pytest.mark.parametrize(
    "detail", ["no .py files", "no dependency manifest at the repository root"]
)
async def test_ac05_not_a_python_project(api, detail):
    async def not_python(coords, ref):
        raise NotPythonProject(detail)

    api.h.deps.validate = not_python
    async with client(api.app) as c:
        response = await post_scan(c)

    assert response.status_code == 422
    assert error_of(response).message == f"Not a Python project: {detail}."
    assert_nothing_started(api)


async def test_github_unavailable_is_502(api):
    async def down(coords, ref):
        raise GitHubUnavailable

    api.h.deps.validate = down
    async with client(api.app) as c:
        response = await post_scan(c)

    assert response.status_code == 502
    assert error_of(response).message == "GitHub did not respond; try again later."
    assert_nothing_started(api)


@pytest.mark.parametrize(
    "body, detail",
    [
        (b"{not json", "the body must be JSON"),
        (b"[1]", "the body must be a JSON object"),
        (b"{}", "repo_url is required"),
        (json.dumps({"repo_url": 5}).encode(), "repo_url must be a string"),
        (json.dumps({"repo_url": URL, "ref": "a/../b"}).encode(), "ref is not valid"),
        (
            json.dumps({"repo_url": URL, "SECRET-FIELD-NAME": 1}).encode(),
            "the body has an unexpected field",
        ),
    ],
)
async def test_request_invalid_names_the_field_and_never_echoes_input(api, body, detail):
    async with client(api.app) as c:
        response = await c.post("/api/scans", content=body)

    assert response.status_code == 422
    err = error_of(response)
    assert err.code == "request_invalid"
    assert err.message == messages.request_invalid(detail)
    assert "SECRET-FIELD-NAME" not in response.text
    assert_nothing_started(api)


# ---- a live scan end to end ----------------------------------------------------------------


async def test_ac32_ac61_report_validates_and_download_has_identical_bytes(api):
    async with client(api.app) as c:
        accepted = await post_scan(c, ref="main")
        assert accepted.status_code == 202
        body = ScanAccepted.model_validate_json(accepted.content)
        assert body.mode == "live"
        assert body.status_url == f"/api/scans/{body.scan_id}"
        assert body.events_url == f"{body.status_url}/events"
        assert body.report_url == f"{body.status_url}/report"
        await finish(api, accepted)

        status = await c.get(body.status_url)
        plain = await c.get(body.report_url)
        download = await c.get(body.report_url + "?download=1")

    assert api.validate_calls == [(RepoCoordinates(owner="octo", name="demo"), "main")]
    assert status.json()["state"] == "completed"
    assert status.json()["outcome"] == "findings"
    assert [s["name"] for s in status.json()["steps"]] == [
        "resolve",
        "osv",
        "research",
        "analysis",
        "reasoning",
        "report",
    ]
    assert plain.status_code == 200
    assert plain.headers["content-type"].startswith("application/json")
    assert "content-disposition" not in plain.headers
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert validate_document(plain.json(), schema) == []
    report = ScanReport.model_validate_json(plain.content)
    assert report.findings[0].verdict == "likely reachable"
    assert download.content == plain.content  # AC-61: same bytes
    assert download.headers["content-disposition"] == (
        f'attachment; filename="proveitreachable-demo-{SHA[:7]}.json"'
    )
    assert api.ctx.live_in_flight == 0
    assert api.h.ledger.state.reservations == []  # settled


async def test_report_not_ready_then_scan_not_found(tmp_path, monkeypatch):
    gate = asyncio.Event()
    api = make_api(tmp_path, monkeypatch, gate=gate)
    async with client(api.app) as c:
        accepted = await post_scan(c)
        scan_id = ScanAccepted.model_validate_json(accepted.content).scan_id
        early = await c.get(f"/api/scans/{scan_id}/report")
        status = await c.get(f"/api/scans/{scan_id}")
        gate.set()
        await finish(api, accepted)
        missing = await c.get("/api/scans/" + "0" * 32)
        missing_report = await c.get("/api/scans/nope/report")

    assert early.status_code == 409
    assert error_of(early).message == messages.message("report_not_ready")
    assert status.json()["state"] in ("queued", "running")
    assert status.json()["outcome"] is None
    assert missing.status_code == 404 and missing_report.status_code == 404
    assert error_of(missing).message == messages.message("scan_not_found")


async def test_ac43_unexpected_exception_is_internal_error_without_a_stack_trace(api):
    async def boom(coords, ref):
        raise RuntimeError("secret internal detail /srv/app.py line 3")

    api.h.deps.validate = boom
    async with client(api.app) as c:
        response = await post_scan(c)

    assert response.status_code == 500
    err = error_of(response)
    assert err.code == "internal_error"
    assert err.message.startswith("Something went wrong (error id ")
    error_id = err.message.split("error id ")[1].rstrip(").")
    assert len(error_id) == 12
    assert "Traceback" not in response.text and "secret internal detail" not in response.text
    assert_nothing_started(api)  # the reservation was released


# ---- rate limit, spend, concurrency (AC-50, AC-51) -----------------------------------------


async def test_ac50_fourth_scan_in_an_hour_is_rate_limited(api):
    async with client(api.app, "198.51.100.7") as c:
        for _ in range(3):
            assert (await finish(api, await post_scan(c))) is not None
        saves = api.h.ledger.saves
        fourth = await post_scan(c)
        saves_after = api.h.ledger.saves
    async with client(api.app, "198.51.100.8") as other:
        elsewhere = await post_scan(other)

    assert fourth.status_code == 429
    err = error_of(fourth)
    assert err.code == "rate_limited"
    retry = int(fourth.headers["retry-after"])
    assert 0 < retry <= 3600 and err.retry_after_s == retry
    assert err.message == messages.rate_limited(math.ceil(retry / 60))
    assert saves_after == saves  # nothing was reserved for the 4th
    assert elsewhere.status_code == 202  # another client is not limited
    await finish(api, elsewhere)


async def test_ac51_ledger_unavailable_pauses_live_scans_and_calls_nothing_paid(api):
    api.h.ledger.fail_loads = True
    async with client(api.app) as c:
        response = await post_scan(c)
        config = await c.get("/api/config")
        health = await c.get("/api/health")

    assert response.status_code == 503
    err = error_of(response)
    assert err.code == "live_scans_paused"
    assert err.message == messages.message("live_scans_paused")
    assert api.h.llm.calls == []
    assert api.h.osv_transport.requests == [] and api.h.tavily_transport.requests == []
    assert api.validate_calls == []
    assert len(api.ctx.jobs) == 0
    assert config.json()["live_scans_enabled"] is False
    assert config.json()["live_scans_paused_reason"] == messages.message("live_scans_paused")
    assert health.json()["live_scans"] == "paused"


async def test_ac51_allowance_at_cap_pauses_live_scans(api):
    api.h.ledger.state = make_state(
        datetime.now(UTC),
        {"local": {"nebius": 10.0, "tavily": 5.0}},
        spent={"local": {"nebius": 10.0, "tavily": 5.0}},
    )
    async with client(api.app) as c:
        response = await post_scan(c)

    assert response.status_code == 503
    assert error_of(response).code == "live_scans_paused"
    assert api.h.llm.calls == [] and api.validate_calls == []
    assert len(api.ctx.jobs) == 0


async def test_ac51_live_scans_enabled_false_disables_live_scans(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, live_scans_enabled=False)
    async with client(api.app) as c:
        response = await post_scan(c)
        config = await c.get("/api/config")
        health = await c.get("/api/health")

    assert response.status_code == 503
    assert error_of(response).code == "live_scans_paused"
    assert api.h.ledger.loads == 0 and api.h.llm.calls == [] and api.validate_calls == []
    assert config.json()["live_scans_enabled"] is False
    assert config.json()["live_scans_paused_reason"]
    assert health.json()["live_scans"] == "disabled"


async def test_server_busy_when_max_concurrent_scans_are_running(tmp_path, monkeypatch):
    gate = asyncio.Event()
    api = make_api(tmp_path, monkeypatch, gate=gate, max_concurrent_scans=1)
    async with client(api.app, "198.51.100.1") as a, client(api.app, "198.51.100.2") as b:
        first = await post_scan(a)
        second = await post_scan(b)
        reservations_while_busy = len(api.h.ledger.state.reservations)
        gate.set()
        await finish(api, first)
        third = await post_scan(b)
        await finish(api, third)

    assert first.status_code == 202
    assert second.status_code == 429
    err = error_of(second)
    assert err.code == "server_busy" and err.message == messages.message("server_busy")
    assert second.headers["retry-after"] == "60"
    assert reservations_while_busy == 1  # the refused request's reservation was released
    assert len(api.ctx.jobs) == 2
    assert third.status_code == 202
    assert api.ctx.live_in_flight == 0


# ---- config, health, schema (AC-76) --------------------------------------------------------


async def test_ac76_health_reports_remaining_fractions_and_no_secrets(tmp_path, monkeypatch):
    api = make_api(
        tmp_path,
        monkeypatch,
        secrets={"nebius_api_key": "SENTINEL-nebius-key", "tavily_api_key": "SENTINEL-tavily-key"},
    )
    api.h.ledger.state = make_state(
        datetime.now(UTC),
        {"local": {"nebius": 10.0, "tavily": 5.0}},
        spent={"local": {"nebius": 2.5, "tavily": 6.0}},
    )
    async with client(api.app) as c:
        before = await c.get("/api/health")
        await api.h.deps.meter.refresh()
        after = await c.get("/api/health")

    assert before.json()["budget_remaining"] is None  # no ledger loaded yet
    health = HealthResponse.model_validate_json(after.content)
    assert health.status == "ok" and health.version == __version__
    assert health.budget_remaining == {"nebius": 0.75, "tavily": 0.0}  # clamped at 0
    assert "SENTINEL" not in after.text


async def test_lifespan_loads_the_ledger_so_health_has_fractions(api):
    async with api.app.router.lifespan_context(api.app), client(api.app) as c:
        health = await c.get("/api/health")

    assert health.json()["budget_remaining"] == {"nebius": 1.0, "tavily": 1.0}
    assert health.json()["live_scans"] == "enabled"


async def test_meter_refresh_failure_pauses_live_scans_and_recovery_resumes_them(api):
    api.h.ledger.fail_loads = 1
    await api.h.deps.meter.refresh()
    async with client(api.app) as c:
        paused = (await c.get("/api/health")).json()["live_scans"]
        await api.h.deps.meter.refresh()
        resumed = (await c.get("/api/health")).json()["live_scans"]

    assert (paused, resumed) == ("paused", "enabled")


async def test_config_has_all_four_limits_features_and_no_eval_without_a_scored_run(api):
    async with client(api.app) as c:
        response = await c.get("/api/config")

    config = AppConfigResponse.model_validate_json(response.content)
    assert config.version == __version__
    assert config.live_scans_enabled is True and config.live_scans_paused_reason is None
    assert config.limits == {
        "max_repo_mb": 50,
        "max_repo_files": 5000,
        "live_scans_per_client_hour": 3,
        "live_scans_per_client_day": 10,
    }
    assert config.features == {"progress_feed": True, "markdown_export": True, "proof": False}
    assert config.eval is None  # latest.json does not exist


@pytest.mark.parametrize(
    "summary, expect_figure",
    [
        (LATEST, True),
        ({**LATEST, "scored": False}, False),
        ({**LATEST, "precision": None}, False),
        ({"scored": True}, False),  # does not validate
    ],
)
async def test_config_eval_only_from_a_scored_run_with_precision(
    api, tmp_path, summary, expect_figure
):
    (tmp_path / "latest.json").write_text(json.dumps(summary), encoding="utf-8")
    async with client(api.app) as c:
        config = (await c.get("/api/config")).json()

    if expect_figure:
        assert config["eval"] == {
            "precision": 1.0,
            "n": 14,
            "run_date": "2026-10-25",
            "model": LATEST["model"],
        }
    else:
        assert config["eval"] is None


async def test_features_follow_the_feature_flags(tmp_path, monkeypatch):
    api = make_api(
        tmp_path, monkeypatch, feature_progress_feed=False, feature_markdown_export=False
    )
    async with client(api.app) as c:
        config = (await c.get("/api/config")).json()

    assert config["features"] == {"progress_feed": False, "markdown_export": False, "proof": False}


async def test_schema_route_serves_the_published_report_schema(api):
    async with client(api.app) as c:
        response = await c.get("/api/schema/report")

    assert response.status_code == 200
    assert response.json() == json.loads(SCHEMA.read_text(encoding="utf-8"))


# ---- static files --------------------------------------------------------------------------


async def test_static_root_serves_index_and_js_is_text_javascript(api):
    async with client(api.app) as c:
        index = await c.get("/")
        js = await c.get("/js/app.js")
        css = await c.get("/css/app.css")

    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert index.content == (WEB / "index.html").read_bytes()
    assert js.headers["content-type"].startswith("text/javascript")
    assert css.headers["content-type"].startswith("text/css")


def test_mimetypes_for_js_css_and_woff2_are_registered():
    import mimetypes

    assert mimetypes.guess_type("a.js")[0] == "text/javascript"
    assert mimetypes.guess_type("a.css")[0] == "text/css"
    assert mimetypes.guess_type("a.woff2")[0] == "font/woff2"


# ---- logs ----------------------------------------------------------------------------------


async def test_logs_hold_only_the_salted_client_hash(api):
    async with client(api.app, "203.0.113.77") as c:
        with capture_json_logs(logging.INFO) as buffer:
            await finish(api, await post_scan(c))
            for _ in range(3):
                await post_scan(c)  # the third of these is rate limited and logged
        await api.ctx.jobs.shutdown()
    lines = log_lines(buffer)

    assert "203.0.113.77" not in buffer.getvalue()
    clients = {line["client"] for line in lines if "client" in line}
    assert clients and all(len(value) == 16 for value in clients)
    assert any(line.get("event") == "scan_refused" for line in lines)


# ---- the serve command ---------------------------------------------------------------------


@pytest.fixture
def serve_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID", "TAVILY_API_KEY", "LIVE_SCANS_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SPEND_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    calls: list[tuple[object, dict]] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.append((app, kw)))
    yield calls
    root.handlers[:] = handlers
    root.setLevel(level)


def test_serve_requires_the_live_scan_set_when_live_scans_are_enabled(serve_env, capsys):
    code = main(["serve"])

    assert code == 1
    assert "Missing required environment variable: NEBIUS_API_KEY" in capsys.readouterr().err
    assert serve_env == []


def test_serve_needs_nothing_when_live_scans_are_disabled(serve_env, monkeypatch):
    monkeypatch.setenv("LIVE_SCANS_ENABLED", "false")

    code = main(["serve", "--host", "0.0.0.0", "--port", "7860"])

    assert code == 0
    ((app, kwargs),) = serve_env
    assert kwargs == {
        "host": "0.0.0.0",
        "port": 7860,
        "workers": 1,
        "log_config": None,
        "access_log": False,
    }
    assert app.state.ctx.deps is None


def test_serve_builds_hosted_deps_when_live_scans_are_enabled(serve_env, monkeypatch):
    for name, value in {
        "NEBIUS_API_KEY": "serve-nebius-key",
        "NEBIUS_PROJECT_ID": "serve-project",
        "TAVILY_API_KEY": "serve-tavily-key",
    }.items():
        monkeypatch.setenv(name, value)
    spenders: list[str] = []

    def fake_build_deps(settings, *, spender="local", **kw):
        spenders.append(spender)
        return make_harness(Path.cwd() / "h", executor="fake").deps

    monkeypatch.setattr("pir.api.app.build_deps", fake_build_deps)

    assert main(["serve"]) == 0
    assert spenders == ["hosted"]
    ((app, kwargs),) = serve_env
    assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 8000
    assert app.state.ctx.deps is not None


# ---- Markdown export (AC-62 server part, AC-63, AC-64) --------------------------


async def test_ac62_report_md_renders_the_finished_scan_and_download_names_the_file(api):
    async with client(api.app) as c:
        accepted = await post_scan(c)
        body = ScanAccepted.model_validate_json(accepted.content)
        await finish(api, accepted)
        report = ScanReport.model_validate_json((await c.get(body.report_url)).content)
        plain = await c.get(body.report_url + ".md")
        download = await c.get(body.report_url + ".md?download=1")

    assert plain.status_code == 200
    assert plain.headers["content-type"].startswith("text/markdown")
    assert "content-disposition" not in plain.headers
    assert download.content == plain.content
    assert download.headers["content-disposition"] == (
        f'attachment; filename="proveitreachable-demo-{SHA[:7]}.md"'
    )
    text = plain.text
    assert text == render_markdown(report)
    finding = report.findings[0]
    i_verdict = text.index(f"### 1. {finding.verdict}")
    i_conf = text.index(f"**Confidence:** {finding.confidence}", i_verdict)
    i_lim = text.index("**Limitations**", i_conf)
    assert i_verdict < i_conf < i_lim
    assert "- Call path:" in text
    for loc in finding.source_locations:
        assert f"`{loc.file}:{loc.line}`" in text
    for link in finding.advisory_links:
        assert f"<{link.url}>" in text
    # the same security headers as every other response
    assert plain.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in plain.headers["content-security-policy"]
    assert plain.headers["referrer-policy"] == "no-referrer"


async def test_report_md_not_ready_then_scan_not_found(tmp_path, monkeypatch):
    gate = asyncio.Event()
    api = make_api(tmp_path, monkeypatch, gate=gate)
    async with client(api.app) as c:
        accepted = await post_scan(c)
        scan_id = ScanAccepted.model_validate_json(accepted.content).scan_id
        early = await c.get(f"/api/scans/{scan_id}/report.md")
        gate.set()
        await finish(api, accepted)
        missing = await c.get("/api/scans/" + "0" * 32 + "/report.md")

    assert early.status_code == 409
    assert error_of(early).message == messages.message("report_not_ready")
    assert missing.status_code == 404
    assert error_of(missing).message == messages.message("scan_not_found")


async def test_ac63_report_md_works_for_a_replay_job_with_no_live_calls(tmp_path):
    from pir.replay import ReplayStore

    fixtures = ROOT / "tests" / "fixtures" / "replays"

    async def no_sleep(_seconds: float) -> None:
        return None

    h = make_harness(tmp_path / "h")
    app = create_app(
        h.deps,
        web_dir=WEB,
        eval_path=tmp_path / "latest.json",
        replay_store=ReplayStore(fixtures, sleep=no_sleep),
    )
    slug = "fixture-yaml"
    async with client(app) as c:
        accepted = await c.post(f"/api/demos/{slug}/replay")
        scan_id = await finish(SimpleNamespace(ctx=app.state.ctx), accepted)
        md = await c.get(f"/api/scans/{scan_id}/report.md")
        js = await c.get(f"/api/scans/{scan_id}/report")

    assert md.status_code == 200 and js.status_code == 200
    report = ScanReport.model_validate_json(js.content)
    assert md.text == render_markdown(report)
    assert md.text.startswith(f"# ProveItReachable report: {report.repo.owner}/")
    assert h.osv_transport.requests == [] and h.tavily_transport.requests == []
    assert h.llm.calls == [] and h.executor.calls == []


async def test_ac64_report_md_is_absent_when_the_feature_is_off_and_json_still_works(
    tmp_path, monkeypatch
):
    api = make_api(tmp_path, monkeypatch, feature_markdown_export=False)
    async with client(api.app) as c:
        accepted = await post_scan(c)
        body = ScanAccepted.model_validate_json(accepted.content)
        await finish(api, accepted)
        md = await c.get(body.report_url + ".md")
        md_download = await c.get(body.report_url + ".md?download=1")
        js = await c.get(body.report_url + "?download=1")
        config = await c.get("/api/config")

    assert md.status_code == 404 and md_download.status_code == 404
    assert "text/markdown" not in md.headers.get("content-type", "")
    assert "content-security-policy" in md.headers  # the headers cover the 404 too
    assert js.status_code == 200
    ScanReport.model_validate_json(js.content)
    assert config.json()["features"]["markdown_export"] is False
