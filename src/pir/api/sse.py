"""The progress feed: `GET /api/scans/{scan_id}/events` as server-sent events (ARCHITECTURE
section 8, AC-41, AC-42, AC-48, AC-78).

Each `ProgressEvent` is one frame: `id: <seq>`, `event: step|done|error`, `data: <JSON>`. The server
replays every stored event with `seq > Last-Event-ID`, then follows live ones and closes after
`done` or `error`. A `: keepalive` comment is sent whenever KEEPALIVE_S pass without an event.
Open streams are capped in total (MAX_SSE_STREAMS) and per client, separately from the live-scan
and replay limits.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from pir import messages
from pir.api.errors import ApiError, minutes_for
from pir.models.api import ProgressEvent

logger = logging.getLogger("pir.api")

KEEPALIVE_S = 15.0  # read on every request, so a test can shorten it
PER_CLIENT_STREAMS = 4
REFUSED_RETRY_AFTER_S = 60  # a stream slot frees when any open stream closes; ask for a minute
_LAST_EVENT_ID = re.compile(r"[0-9]{1,9}")


class StreamLimiter:
    """Open streams in total and per client. Separate from `RateLimiter` and `ReplayLimiter`, so
    a client limited for live scans can still open a stream. `release` must follow every
    successful `acquire`."""

    def __init__(self, *, max_streams: int = 40, per_client: int = PER_CLIENT_STREAMS) -> None:
        self._max_streams = max_streams
        self._per_client = per_client
        self._open: dict[str, int] = {}

    @property
    def open(self) -> int:
        return sum(self._open.values())

    def acquire(self, client_key: str) -> bool:
        """Take a slot. No `await` between the checks and the increment."""
        if self.open >= self._max_streams or self._open.get(client_key, 0) >= self._per_client:
            return False
        self._open[client_key] = self._open.get(client_key, 0) + 1
        return True

    def release(self, client_key: str) -> None:
        left = self._open.get(client_key, 0) - 1
        if left > 0:
            self._open[client_key] = left
        else:
            self._open.pop(client_key, None)


def parse_last_event_id(value: str | None) -> int:
    """The `Last-Event-ID` header as a sequence number; missing or malformed means 0 (all)."""
    if value is None:
        return 0
    value = value.strip()
    return int(value) if _LAST_EVENT_ID.fullmatch(value) else 0


def format_frame(event: ProgressEvent) -> str:
    """One SSE frame. `model_dump_json` is a single line, so no `data:` splitting is needed."""
    return f"id: {event.seq}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


async def sse_frames(
    events: AsyncIterator[ProgressEvent], keepalive_s: float
) -> AsyncIterator[str]:
    """Frames for `events`, a `: keepalive` comment after every `keepalive_s` of silence, ending
    after the first `done` or `error` event (or when `events` ends)."""
    pending: asyncio.Future[ProgressEvent] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(events.__anext__())  # type: ignore[arg-type]
            await asyncio.wait({pending}, timeout=keepalive_s)
            if not pending.done():
                yield ": keepalive\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                pending = None
                return
            pending = None
            yield format_frame(event)
            if event.type in ("done", "error"):
                return
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()


class _StreamResponse(StreamingResponse):
    """Releases the client's stream slot on every exit: the stream ending, a client disconnect, a
    cancelled task or an error, including a response that never started iterating."""

    def __init__(self, *args: object, on_close, **kwargs: object) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._on_close()


def create_sse_router(*, max_streams: int) -> APIRouter:
    """The events route. Registered only when FEATURE_PROGRESS_FEED is true (AC-42)."""
    from pir.api.routes import get_context  # routes imports this module

    router = APIRouter()  # create_router mounts it under /api
    limiter = StreamLimiter(max_streams=max_streams)

    @router.get("/scans/{scan_id}/events")
    async def scan_events(scan_id: str, request: Request) -> StreamingResponse:
        ctx = get_context(request)
        try:
            job = ctx.jobs.get(scan_id)
        except KeyError:
            raise ApiError("scan_not_found") from None
        key = ctx.client_key(request)
        client = ctx.hasher.hash(key)
        if not limiter.acquire(key):
            logger.info(
                "stream refused",
                extra={"event": "stream_refused", "outcome": "too_many_requests", "client": client},
            )
            raise ApiError(
                "too_many_requests",
                messages.too_many_requests(minutes_for(REFUSED_RETRY_AFTER_S)),
                retry_after_s=REFUSED_RETRY_AFTER_S,
            )
        try:
            after = parse_last_event_id(request.headers.get("last-event-id"))
            return _StreamResponse(
                sse_frames(job.stream(after), KEEPALIVE_S),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                on_close=lambda: limiter.release(key),
            )
        except BaseException:
            limiter.release(key)
            raise

    return router
