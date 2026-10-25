"""FakeLangSmithClient: a real `langsmith.Client` whose HTTP session records instead of sending.

Because it is the real client, `hide_inputs` / `hide_outputs` and the run metadata behave as in
production; only the network is replaced. Every run payload the client would have uploaded is
available decoded in `payloads`.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import requests
from langsmith import Client

from pir.tracing import redact_payload


def check_feedback_args(
    run_id: Any = None,
    *,
    trace_id: Any = None,
    project_id: Any = None,
    session_id: Any = None,
) -> None:
    """The argument rules of langsmith 0.14.12 `Client.create_feedback`, for fakes that override it.

    The real client raises on `project_id` together with a run or trace id, and warns when run
    feedback has no `session_id` (the warning is checked by the real-client test).
    """
    if run_id is None and trace_id is None and project_id is None:
        raise ValueError("One of run_id, trace_id, or project_id  must be provided")
    if (run_id is not None or trace_id is not None) and project_id is not None:
        raise ValueError("project_id cannot be provided if run_id or trace_id is provided")


class _RecordingSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str, bytes]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        data = kwargs.get("data")
        self.calls.append((method, url, data if isinstance(data, bytes) else b""))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.headers["Content-Type"] = "application/json"
        return response

    def send(self, *args: Any, **kwargs: Any) -> requests.Response:  # pragma: no cover
        raise AssertionError("the fake LangSmith session never sends a request")


class FakeRuns:
    """`client.runs`: `query` returns only `id` unless `selects` names more, like the real API
    (langsmith 0.14.12 `runs.query` / `query_v2`), so a caller that forgets `selects` sees no
    names."""

    def __init__(self, client: "FakeLangSmithClient") -> None:
        self._client = client

    def query(self, **kwargs: Any) -> AsyncIterator[Any]:
        self._client.query_calls.append(kwargs)
        selects = {str(item).upper() for item in kwargs.get("selects") or []}
        rows = self._client.stored_runs(kwargs)

        async def generate() -> AsyncIterator[Any]:
            for row in rows:
                out = {"id": row["id"]}
                if "NAME" in selects:
                    out["name"] = row["name"]
                yield type("Run", (), out)()

        return generate()


class FakeLangSmithClient(Client):
    def __init__(self, *, redact: bool = True) -> None:
        self._recording = _RecordingSession()
        self.query_calls: list[dict[str, Any]] = []
        hide = redact_payload if redact else None
        super().__init__(
            api_url="https://langsmith.invalid",
            api_key="fake-langsmith-key",
            session=self._recording,
            auto_batch_tracing=False,
            hide_inputs=hide,
            hide_outputs=hide,
        )

    @property
    def payloads(self) -> list[dict[str, Any]]:
        """Decoded POST and PATCH run bodies, in order."""
        out = []
        for method, url, data in self._recording.calls:
            if method in ("POST", "PATCH") and "/runs" in url and data:
                out.append(json.loads(data))
        return out

    @property
    def posted(self) -> list[dict[str, Any]]:
        """Decoded run-creation bodies only (one per run)."""
        return [
            json.loads(data)
            for method, url, data in self._recording.calls
            if method == "POST" and "/runs" in url and data
        ]

    @property
    def runs(self) -> FakeRuns:  # type: ignore[override]
        return FakeRuns(self)

    def stored_runs(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """The runs a query would list (id and name): the posted root runs."""
        roots = [p for p in self.posted if not p.get("parent_run_id")]
        return [{"id": str(p["id"]), "name": p["name"]} for p in roots]

    def text(self) -> str:
        """Everything uploaded, as one string (for 'no secret appears' checks)."""
        return "\n".join(data.decode("utf-8") for _, _, data in self._recording.calls)

    def root_runs(self) -> list[dict[str, Any]]:
        return [p for p in self.posted if p.get("name") == "pir.scan"]

    def scan_ids(self) -> set[str]:
        return {
            p["extra"]["metadata"]["scan_id"]
            for p in self.payloads
            if p.get("extra", {}).get("metadata", {}).get("scan_id")
        }
