"""ASGI middleware: security headers on every response, and the 4 KB request body cap.

Pure ASGI (not `BaseHTTPMiddleware`) so the body is counted as it arrives and the headers are
added at the message level, which covers streaming responses, errors and 404s alike.
"""

import logging
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pir.api.errors import ApiError, error_response, internal_error_response, request_invalid

logger = logging.getLogger("pir.api")

MAX_BODY_BYTES = 4096

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'self' https://huggingface.co"
)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode("ascii")),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
)
_SECURITY_NAMES = {name for name, _ in SECURITY_HEADERS}


class BodyTooLarge(Exception):
    """More than `MAX_BODY_BYTES` were received for one request."""


class SecurityHeadersMiddleware:
    """Adds the headers to every `http.response.start`. Also turns an exception nobody handled
    into `internal_error` (with an error id, no stack trace), so even that response has them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                kept = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in _SECURITY_NAMES
                ]
                message = {**message, "headers": [*kept, *SECURITY_HEADERS]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            if started:
                raise
            error_id = uuid.uuid4().hex[:12]
            logger.error("unhandled error in the API", exc_info=True, extra={"error_id": error_id})
            await internal_error_response(error_id)(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """Refuses a request body over 4 KB, counted on the bytes received: a declared
    `Content-Length` is checked up front, and a chunked body (no length) while it is read."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _too_large(self) -> ApiError:
        return request_invalid(f"the request body is over {self.max_bytes // 1024} KB", status=413)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break  # the server rejects a malformed length itself
                if declared > self.max_bytes:
                    await error_response(self._too_large())(scope, receive, send)
                    return
        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if started:
                raise
            await error_response(self._too_large())(scope, receive, tracking_send)
