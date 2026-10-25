"""A recording `httpx.MockTransport` with routes, plus loaders for tests/fixtures/http/.

Routes map "METHOD https://host/path" (a query string is optional; the full URL is tried first,
then the URL without its query) to a response, an exception to raise, a callable taking the
request, or a list of those used in order (the last one repeats).
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "http"

Route = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


def fixture_text(*parts: str) -> str:
    return FIXTURES.joinpath(*parts).read_text(encoding="utf-8")


def fixture_json(*parts: str) -> Any:
    return json.loads(fixture_text(*parts))


def json_response(
    status: int, payload: Any, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


def fixture_response(
    status: int, *parts: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    return json_response(status, fixture_json(*parts), headers)


class RecordingTransport(httpx.MockTransport):
    """Serves `routes` and records every request that reaches the transport."""

    def __init__(self, routes: dict[str, Route | list[Route]] | None = None) -> None:
        super().__init__(self._handle)
        self.routes: dict[str, Route | list[Route]] = dict(routes or {})
        self.requests: list[httpx.Request] = []
        self._calls: dict[str, int] = {}

    @property
    def urls(self) -> list[str]:
        return [f"{r.method} {r.url}" for r in self.requests]

    def json_bodies(self) -> list[Any]:
        return [json.loads(r.content) for r in self.requests if r.content]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        full = f"{request.method} {request.url}"
        bare = f"{request.method} {request.url.copy_with(query=None, fragment=None)}"
        key = full if full in self.routes else bare
        if key not in self.routes:
            raise AssertionError(f"unexpected request: {full}")
        route = self.routes[key]
        if isinstance(route, list):
            index = self._calls.get(key, 0)
            self._calls[key] = index + 1
            route = route[min(index, len(route) - 1)]
        if isinstance(route, Exception):
            raise route
        if callable(route):
            return route(request)
        return route
