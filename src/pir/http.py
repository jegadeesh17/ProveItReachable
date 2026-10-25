"""Outbound HTTP allowlist (ARCHITECTURE section 12): every host-side request goes through here.

`make_client` returns an `httpx.AsyncClient` that refuses any host outside its set, plain HTTP,
explicit ports and userinfo before a request is sent; never follows redirects; and reads each
response body in a stream with a byte cap.
"""

import httpx

MIB = 1024 * 1024

# Hosts and byte caps from ARCHITECTURE section 12.
GITHUB_HOST = "api.github.com"
OSV_HOST = "api.osv.dev"
GITHUB_MAX_BYTES = 5 * MIB
OSV_MAX_BYTES = 5 * MIB

_DROPPED_HEADERS = frozenset({"content-length", "content-encoding", "transfer-encoding"})


class OutboundBlocked(Exception):
    """A request was refused before being sent: host not allowed, not HTTPS, port or userinfo."""


class ResponseTooLarge(httpx.HTTPError):
    """A response body went over the client's byte cap."""


def _check_request(request: httpx.Request, allowed: frozenset[str]) -> None:
    url = request.url
    if url.scheme != "https":
        raise OutboundBlocked("only https requests are allowed")
    host = url.host.lower()
    if host not in allowed:
        raise OutboundBlocked(f"host {host!r} is not allowed")
    if url.port is not None or url.userinfo:
        raise OutboundBlocked("explicit ports and userinfo are not allowed")


class _CappedClient(httpx.AsyncClient):
    """Reads every non-streaming response body with a byte cap, counting decoded bytes."""

    def __init__(self, *, max_bytes: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._max_bytes = max_bytes

    async def send(self, request: httpx.Request, *, stream: bool = False, **kwargs):
        response = await super().send(request, stream=True, **kwargs)
        if stream:
            return response
        try:
            declared = response.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
                raise ResponseTooLarge(f"response over the {self._max_bytes} byte cap")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._max_bytes:
                    raise ResponseTooLarge(f"response over the {self._max_bytes} byte cap")
                chunks.append(chunk)
        finally:
            await response.aclose()
        headers = [
            (key, value)
            for key, value in response.headers.multi_items()
            if key.lower() not in _DROPPED_HEADERS
        ]
        return httpx.Response(
            response.status_code,
            headers=headers,
            content=b"".join(chunks),
            request=request,
            extensions=response.extensions,
        )


def make_client(
    allowed_hosts: set[str] | frozenset[str],
    max_bytes: int,
    timeout: float,
    *,
    headers: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """An `AsyncClient` bound to `allowed_hosts`, HTTPS only, no redirects, bodies capped.

    `transport` is for tests (an `httpx.MockTransport`); production leaves it unset.
    """
    if not allowed_hosts:
        raise ValueError("allowed_hosts must not be empty")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    allowed = frozenset(host.lower() for host in allowed_hosts)

    async def refuse_disallowed(request: httpx.Request) -> None:
        _check_request(request, allowed)

    return _CappedClient(
        max_bytes=max_bytes,
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        trust_env=False,
        headers=headers,
        transport=transport,
        event_hooks={"request": [refuse_disallowed]},
    )
