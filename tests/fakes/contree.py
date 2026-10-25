"""A fake `contree-sdk` async client: just what ContreeExecutor uses (images.oci, image.run).

`run()` is lazy like the real SDK: it returns an awaitable and only `await` executes it. Every
call is recorded so tests can assert that the SDK `run()` is used by `prepare` alone, with the
constant command. The REST side is served by `RecordingTransport` and tests/fixtures/http/contree/.
"""

import uuid
from types import SimpleNamespace
from typing import Any

BASE_URL = "https://api.tokenfactory.nebius.com/sandboxes/"
HOST = "api.tokenfactory.nebius.com"
API_KEY = "SENTINEL-API-KEY-DO-NOT-LEAK"
PROJECT = "SENTINEL-PROJECT"


class FakeAuth:
    base_url = BASE_URL

    def get_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {API_KEY}", "Project": PROJECT}


class FakeRun:
    """What `image.run(...)` returns: awaiting it executes and yields the result image."""

    def __init__(self, client: "FakeContree", kwargs: dict[str, Any]) -> None:
        self._client = client
        self._kwargs = kwargs

    def __await__(self):
        return self._execute().__await__()

    async def _execute(self) -> "FakeImage":
        client = self._client
        client.executed.append(self._kwargs)
        if client.fail_run is not None:
            raise client.fail_run
        image = FakeImage(client, str(uuid.uuid4()))
        client.produced.append(str(image.uuid))
        image.exit_code = client.run_exit_code
        return image


class FakeImage:
    def __init__(self, client: "FakeContree", image_uuid: str | None) -> None:
        self._client = client
        self.uuid = uuid.UUID(image_uuid) if image_uuid else None
        self.exit_code = 0

    def run(self, command: str | None = None, **kwargs: Any) -> FakeRun:
        call = {"command": command, **kwargs}
        self._client.run_calls.append(call)
        return FakeRun(self._client, call)


class FakeImages:
    def __init__(self, client: "FakeContree") -> None:
        self._client = client

    async def oci(self, ref: str, *, tag: str | None = None, **kwargs: Any) -> FakeImage:
        self._client.oci_calls.append({"ref": ref, "tag": tag, **kwargs})
        if self._client.fail_oci is not None:
            raise self._client.fail_oci
        return FakeImage(self._client, self._client.base_uuid)


class FakeContree:
    def __init__(self) -> None:
        self.config = SimpleNamespace(auth=FakeAuth())
        self.images = FakeImages(self)
        self.base_uuid = str(uuid.uuid4())
        self.oci_calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []
        self.executed: list[dict[str, Any]] = []
        self.produced: list[str] = []  # UUIDs of the images executed runs returned
        self.fail_oci: Exception | None = None
        self.fail_run: Exception | None = None
        self.run_exit_code = 0
