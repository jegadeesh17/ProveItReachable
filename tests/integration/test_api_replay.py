"""The demo routes through `httpx.ASGITransport` (M2-TASK-03).

Covers AC-46 (API), AC-47, AC-48, AC-49 (computed pacing), AC-52, AC-63 (server part), AC-78
(replay part) and the demo slug allowlist (finding 5).
"""

import asyncio
import builtins
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fakes.deps import make_harness
from pydantic import TypeAdapter

from pir import messages
from pir.api.app import create_app
from pir.models.api import (
    DemoSummary,
    ErrorResponse,
    ProgressEvent,
    ScanAccepted,
    ScanStatusResponse,
)
from pir.models.recording import ScanRecording
from pir.models.report import ScanReport
from pir.ratelimit import Allowed
from pir.replay import ReplayStore, replay_schedule
from pir.spend import Refused

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "replays"
SLUG = "fixture-yaml"
LIVE_URL = "https://github.com/octo/demo"


def recorded() -> ScanRecording:
    return ScanRecording.model_validate_json((FIXTURES / f"{SLUG}.json").read_text("utf-8"))


def make_api(tmp_path, *, gate: asyncio.Event | None = None, **settings_kw):
    """An app on fakes. Replays sleep on `gate` when given, else not at all."""

    async def sleep(_seconds: float) -> None:
        if gate is not None:
            await gate.wait()

    h = make_harness(tmp_path / "h", **settings_kw)
    app = create_app(
        h.deps,
        web_dir=ROOT / "web",
        eval_path=tmp_path / "latest.json",
        replay_store=ReplayStore(FIXTURES, sleep=sleep),
    )
    return SimpleNamespace(h=h, app=app, ctx=app.state.ctx)


def client(app, host: str = "203.0.113.9") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(host, 4000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def error_of(response: httpx.Response):
    return ErrorResponse.model_validate_json(response.content).error


async def start(c: httpx.AsyncClient, slug: str = SLUG) -> httpx.Response:
    return await c.post(f"/api/demos/{slug}/replay")


async def finish(api, response: httpx.Response) -> str:
    scan_id = ScanAccepted.model_validate_json(response.content).scan_id
    await api.ctx.jobs.get(scan_id).task
    return scan_id


@pytest.fixture
def api(tmp_path):
    return make_api(tmp_path)


def assert_no_calls(api) -> None:
    h = api.h
    assert h.osv_transport.requests == []
    assert h.tavily_transport.requests == []
    assert h.llm.calls == []
    assert h.executor.calls == []
    assert (h.ledger.loads, h.ledger.saves, h.ledger.save_attempts) == (0, 0, 0)


# ---- list and start (AC-46, AC-48, AC-63) ---------------------------------------------------


async def test_ac46_get_demos_lists_the_recordings_as_demo_summaries(api):
    async with client(api.app) as c:
        response = await c.get("/api/demos")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    [demo] = TypeAdapter(list[DemoSummary]).validate_json(response.content)
    rec = recorded()
    assert (demo.slug, demo.repo_url, demo.commit_sha) == (
        SLUG,
        rec.report.repo.url,
        rec.report.repo.commit_sha,
    )
    assert demo.by_verdict == rec.report.summary.by_verdict
    assert "content-security-policy" in response.headers  # the headers cover it too


async def test_ac46_get_demos_with_no_recordings_is_an_empty_list(tmp_path):
    h = make_harness(tmp_path / "h")
    app = create_app(
        h.deps,
        web_dir=ROOT / "web",
        eval_path=tmp_path / "latest.json",
        replay_store=ReplayStore(tmp_path / "none"),
    )
    async with client(app) as c:
        response = await c.get("/api/demos")
    assert (response.status_code, response.json()) == (200, [])


async def test_ac48_ac63_replay_report_and_events_equal_the_recording(api):
    rec = recorded()
    async with client(api.app) as c:
        response = await start(c)
        assert response.status_code == 202
        accepted = ScanAccepted.model_validate_json(response.content)
        assert accepted.mode == "replay"
        assert accepted.status_url == f"/api/scans/{accepted.scan_id}"
        scan_id = await finish(api, response)

        status = ScanStatusResponse.model_validate_json(
            (await c.get(f"/api/scans/{scan_id}")).content
        )
        report_response = await c.get(f"/api/scans/{scan_id}/report")
        download = await c.get(f"/api/scans/{scan_id}/report?download=1")

    assert (status.mode, status.state, status.outcome) == (
        "replay",
        "completed",
        rec.report.outcome,
    )
    assert report_response.content == rec.report.model_dump_json(indent=2).encode()  # byte for byte
    assert ScanReport.model_validate_json(report_response.content) == rec.report
    assert download.content == report_response.content  # AC-63 (server part)
    assert download.headers["content-disposition"].startswith("attachment; filename=")
    assert download.headers["content-disposition"].endswith('.json"')
    job = api.ctx.jobs.get(scan_id)
    assert job.log == [e.event for e in rec.events]
    assert [e.seq for e in job.log] == list(range(1, len(rec.events) + 1))
    assert isinstance(job.log[0], ProgressEvent)


async def test_ac49_computed_pacing_of_the_longest_recording_is_under_180_seconds(api):
    rec = recorded()
    long_events = [
        e.model_copy(update={"offset_ms": e.offset_ms * 1000}) for e in rec.events
    ]  # a 26 000 s scan
    longest = rec.model_copy(update={"events": long_events})

    seconds = replay_schedule(longest, api.ctx.settings.replay_max_seconds)[-1]

    assert seconds <= api.ctx.settings.replay_max_seconds + 1e-9
    assert seconds < 180


# ---- costs nothing, no live limits (AC-47, AC-52) ------------------------------------------


async def test_ac47_a_replay_makes_zero_calls_and_leaves_the_meter_and_ledger_alone(api):
    async with client(api.app) as c:
        await finish(api, await start(c))

    assert_no_calls(api)
    assert api.ctx.live_in_flight == 0


async def test_ac52_replay_works_while_live_scans_are_paused(api, monkeypatch):
    async def paused(_scan_id):
        return Refused("paused")

    monkeypatch.setattr(api.ctx.deps.meter, "status", lambda: "paused")
    monkeypatch.setattr(api.ctx.deps.meter, "reserve", paused)
    async with client(api.app) as c:
        refused = await c.post("/api/scans", json={"repo_url": LIVE_URL})
        replay = await start(c)
        await finish(api, replay)

    assert refused.status_code == 503 and error_of(refused).code == "live_scans_paused"
    assert replay.status_code == 202
    assert_no_calls(api)


async def test_ac52_replay_works_with_live_scans_switched_off_and_no_deps(tmp_path):
    from pir.config import Settings

    settings = Settings(_env_file=None, live_scans_enabled=False)
    app = create_app(
        settings=settings,
        web_dir=ROOT / "web",
        eval_path=tmp_path / "latest.json",
        replay_store=ReplayStore(FIXTURES, sleep=lambda _s: asyncio.sleep(0)),
    )
    assert app.state.ctx.deps is None
    async with client(app) as c:
        scan_id = ScanAccepted.model_validate_json((await start(c)).content).scan_id
        await app.state.ctx.jobs.get(scan_id).task
        report = await c.get(f"/api/scans/{scan_id}/report")
    assert report.status_code == 200


async def test_ac52_replay_works_for_a_client_that_is_live_scan_rate_limited(api):
    key = "203.0.113.9"
    while isinstance(api.ctx.limiter.check_and_consume(key, api.ctx.clock.now()), Allowed):
        pass  # use up every live-scan allowance of this client

    async with client(api.app, key) as c:
        refused = await c.post("/api/scans", json={"repo_url": LIVE_URL})
        replay = await start(c)
        await finish(api, replay)

    assert refused.status_code == 429  # live limits hit (rate_limited or server_busy per hour)
    assert error_of(refused).code == "rate_limited"
    assert replay.status_code == 202
    assert_no_calls(api)


async def test_replays_do_not_consume_live_scan_allowance(api):
    async with client(api.app) as c:
        for _ in range(5):
            await finish(api, await start(c))
    verdict = api.ctx.limiter.check_and_consume("203.0.113.9", api.ctx.clock.now())
    assert isinstance(verdict, Allowed)


# ---- slug allowlist (finding 5) ------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "%2e%2e",
        "%2e%2e%2fx",
        "..%2Fx",
        "%2e%2e%5cx",
        "..%5Cwindows",
        "UPPER",
        "fixture_yaml",
        "nonexistent",
        "x" * 41,
        "fixture-yaml%0A",
        "fixture-yaml.json",
        "%C3%BCber",
        "",
    ],
)
async def test_non_allowlisted_slugs_return_demo_not_found_without_filesystem_access(
    api, monkeypatch, slug
):
    def forbidden(*_a, **_k):
        raise AssertionError("filesystem access while handling a replay request")

    async with client(api.app) as c:
        with monkeypatch.context() as m:
            m.setattr(builtins, "open", forbidden)
            for name in ("open", "read_bytes", "read_text", "stat", "exists", "is_file", "resolve"):
                m.setattr(Path, name, forbidden)
            response = await c.post(f"/api/demos/{slug}/replay")

    assert response.status_code == 404
    error = error_of(response)
    assert (error.code, error.message) == ("demo_not_found", "That demo does not exist.")
    assert len(api.ctx.jobs) == 0
    assert_no_calls(api)


# ---- replay caps (AC-78, replay part) ------------------------------------------------------


async def test_ac78_third_running_replay_per_client_is_refused_with_retry_after(tmp_path):
    gate = asyncio.Event()
    api = make_api(tmp_path, gate=gate)
    async with client(api.app) as c, client(api.app, "198.51.100.7") as other:
        first = await start(c)
        second = await start(c)
        third = await start(c)
        elsewhere = await start(other)  # another client is unaffected
        gate.set()
        for response in (first, second, elsewhere):
            await finish(api, response)
        after = await start(c)  # a finished replay frees its slot
        await finish(api, after)

    assert (first.status_code, second.status_code, elsewhere.status_code) == (202, 202, 202)
    assert third.status_code == 429
    error = error_of(third)
    assert error.code == "too_many_requests"
    assert error.message == messages.too_many_requests(1)
    assert error.message == "Too many requests right now; try again in 1 min."
    assert third.headers["retry-after"] == "60" and error.retry_after_s == 60
    assert after.status_code == 202
    assert api.ctx.replay_limiter.running == 0


async def test_ac78_eleventh_running_replay_in_total_is_refused(tmp_path):
    gate = asyncio.Event()
    api = make_api(tmp_path, gate=gate)
    clients = [client(api.app, f"198.51.100.{i}") for i in range(1, 12)]
    try:
        responses = [await start(c) for c in clients]
        assert [r.status_code for r in responses[:10]] == [202] * 10
        refused = responses[10]
        assert refused.status_code == 429
        assert error_of(refused).code == "too_many_requests"
        assert error_of(refused).message == "Too many requests right now; try again in 1 min."
        assert refused.headers["retry-after"] == "60"
        assert api.ctx.jobs.running("replay") == 10
        gate.set()
        for response in responses[:10]:
            await finish(api, response)
    finally:
        gate.set()
        for c in clients:
            await c.aclose()
    assert api.ctx.replay_limiter.running == 0


async def test_ac78_thirty_first_start_in_an_hour_is_refused_until_the_hour_passes(
    tmp_path, frozen_clock
):
    api = make_api(tmp_path)
    api.ctx.clock = frozen_clock
    async with client(api.app) as c:
        for _ in range(30):
            response = await start(c)
            assert response.status_code == 202
            await finish(api, response)
            frozen_clock.advance(60)  # 30 starts within 30 minutes
        refused = await start(c)
        frozen_clock.advance(31 * 60)  # the first start is now over an hour old
        allowed = await start(c)
        await finish(api, allowed)

    assert refused.status_code == 429
    error = error_of(refused)
    assert error.code == "too_many_requests"
    assert refused.headers["retry-after"] == str(error.retry_after_s)
    assert error.retry_after_s == 60 * 60 - 30 * 60  # the oldest start was 30 minutes ago
    assert error.message == messages.too_many_requests(30)
    assert allowed.status_code == 202


async def test_refused_replays_start_no_job_and_consume_nothing(tmp_path):
    gate = asyncio.Event()
    api = make_api(tmp_path, gate=gate)
    async with client(api.app) as c:
        accepted = [await start(c), await start(c)]
        refused = [await start(c) for _ in range(5)]
        assert [r.status_code for r in refused] == [429] * 5
        assert len(api.ctx.jobs) == 2
        gate.set()
        for response in accepted:
            await finish(api, response)
    assert api.ctx.replay_limiter.running == 0


async def test_a_replay_that_fails_still_frees_its_slot(tmp_path, monkeypatch):
    api = make_api(tmp_path)

    async def boom(*_a, **_k):
        raise RuntimeError("playback failed")

    monkeypatch.setattr(api.ctx.replays, "play", boom)
    async with client(api.app) as c:
        scan_id = await finish(api, await start(c))
        status = ScanStatusResponse.model_validate_json(
            (await c.get(f"/api/scans/{scan_id}")).content
        )

    assert status.state == "failed"
    assert api.ctx.replay_limiter.running == 0
