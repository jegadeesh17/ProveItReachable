"""The progress feed `GET /api/scans/{id}/events` (M2-TASK-05).

Covers AC-41 (stream part), AC-42 (server part), AC-48 (feed part) and AC-78 (stream part).
Finished jobs are read through `httpx.ASGITransport`; open (never-ending) streams, which that
transport would buffer forever, are driven with a raw ASGI call so the test can disconnect.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fakes.deps import Harness, make_harness, write_repo

from pir import messages
from pir.api import routes, sse
from pir.api.app import create_app
from pir.models.api import ErrorResponse, ProgressEvent, ScanAccepted
from pir.models.recording import ScanRecording
from pir.pipeline import STEP_ORDER
from pir.replay import ReplayStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "replays"
SLUG = "fixture-yaml"
URL = "https://github.com/octo/demo"
HOST = "203.0.113.9"


def make_api(tmp_path, monkeypatch, *, gate: asyncio.Event | None = None, **settings_kw):
    """An app on fakes. Live scans run the real pipeline on a local fixture repository; replays
    sleep on `gate` when given, else not at all."""

    async def sleep(_seconds: float) -> None:
        if gate is not None:
            await gate.wait()

    h = make_harness(tmp_path / "h", **settings_kw)
    repo = write_repo(tmp_path / "repo")

    async def validate(coords, ref):
        from test_api import VALIDATED

        return VALIDATED

    h.deps.validate = validate
    real_run_scan = routes.run_scan

    async def fixture_scan(deps, target, budget, **kwargs):
        return await real_run_scan(deps, Harness.target(repo), budget, **kwargs)

    monkeypatch.setattr(routes, "run_scan", fixture_scan)
    app = create_app(
        h.deps,
        web_dir=ROOT / "web",
        eval_path=tmp_path / "latest.json",
        replay_store=ReplayStore(FIXTURES, sleep=sleep),
    )
    return SimpleNamespace(h=h, app=app, ctx=app.state.ctx)


def client(app, host: str = HOST) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(host, 4000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def error_of(response: httpx.Response):
    return ErrorResponse.model_validate_json(response.content).error


def recorded() -> ScanRecording:
    return ScanRecording.model_validate_json((FIXTURES / f"{SLUG}.json").read_text("utf-8"))


def parse_frames(text: str) -> list[dict[str, str]]:
    """The `id`, `event` and `data` fields of every frame; comment-only blocks are skipped."""
    frames = []
    for block in text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if fields:
            frames.append(fields)
    return frames


def events_of(text: str) -> list[ProgressEvent]:
    return [ProgressEvent.model_validate_json(f["data"]) for f in parse_frames(text)]


async def start_replay(api) -> str:
    async with client(api.app) as c:
        response = await c.post(f"/api/demos/{SLUG}/replay")
    assert response.status_code == 202
    return ScanAccepted.model_validate_json(response.content).scan_id


async def finished_replay(api) -> str:
    scan_id = await start_replay(api)
    await api.ctx.jobs.get(scan_id).task
    return scan_id


async def finished_live(api) -> str:
    async with client(api.app) as c:
        response = await c.post("/api/scans", json={"repo_url": URL})
    assert response.status_code == 202
    scan_id = ScanAccepted.model_validate_json(response.content).scan_id
    await api.ctx.jobs.get(scan_id).task
    return scan_id


class RawStream:
    """One open GET to the events route, read at the ASGI level."""

    def __init__(
        self,
        app,
        scan_id: str,
        host: str,
        headers: dict[str, str] | None = None,
        spec_version: str = "2.3",  # what uvicorn reports: the disconnect arrives through receive
    ) -> None:
        self.messages: list[dict] = []
        self.broken = False  # sends raise OSError, as on ASGI 2.4 when the client is gone
        self._disconnect = asyncio.Event()
        self._sent_request = False
        raw_headers = [(b"host", b"test")] + [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ]
        path = f"/api/scans/{scan_id}/events"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": spec_version},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": raw_headers,
            "client": (host, 4000),
            "server": ("test", 80),
        }
        self.task = asyncio.get_running_loop().create_task(app(scope, self._receive, self._send))

    async def _receive(self) -> dict:
        if not self._sent_request:
            self._sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict) -> None:
        if self.broken:
            raise OSError("client gone")
        self.messages.append(message)

    def disconnect(self) -> None:
        self._disconnect.set()

    async def started(self) -> int:
        """The status code, once the response has started."""
        for _ in range(500):
            for m in self.messages:
                if m["type"] == "http.response.start":
                    return m["status"]
            await asyncio.sleep(0.01)
        raise AssertionError("the response never started")

    @property
    def headers(self) -> dict[str, str]:
        start = next(m for m in self.messages if m["type"] == "http.response.start")
        return {k.decode(): v.decode() for k, v in start["headers"]}

    @property
    def body(self) -> str:
        return b"".join(
            m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"
        ).decode()

    async def wait_for_body(self, text: str) -> None:
        for _ in range(500):
            if text in self.body:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{text!r} never arrived; got {self.body!r}")

    async def closed(self) -> None:
        await asyncio.wait_for(self.task, 5)


@pytest.fixture
def api(tmp_path, monkeypatch):
    return make_api(tmp_path, monkeypatch)


# ---- AC-41: live stream ---------------------------------------------------------------------


async def test_ac41_live_scan_stream_has_steps_in_order_and_a_final_done_event(api):
    scan_id = await finished_live(api)
    job = api.ctx.jobs.get(scan_id)

    async with client(api.app) as c:
        response = await c.get(f"/api/scans/{scan_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = parse_frames(response.text)
    events = events_of(response.text)
    assert events == job.log  # the whole log, in order
    assert [f["id"] for f in frames] == [str(i) for i in range(1, len(events) + 1)]
    assert [f["event"] for f in frames] == [e.type for e in events]
    assert events[-1].type == "done" and events[-1].outcome == job.report.outcome
    assert [e.type for e in events[:-1]] == ["step"] * (len(events) - 1)
    steps = [e for e in events if e.type == "step"]
    assert [s.step for s in steps if s.state == "running"] == list(STEP_ORDER)
    for name in STEP_ORDER:
        states = [s.state for s in steps if s.step == name]
        assert states[0] == "running" and states[-1] in ("done", "failed", "skipped")
        assert len(states) == 2
    terminal = [s for s in steps if s.state != "running"]
    assert [s.tools for s in terminal] == [list(st.tools) for st in job.report.steps]
    assert any(s.tools for s in terminal)  # at least one step used tools
    assert all(s.tools == [] for s in steps if s.state == "running")


async def test_ac41_sse_headers_and_security_headers_are_on_the_response(api):
    scan_id = await finished_replay(api)

    async with client(api.app) as c:
        response = await c.get(f"/api/scans/{scan_id}/events")

    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert "content-security-policy" in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_ac41_reconnect_with_last_event_id_resends_only_later_events(api):
    scan_id = await finished_replay(api)
    total = len(api.ctx.jobs.get(scan_id).log)
    assert total > 3

    async with client(api.app) as c:
        resumed = await c.get(f"/api/scans/{scan_id}/events", headers={"Last-Event-ID": "3"})
        at_end = await c.get(f"/api/scans/{scan_id}/events", headers={"Last-Event-ID": str(total)})
        junk = await c.get(f"/api/scans/{scan_id}/events", headers={"Last-Event-ID": "abc"})
        negative = await c.get(f"/api/scans/{scan_id}/events", headers={"Last-Event-ID": "-5"})

    assert [e.seq for e in events_of(resumed.text)] == list(range(4, total + 1))
    assert at_end.status_code == 200 and parse_frames(at_end.text) == []
    assert len(events_of(junk.text)) == total  # malformed means "from the start"
    assert len(events_of(negative.text)) == total


async def test_ac41_a_failed_job_streams_its_error_event_last_and_closes(api):
    async def boom(_sink):
        raise RuntimeError("secret internal detail")

    job = api.ctx.jobs.create("live", boom)
    await job.task

    async with client(api.app) as c:
        response = await c.get(f"/api/scans/{job.scan_id}/events")

    frames = parse_frames(response.text)
    assert [f["event"] for f in frames] == ["error"]
    event = events_of(response.text)[0]
    assert event.type == "error" and event.message.startswith("Something went wrong (error id")
    assert "secret internal detail" not in response.text


async def test_ac41_a_stream_opened_mid_scan_gets_stored_then_live_events(api):
    gate = asyncio.Event()

    async def runner(sink):
        from pir.models.report import ScanReport

        await sink.emit(_step(1, "running"))
        await gate.wait()
        await sink.emit(_step(2, "done"))
        return ScanReport.model_validate_json(recorded().report.model_dump_json())

    job = api.ctx.jobs.create("replay", runner)
    await asyncio.sleep(0.05)
    stream = RawStream(api.app, job.scan_id, HOST)
    assert await stream.started() == 200
    await stream.wait_for_body("id: 1\n")
    assert "id: 2\n" not in stream.body

    gate.set()
    await stream.closed()

    assert [f["id"] for f in parse_frames(stream.body)] == ["1", "2"]


def _step(seq: int, state: str) -> ProgressEvent:
    from datetime import UTC, datetime

    return ProgressEvent(
        seq=seq,
        scan_id="x",
        type="step",
        step="resolve",
        state=state,
        message=None,
        at=datetime.now(UTC),
        outcome=None,
        tools=[],
    )


# ---- AC-48: replay feed ---------------------------------------------------------------------


async def test_ac48_replay_stream_equals_the_recorded_events(api):
    scan_id = await finished_replay(api)
    rec = recorded()

    async with client(api.app) as c:
        response = await c.get(f"/api/scans/{scan_id}/events")

    events = events_of(response.text)
    assert events == [e.event for e in rec.events]
    assert [e.seq for e in events] == list(range(1, len(rec.events) + 1))
    assert events[-1].type == "done"


# ---- errors ---------------------------------------------------------------------------------


@pytest.mark.parametrize("scan_id", ["0" * 32, "nope", "a b"])
async def test_unknown_scan_id_is_scan_not_found(api, scan_id):
    async with client(api.app) as c:
        response = await c.get(f"/api/scans/{scan_id}/events")

    assert response.status_code == 404
    assert error_of(response).code == "scan_not_found"
    assert error_of(response).message == messages.message("scan_not_found")
    assert "content-security-policy" in response.headers


# ---- AC-42: feature flag --------------------------------------------------------------------


async def test_ac42_with_the_feed_off_the_route_is_absent_and_config_says_so(tmp_path, monkeypatch):
    api = make_api(tmp_path, monkeypatch, feature_progress_feed=False)
    scan_id = await finished_replay(api)

    async with client(api.app) as c:
        events = await c.get(f"/api/scans/{scan_id}/events")
        config = await c.get("/api/config")
        status = await c.get(f"/api/scans/{scan_id}")

    assert events.status_code == 404
    assert "text/event-stream" not in events.headers.get("content-type", "")
    assert config.json()["features"]["progress_feed"] is False
    assert status.status_code == 200  # polling still works


async def test_ac42_with_the_feed_on_config_reports_it(api):
    async with client(api.app) as c:
        config = await c.get("/api/config")
    assert config.json()["features"]["progress_feed"] is True


# ---- AC-78: stream caps ---------------------------------------------------------------------


async def open_job(api) -> str:
    """A replay that never finishes (its sleep waits on a gate nobody sets)."""
    return await start_replay(api)


@pytest.fixture
def held(tmp_path, monkeypatch):
    """An app whose replays stay running, so their streams stay open."""
    gate = asyncio.Event()
    return make_api(tmp_path, monkeypatch, gate=gate)


async def test_ac78_fifth_open_stream_for_a_client_is_429_with_retry_after(held):
    scan_id = await open_job(held)
    streams = [RawStream(held.app, scan_id, HOST) for _ in range(4)]
    for s in streams:
        assert await s.started() == 200

    fifth = RawStream(held.app, scan_id, HOST)
    assert await fifth.started() == 429
    await fifth.closed()
    body = ErrorResponse.model_validate_json(
        b"".join(m.get("body", b"") for m in fifth.messages if m["type"] == "http.response.body")
    ).error
    assert body.code == "too_many_requests"
    assert body.message == messages.too_many_requests(1)
    assert body.retry_after_s == 60
    assert fifth.headers["retry-after"] == "60"
    assert "content-security-policy" in fifth.headers

    other = RawStream(held.app, scan_id, "198.51.100.7")  # another client is unaffected
    assert await other.started() == 200

    for s in [*streams, other]:
        s.disconnect()
        await s.closed()


async def test_ac78_forty_first_global_stream_is_429(held):
    scan_id = await open_job(held)
    streams = []
    for i in range(10):  # 10 clients x 4 streams = 40, the global cap
        for _ in range(4):
            streams.append(RawStream(held.app, scan_id, f"198.51.100.{i}"))
    for s in streams:
        assert await s.started() == 200

    forty_first = RawStream(held.app, scan_id, "192.0.2.1")
    assert await forty_first.started() == 429
    await forty_first.closed()
    assert b"too_many_requests" in b"".join(
        m.get("body", b"") for m in forty_first.messages if m["type"] == "http.response.body"
    )
    assert dict(forty_first.headers)["retry-after"] == "60"

    for s in streams:
        s.disconnect()
        await s.closed()


async def test_ac78_a_slot_is_freed_when_the_client_disconnects(held):
    scan_id = await open_job(held)
    streams = [RawStream(held.app, scan_id, HOST) for _ in range(4)]
    for s in streams:
        assert await s.started() == 200
    blocked = RawStream(held.app, scan_id, HOST)
    assert await blocked.started() == 429

    streams[0].disconnect()
    await streams[0].closed()
    again = RawStream(held.app, scan_id, HOST)

    assert await again.started() == 200
    for s in [*streams[1:], again]:
        s.disconnect()
        await s.closed()


async def test_ac78_a_slot_is_freed_when_a_send_fails_on_a_gone_client(held, monkeypatch):
    from starlette.requests import ClientDisconnect

    monkeypatch.setattr(sse, "KEEPALIVE_S", 0.05)
    scan_id = await open_job(held)
    streams = [RawStream(held.app, scan_id, HOST, spec_version="2.4") for _ in range(4)]
    for s in streams:
        assert await s.started() == 200
    blocked = RawStream(held.app, scan_id, HOST)
    assert await blocked.started() == 429

    streams[0].broken = True  # the next keepalive cannot be sent
    with pytest.raises(ClientDisconnect):
        await streams[0].closed()
    again = RawStream(held.app, scan_id, HOST)

    assert await again.started() == 200
    for s in [*streams[1:], again]:
        s.disconnect()
    for s in streams[1:]:
        s.task.cancel()
    await again.closed()


async def test_ac78_a_slot_is_freed_when_the_stream_ends_normally(api):
    scan_id = await finished_replay(api)
    async with client(api.app) as c:
        for _ in range(6):  # more sequential streams than the per-client cap of 4
            response = await c.get(f"/api/scans/{scan_id}/events")
            assert response.status_code == 200


async def test_ac78_a_live_scan_rate_limited_client_can_still_open_a_stream(api):
    scan_id = await finished_replay(api)
    for _ in range(3):
        async with client(api.app) as c:
            await c.post("/api/scans", json={"repo_url": URL})
    async with client(api.app) as c:
        limited = await c.post("/api/scans", json={"repo_url": URL})
        events = await c.get(f"/api/scans/{scan_id}/events")

    assert limited.status_code == 429 and error_of(limited).code == "rate_limited"
    assert events.status_code == 200 and events_of(events.text)[-1].type == "done"


async def test_ac78_an_unknown_scan_does_not_take_a_stream_slot(held):
    scan_id = await open_job(held)
    for _ in range(6):
        missing = RawStream(held.app, "0" * 32, HOST)
        assert await missing.started() == 404
        await missing.closed()

    streams = [RawStream(held.app, scan_id, HOST) for _ in range(4)]
    for s in streams:
        assert await s.started() == 200
    for s in streams:
        s.disconnect()
        await s.closed()


# ---- keepalive and helpers ------------------------------------------------------------------


async def test_keepalive_comment_is_sent_while_the_job_is_quiet(held, monkeypatch):
    monkeypatch.setattr(sse, "KEEPALIVE_S", 0.05)
    scan_id = await open_job(held)
    stream = RawStream(held.app, scan_id, HOST)
    assert await stream.started() == 200

    await stream.wait_for_body(": keepalive\n\n")

    stream.disconnect()
    await stream.closed()
    assert stream.headers["cache-control"] == "no-cache"


async def test_sse_frames_stops_after_the_first_done_event():
    events = [_step(1, "running"), _done(2), _step(3, "done")]

    async def source():
        for e in events:
            yield e

    frames = [f async for f in sse.sse_frames(source(), 5.0)]

    assert [f.split("\n")[0] for f in frames] == ["id: 1", "id: 2"]
    assert frames[1].startswith("id: 2\nevent: done\ndata: {")
    assert frames[1].endswith("}\n\n")


def _done(seq: int) -> ProgressEvent:
    return _step(seq, "done").model_copy(update={"type": "done", "step": None, "state": None})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 0),
        ("", 0),
        ("0", 0),
        ("7", 7),
        (" 12 ", 12),
        ("-1", 0),
        ("1.5", 0),
        ("abc", 0),
        ("9" * 50, 0),
    ],
)
def test_parse_last_event_id(value, expected):
    assert sse.parse_last_event_id(value) == expected


def test_stream_limiter_counts_per_client_and_in_total():
    limiter = sse.StreamLimiter(max_streams=3, per_client=2)
    assert limiter.acquire("a") and limiter.acquire("a")
    assert not limiter.acquire("a")  # per client
    assert limiter.acquire("b")
    assert not limiter.acquire("c")  # global
    limiter.release("a")
    assert limiter.acquire("c")
    assert limiter.open == 3
    for key in ("a", "b", "c", "c", "c"):  # extra releases never go negative
        limiter.release(key)
    assert limiter.open == 0
