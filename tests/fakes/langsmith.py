"""FakeLangSmithClient: a real `langsmith.Client` whose HTTP session records instead of sending.

Because it is the real client, `hide_inputs` / `hide_outputs` and the run metadata behave as in
production; only the network is replaced. Every run payload the client would have uploaded is
available decoded in `payloads`.
"""

import json
from typing import Any

import requests
from langsmith import Client

from pir.tracing import redact_payload


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


class FakeLangSmithClient(Client):
    def __init__(self, *, redact: bool = True) -> None:
        self._recording = _RecordingSession()
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
