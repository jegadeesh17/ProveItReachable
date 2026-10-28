"""Security carry-over for the HTTP API (security review findings 5 and 8; ARCHITECTURE 22).

The 4 KB body cap on chunked bodies, the security headers on every kind of response, and the
client key behind a proxy, all through `httpx.ASGITransport`.
"""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fakes.deps import SHA, Harness, capture_json_logs, make_harness, write_repo

from pir.api import routes
from pir.api.app import create_app
from pir.api.security_headers import CSP, MAX_BODY_BYTES
from pir.github import RepoCoordinates, ValidatedTarget
from pir.models.api import ErrorResponse, ScanAccepted

ROOT = Path(__file__).resolve().parents[2]
URL = "https://github.com/octo/demo"
EXPECTED_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'self' https://huggingface.co"
)
VALIDATED = ValidatedTarget(
    coords=RepoCoordinates(owner="octo", name="demo"),
    default_branch="main",
    commit_sha=SHA,
    files_total=3,
    bytes_total=1000,
    py_files=1,
    manifests=["requirements.txt"],
)


def make_api(tmp_path, monkeypatch, **settings_kw):
    h = make_harness(tmp_path / "h", **settings_kw)
    repo = write_repo(tmp_path / "repo")
    validated: list[str] = []

    async def validate(coords, ref):
        validated.append(coords.name)
        return VALIDATED

    h.deps.validate = validate
    real_run_scan = routes.run_scan

    async def fixture_scan(deps, target, budget, **kwargs):
        return await real_run_scan(deps, Harness.target(repo), budget, **kwargs)

    monkeypatch.setattr(routes, "run_scan", fixture_scan)
    app = create_app(h.deps, web_dir=ROOT / "web", eval_path=tmp_path / "latest.json")
    return SimpleNamespace(h=h, app=app, ctx=app.state.ctx, validated=validated)


def client(app, host: str = "203.0.113.9") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(host, 4000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def api(tmp_path, monkeypatch):
    return make_api(tmp_path, monkeypatch)


# ---- the 4 KB body cap (finding 5) ---------------------------------------------------------


async def chunks(total: int, size: int = 1000):
    sent = 0
    while sent < total:
        piece = min(size, total - sent)
        yield b" " * piece
        sent += piece


async def test_chunked_body_over_4kb_without_content_length_is_refused(api):
    seen: list[dict[bytes, bytes]] = []

    async def spy(scope, receive, send):
        if scope["type"] == "http":
            seen.append(dict(scope["headers"]))
        await api.app(scope, receive, send)

    transport = httpx.ASGITransport(app=spy, client=("203.0.113.9", 4000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.post(
            "/api/scans", content=chunks(MAX_BODY_BYTES + 904), headers={"x-test": "chunked"}
        )

    assert b"content-length" not in seen[0]  # really a chunked upload
    assert response.status_code == 413
    err = ErrorResponse.model_validate_json(response.content).error
    assert err.code == "request_invalid" and "4 KB" in err.message
    assert len(api.ctx.jobs) == 0 and api.validated == []
    assert api.h.ledger.loads == 0


async def test_declared_content_length_over_4kb_is_refused_before_the_app_runs(api):
    async with client(api.app) as c:
        response = await c.post("/api/scans", content=b"x" * (MAX_BODY_BYTES + 1))

    assert response.status_code == 413
    assert ErrorResponse.model_validate_json(response.content).error.code == "request_invalid"
    assert api.validated == [] and len(api.ctx.jobs) == 0


async def test_a_body_of_exactly_4kb_is_not_refused_for_size(api):
    body = json.dumps({"repo_url": URL, "ref": None}).encode()
    body = body + b" " * (MAX_BODY_BYTES - len(body))
    assert len(body) == MAX_BODY_BYTES

    async with client(api.app) as c:
        response = await c.post("/api/scans", content=body)

    assert response.status_code == 202
    await api.ctx.jobs.shutdown()


async def test_chunked_body_under_4kb_is_accepted(api):
    async def body():
        yield b'{"repo_url": '
        yield json.dumps(URL).encode()
        yield b"}"

    async with client(api.app) as c:
        response = await c.post("/api/scans", content=body())

    assert response.status_code == 202
    await api.ctx.jobs.shutdown()


# ---- security headers on every response ----------------------------------------------------


def assert_security_headers(response: httpx.Response) -> None:
    assert response.headers["content-security-policy"] == EXPECTED_CSP
    assert (
        "frame-ancestors 'self' https://huggingface.co"
        in response.headers["content-security-policy"]
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert len(response.headers.get_list("content-security-policy")) == 1


def test_the_csp_constant_is_the_one_in_the_architecture():
    assert CSP == EXPECTED_CSP


async def test_headers_on_json_errors_static_files_and_404s(api):
    async def boom(coords, ref):
        raise RuntimeError("unexpected")

    async with client(api.app) as c:
        responses = {
            "config": await c.get("/api/config"),
            "health": await c.get("/api/health"),
            "schema": await c.get("/api/schema/report"),
            "scan_not_found": await c.get("/api/scans/" + "0" * 32),
            "invalid_url": await c.post("/api/scans", json={"repo_url": "nope"}),
            "request_invalid": await c.post("/api/scans", content=b"{"),
            "too_large": await c.post("/api/scans", content=b"x" * 5000),
            "index": await c.get("/"),
            "script": await c.get("/js/app.js"),
            "stylesheet": await c.get("/css/app.css"),
            "static_404": await c.get("/no-such-file.txt"),
            "api_404": await c.get("/api/no-such-route"),
            "method_not_allowed": await c.put("/api/config"),
            "traversal": await c.get("/..%2f..%2fpyproject.toml"),
        }
        api.h.deps.validate = boom
        responses["internal_error"] = await c.post("/api/scans", json={"repo_url": URL})

    assert responses["config"].status_code == 200
    assert responses["static_404"].status_code == 404
    assert responses["api_404"].status_code == 404
    assert responses["method_not_allowed"].status_code == 405
    assert responses["internal_error"].status_code == 500
    assert responses["traversal"].status_code in (400, 404)
    for name, response in responses.items():
        try:
            assert_security_headers(response)
        except AssertionError as exc:  # name the response that lacks them
            raise AssertionError(f"{name}: {exc}") from exc


async def test_headers_on_a_download_and_on_a_rate_limited_response(api):
    async with client(api.app, "198.51.100.30") as c:
        for _ in range(3):
            accepted = await c.post("/api/scans", json={"repo_url": URL})
            await api.ctx.jobs.get(ScanAccepted.model_validate_json(accepted.content).scan_id).task
        limited = await c.post("/api/scans", json={"repo_url": URL})
        scan_id = ScanAccepted.model_validate_json(accepted.content).scan_id
        download = await c.get(f"/api/scans/{scan_id}/report?download=1")

    assert limited.status_code == 429 and download.status_code == 200
    assert_security_headers(limited)
    assert_security_headers(download)


# ---- client key behind a proxy (finding 8) -------------------------------------------------


async def post(c, xff: str | None = None) -> httpx.Response:
    headers = {"X-Forwarded-For": xff} if xff is not None else {}
    return await c.post("/api/scans", json={"repo_url": URL}, headers=headers)


async def settle_jobs(api) -> None:
    await api.ctx.jobs.shutdown()


async def test_spoofed_leftmost_xff_entry_does_not_evade_the_limit(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, trusted_proxy_hops=1, max_concurrent_scans=10)
    async with client(api.app, "10.0.0.1") as c:
        results = [await post(c, f"198.51.100.{i}, 192.0.2.50") for i in range(1, 5)]
    await settle_jobs(api)

    assert [r.status_code for r in results] == [202, 202, 202, 429]  # one client: 192.0.2.50


async def test_different_real_clients_behind_the_proxy_are_limited_separately(
    tmp_path, monkeypatch
):
    api = make_api(tmp_path, monkeypatch, trusted_proxy_hops=1, max_concurrent_scans=10)
    async with client(api.app, "10.0.0.1") as c:
        first = [await post(c, "6.6.6.6, 192.0.2.1") for _ in range(4)]
        other = await post(c, "6.6.6.6, 192.0.2.2")
    await settle_jobs(api)

    assert first[-1].status_code == 429
    assert other.status_code == 202


async def test_xff_with_fewer_entries_than_trusted_hops_uses_the_connection_address(
    tmp_path, monkeypatch
):
    api = make_api(tmp_path, monkeypatch, trusted_proxy_hops=2, max_concurrent_scans=10)
    async with client(api.app, "10.0.0.1") as c:
        # One entry only: the attacker-chosen value must not become the key.
        results = [await post(c, f"203.0.113.{i}") for i in range(1, 5)]
    async with client(api.app, "10.0.0.2") as other:
        elsewhere = await post(other, "203.0.113.1")
    await settle_jobs(api)

    assert [r.status_code for r in results] == [202, 202, 202, 429]  # keyed by 10.0.0.1
    assert elsewhere.status_code == 202  # keyed by 10.0.0.2


async def test_without_trusted_hops_xff_is_ignored(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, trusted_proxy_hops=0, max_concurrent_scans=10)
    async with client(api.app, "10.0.0.1") as c:
        results = [await post(c, f"203.0.113.{i}") for i in range(1, 5)]
    await settle_jobs(api)

    assert results[-1].status_code == 429


async def test_logs_contain_only_the_salted_hash_never_the_address_or_xff(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, trusted_proxy_hops=1, max_concurrent_scans=10)
    with capture_json_logs(logging.DEBUG) as buffer:
        async with client(api.app, "10.9.8.7") as c:
            for _ in range(4):
                await post(c, "6.6.6.6, 192.0.2.77")
        await settle_jobs(api)

    logged = buffer.getvalue()
    assert "192.0.2.77" not in logged and "10.9.8.7" not in logged and "6.6.6.6" not in logged
    assert '"client"' in logged
