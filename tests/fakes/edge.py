"""The network edge for wiring tests: OSV, Tavily and Nebius answer every real httpx request.

`install` patches `httpx.AsyncHTTPTransport.handle_async_request`, so the production clients run
unchanged (host allowlist, redirects refused, body caps, retries, the OpenAI SDK and its response
parsing) and only the remote answers come from here. A request no route expects is recorded in
`unexpected` and answered with 599; a test must assert `unexpected == []`.
"""

import json
from typing import Any

import httpx
import pytest

from fakes.deps import GHSA, OTHER_GHSA, RESEARCH_REPLY, VERDICT
from fakes.http import fixture_response

OSV_HOST = "api.osv.dev"
TAVILY_HOST = "api.tavily.com"
NEBIUS_HOST = "api.tokenfactory.nebius.com"
FIND_CALL_SITES = {"symbol": "yaml.full_load"}


def _completion(message: dict[str, Any], finish: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-edge",
        "object": "chat.completion",
        "created": 1,
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def _tool_message(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return {"role": "assistant", "content": None, "tool_calls": [call]}


class NetworkEdge:
    """Scripted remote answers. Set the attributes before the scan runs."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.unexpected: list[str] = []
        # OSV: package name -> advisory ids returned by querybatch. Empty dict: no advisories.
        self.osv_ids: dict[str, list[str]] = {"pyyaml": [GHSA]}
        self.osv_status: int | None = None  # querybatch answers this status when set
        self.tavily_status: int | None = None
        self.nebius_status: int | None = None
        self.research: dict[str, Any] = RESEARCH_REPLY
        self.verdict: dict[str, Any] = VERDICT

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def handle(_transport: httpx.AsyncHTTPTransport, request: httpx.Request):
            return self._route(request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)

    def hosts(self) -> set[str]:
        return {request.url.host for request in self.requests}

    def paths(self, host: str) -> list[str]:
        return [r.url.path for r in self.requests if r.url.host == host]

    def _route(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == OSV_HOST and path == "/v1/querybatch":
            return self._querybatch(request)
        if host == OSV_HOST and path.startswith("/v1/vulns/"):
            advisory_id = path.rsplit("/", 1)[-1]
            if advisory_id in (GHSA, OTHER_GHSA):
                return fixture_response(200, "osv", f"vuln_{advisory_id}.json")
            return httpx.Response(404, json={"code": 5, "message": "not found"})
        if host == TAVILY_HOST and path == "/search":
            if self.tavily_status is not None:
                return httpx.Response(self.tavily_status, json={"detail": "edge says no"})
            return fixture_response(200, "tavily", "search_pyyaml.json")
        if host == NEBIUS_HOST and path == "/v1/chat/completions":
            return self._chat(request)
        self.unexpected.append(f"{request.method} {request.url}")
        return httpx.Response(599, json={"detail": "unexpected request in a wiring test"})

    def _querybatch(self, request: httpx.Request) -> httpx.Response:
        if self.osv_status is not None:
            return httpx.Response(self.osv_status, json={"message": "edge says no"})
        queries = json.loads(request.content)["queries"]
        results = [
            {"vulns": [{"id": i} for i in self.osv_ids.get(q["package"]["name"], [])]}
            for q in queries
        ]
        return httpx.Response(200, json={"results": results})

    def _chat(self, request: httpx.Request) -> httpx.Response:
        if self.nebius_status is not None:
            return httpx.Response(self.nebius_status, json={"error": "edge says no"})
        body = json.loads(request.content)
        if body.get("response_format"):  # the researcher asks for a JSON object
            message = {"role": "assistant", "content": json.dumps(self.research)}
            return httpx.Response(200, json=_completion(message, "stop"))
        if any(m.get("role") == "tool" for m in body.get("messages", [])):
            message = _tool_message("submit_verdict", self.verdict)
            return httpx.Response(200, json=_completion(message, "tool_calls"))
        message = _tool_message("find_call_sites", FIND_CALL_SITES)
        return httpx.Response(200, json=_completion(message, "tool_calls"))
