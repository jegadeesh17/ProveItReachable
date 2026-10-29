"""ContreeExecutor: Token Factory Sandboxes (ARCHITECTURE section 7.1).

The SDK is used for the base image import and for `prepare` only (its `run()` is root with no
network control). Every command that touches repo content goes through the REST instances API
with `uid = gid = 65534`, an explicit `networking.enabled`, a fixed `env -i` environment and a
metered budget hold. Errors that could carry a secret are reduced to a class name or a status.
"""

import asyncio
import base64
import binascii
import hashlib
import logging
import math
import re
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from pir.config import Settings, load_settings
from pir.executor.base import ExecResult, ExecutorSpendDenied, ExecutorUnavailable, ImageRef
from pir.http import MIB, OutboundBlocked, make_client
from pir.sandbox_agent.resolve import UV_ENV
from pir.spend import ScanBudget, SpendDenied, SpendHold

logger = logging.getLogger("pir.executor.contree")

# The platform refuses `truncate_output_at` of 11 MiB (400); 10 MiB is accepted (checked live).
SANDBOX_MAX_OUTPUT_BYTES = 10 * MIB
REST_MAX_BYTES = 12 * MIB  # ARCHITECTURE section 12: the Sandboxes REST cap
PREPARE_CMD = "mkdir -p /work && chown 65534:65534 /work"
PREPARE_TIMEOUT_S = 120
SANDBOX_UID = SANDBOX_GID = 65534
CANCEL_GRACE_S = 30.0
POLL_START_S = 0.5
POLL_MAX_S = 2.0
HOSTNAME = "localhost"
ENV_PREFIX = "env -i " + " ".join(shlex.quote(f"{k}={v}") for k, v in UV_ENV.items())

_DIGEST_RE = re.compile(r"^(?P<tag>[^\s@]+)@sha256:(?P<hex>[0-9a-f]{64})$")
_TERMINAL = frozenset({"SUCCESS", "FAILED", "CANCELLED"})
_RUNNING = frozenset({"ASSIGNED", "PENDING", "EXECUTING"})
_MAX_DETAIL_CHARS = 300

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


def split_base_image(ref: str) -> tuple[str, str]:
    """`(version tag to import, local tag)` for `<version tag>@sha256:<64 hex>` (ADR-0033).

    contree-sdk 0.3.6 answers 404 for every `@sha256:` reference, so only the part before `@` is
    imported; without `tag=` the SDK would derive a local name containing `@` (400). The digest
    is enforced by the base-image check (selfcheck), not by the importer.
    """
    match = _DIGEST_RE.match(ref)
    if match is None:
        raise ExecutorUnavailable("SANDBOX_BASE_IMAGE must end in @sha256:<64 hex digits>")
    return match.group("tag"), f"pir-base/sha256-{match.group('hex')}"


def files_key(files: Mapping[str, bytes]) -> str:
    """sha256 over the sorted (path, sha256(content)) pairs: the `prepare` cache key."""
    outer = hashlib.sha256()
    for path in sorted(files):
        outer.update(path.encode("utf-8") + b"\0" + hashlib.sha256(files[path]).digest())
    return outer.hexdigest()


def build_command(argv: list[str]) -> str:
    """The shell command string: the fixed `env -i` prefix plus the quoted argv."""
    return f"{ENV_PREFIX} {shlex.join(argv)}"


def _short(text: object) -> str:
    cleaned = "".join(ch if ch.isprintable() else " " for ch in str(text))
    return cleaned[:_MAX_DETAIL_CHARS]


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _decode_stream(stream: object, what: str) -> tuple[str, bool]:
    if not isinstance(stream, dict):
        raise ExecutorUnavailable(f"malformed response: {what} is missing")
    value, encoding, truncated = (
        stream.get("value"),
        stream.get("encoding"),
        stream.get("truncated"),
    )
    if not isinstance(value, str) or not isinstance(truncated, bool):
        raise ExecutorUnavailable(f"malformed response: {what} has the wrong shape")
    try:
        if encoding == "base64":
            raw = base64.b64decode(value.encode("ascii"), validate=True)
        elif encoding == "ascii":
            raw = value.encode("utf-8")
        else:
            raise ExecutorUnavailable(f"malformed response: unknown {what} encoding")
    except (binascii.Error, UnicodeError) as exc:
        raise ExecutorUnavailable(f"malformed response: {what} is not decodable") from exc
    return raw.decode("utf-8", errors="replace"), truncated


def _uuid_text(value: object, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ExecutorUnavailable(f"malformed response: {what} is not a UUID") from exc


@dataclass
class _RunState:
    """Whether the platform accepted the operation (so a failure may still have cost money)."""

    started: bool = False


@dataclass
class _Outcome:
    result: ExecResult
    reported_cost: float | None


class ContreeExecutor:
    """`Executor` over the Sandboxes API. One instance per process (it caches images)."""

    kind = "sandbox"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._settings = settings or load_settings()
        self._sdk_client = client
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max(1, self._settings.sandbox_max_concurrent_ops))
        self._base_lock = asyncio.Lock()
        self._prepare_lock = asyncio.Lock()
        self._base: Any = None
        self._prepared: dict[str, ImageRef] = {}

    def _secrets(self) -> list[str]:
        """Every credential value this executor can hold (settings and the SDK's auth headers)."""
        found: list[str] = []
        key = self._settings.nebius_api_key
        found += [key.get_secret_value() if key else "", self._settings.nebius_project_id or ""]
        auth = getattr(getattr(self._sdk_client, "config", None), "auth", None)
        if auth is not None:
            found += [str(v).removeprefix("Bearer ") for v in auth.get_headers().values()]
        return [secret for secret in found if secret]

    def _detail(self, exc: BaseException) -> str:
        """`ClassName: message` for an SDK error, with the credentials removed from the text."""
        text = str(exc)
        for secret in self._secrets():
            text = text.replace(secret, "***")
        return f"{type(exc).__name__}: {_short(text)}"

    # ---- SDK (base image and prepare only) --------------------------------------------

    def _sdk(self) -> Any:
        if self._sdk_client is None:
            from contree_sdk import Contree
            from contree_sdk.auth import IAMAuth
            from contree_sdk.config import ContreeConfig

            key = self._settings.nebius_api_key
            project = self._settings.nebius_project_id
            if key is None or not key.get_secret_value() or not project:
                raise ExecutorUnavailable(
                    "NEBIUS_API_KEY and NEBIUS_PROJECT_ID are required for EXECUTOR=sandbox"
                )
            auth = IAMAuth(token=key.get_secret_value(), project_id=project)
            self._sdk_client = Contree(ContreeConfig(auth=auth))
        return self._sdk_client

    async def _base_image(self) -> Any:
        """The base image, imported by its version tag on first use and kept for the process."""
        async with self._base_lock:
            if self._base is not None:
                return self._base
            ref = self._settings.sandbox_base_image
            if not ref:
                raise ExecutorUnavailable("SANDBOX_BASE_IMAGE is not set")
            version_tag, local_tag = split_base_image(ref)
            sdk = self._sdk()
            try:
                self._base = await sdk.images.oci(version_tag, tag=local_tag)
            except Exception as exc:  # the SDK raises many types; all mean "no base image"
                raise ExecutorUnavailable(f"base image import failed: {self._detail(exc)}") from exc
            return self._base

    async def prepare(self, files: dict[str, bytes]) -> ImageRef:
        if not files:
            raise ValueError("files must not be empty")
        for path, content in files.items():
            if not isinstance(path, str) or not path.startswith("/opt/pir/") or ".." in path:
                raise ValueError(f"image path must be absolute under /opt/pir/: {path!r}")
            if not isinstance(content, bytes):
                raise ValueError(f"content of {path!r} must be bytes")
        key = files_key(files)
        async with self._prepare_lock:
            cached = self._prepared.get(key)
            if cached is not None:
                return cached
            async with self._semaphore:
                base = await self._base_image()
                try:
                    # The only SDK run(): root, no network control, trusted files, constant command.
                    done = await base.run(
                        shell=PREPARE_CMD,
                        files=dict(files),
                        disposable=False,
                        timeout=PREPARE_TIMEOUT_S,
                    )
                    exit_code, image_uuid = done.exit_code, done.uuid
                except Exception as exc:  # SDK failure of any kind
                    raise ExecutorUnavailable(f"prepare failed: {self._detail(exc)}") from exc
            if exit_code != 0 or image_uuid is None:
                raise ExecutorUnavailable(f"prepare failed: exit code {exit_code}")
            ref = ImageRef(kind="sandbox", ref=_uuid_text(image_uuid, "prepared image"))
            self._prepared[key] = ref
            return ref

    # ---- REST (every command) ---------------------------------------------------------

    def _rest_base(self) -> tuple[str, str, dict[str, str]]:
        auth = self._sdk().config.auth
        parts = urlsplit(str(auth.base_url))
        if parts.scheme != "https" or not parts.hostname:
            raise ExecutorUnavailable("the Sandboxes API URL must be an https URL")
        return parts.hostname, str(auth.base_url).rstrip("/"), dict(auth.get_headers())

    def _http(self) -> tuple[httpx.AsyncClient, str]:
        host, base_url, headers = self._rest_base()
        client = make_client(
            {host},
            REST_MAX_BYTES,
            self._settings.timeout_nebius_s,
            headers={**headers, "Accept": "application/json"},
            transport=self._transport,
        )
        return client, base_url

    @staticmethod
    async def _json(client: httpx.AsyncClient, method: str, url: str, **kw: Any) -> Any:
        try:
            response = await client.request(method, url, **kw)
        except (httpx.HTTPError, OutboundBlocked) as exc:
            raise ExecutorUnavailable(f"Sandboxes request failed: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise ExecutorUnavailable(
                f"Sandboxes refused the credentials (HTTP {response.status_code})"
            )
        if response.status_code >= 400:
            raise ExecutorUnavailable(f"Sandboxes returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ExecutorUnavailable("malformed response: body is not JSON") from exc

    def _request_body(
        self, image: ImageRef, argv: list[str], network: bool, timeout_s: float, save: bool
    ) -> dict[str, Any]:
        return {
            "command": build_command(argv),
            "args": [],
            "shell": True,
            "env": {},
            "cwd": "",
            "image": image.ref,
            "hostname": HOSTNAME,
            "stdin": {"value": "", "encoding": "ascii", "truncated": False},
            "disposable": not save,
            "timeout": math.ceil(timeout_s),
            "truncate_output_at": SANDBOX_MAX_OUTPUT_BYTES,
            "files": {},
            "preserve_env": False,
            "networking": {"enabled": network},
            "uid": SANDBOX_UID,
            "gid": SANDBOX_GID,
        }

    async def run(
        self,
        image: ImageRef,
        argv: list[str],
        *,
        network: bool,
        timeout_s: float,
        save: bool = False,
        budget: ScanBudget | None = None,
    ) -> ExecResult:
        if image.kind != "sandbox":
            raise ValueError("ContreeExecutor runs sandbox images only")
        if not argv or not all(isinstance(a, str) and a for a in argv):
            raise ValueError("argv must be a non-empty list of non-empty strings")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be a positive number")
        rate = self._settings.sandbox_usd_per_second
        hold: SpendHold | None = None
        if budget is not None:
            decision = budget.try_spend("nebius", math.ceil(timeout_s) * rate)
            if isinstance(decision, SpendDenied):
                raise ExecutorSpendDenied(decision)
            hold = decision
        state = _RunState()
        try:
            async with self._semaphore:
                outcome = await self._execute(image, argv, network, timeout_s, save, state)
        except BaseException:
            if budget is not None and hold is not None:
                # Unknown cost once the platform accepted the operation: keep the whole hold.
                budget.record(hold, hold.amount_usd if state.started else 0.0, service="sandbox")
                if state.started:
                    budget.count("sandbox_operations")  # timeouts included (ADR-0045)
            raise
        cost = outcome.reported_cost
        if cost is None:
            cost = outcome.result.duration_s * rate
        if budget is not None and hold is not None:
            budget.record(hold, cost, service="sandbox")
            budget.count("sandbox_operations")
        return outcome.result.model_copy(update={"cost_usd": cost})

    async def _execute(
        self,
        image: ImageRef,
        argv: list[str],
        network: bool,
        timeout_s: float,
        save: bool,
        state: _RunState,
    ) -> _Outcome:
        client, base_url = self._http()
        async with client:
            created = await self._json(
                client,
                "POST",
                f"{base_url}/v1/instances",
                json=self._request_body(image, argv, network, timeout_s, save),
            )
            op_id = _uuid_text(
                created.get("uuid") if isinstance(created, dict) else None, "operation"
            )
            state.started = True
            started = self._clock()
            deadline = started + timeout_s + CANCEL_GRACE_S
            interval = POLL_START_S
            op_url = f"{base_url}/v1/operations/{op_id}"
            while True:
                data = await self._json(client, "GET", op_url)
                status = data.get("status") if isinstance(data, dict) else None
                if status in _TERMINAL:
                    break
                if status not in _RUNNING:
                    raise ExecutorUnavailable("malformed response: unknown operation status")
                now = self._clock()
                if now >= deadline:
                    await self._cancel(client, op_url)
                    return self._host_timeout(
                        now - started, math.ceil(timeout_s) * self._settings.sandbox_usd_per_second
                    )
                await self._sleep(min(interval, max(0.0, deadline - now)))
                interval = min(interval * 2, POLL_MAX_S)
        return self._map_terminal(data, save, self._clock() - started)

    async def _cancel(self, client: httpx.AsyncClient, op_url: str) -> None:
        try:
            await client.delete(op_url)
        except (httpx.HTTPError, OutboundBlocked) as exc:
            logger.warning("cancelling a timed-out operation failed: %s", type(exc).__name__)

    @staticmethod
    def _host_timeout(elapsed: float, estimate_usd: float) -> _Outcome:
        result = ExecResult(
            exit_code=-1,
            stdout="",
            stderr="",
            timed_out=True,
            truncated=False,
            duration_s=elapsed,
            cost_usd=0.0,
            image=None,
        )
        # The platform's own timeout bounds the run, so the estimate is the cost we can know.
        return _Outcome(result, estimate_usd)

    @staticmethod
    def _map_terminal(data: dict[str, Any], save: bool, wall_s: float) -> _Outcome:
        """Result mapping of section 7.1: timed_out first, then SUCCESS, else unavailable."""
        status = data.get("status")
        if status != "SUCCESS":
            raise ExecutorUnavailable(
                f"operation {status}: {_short(data.get('error') or '')}".strip()
            )
        metadata = data.get("metadata")
        result = metadata.get("result") if isinstance(metadata, dict) else None
        if not isinstance(result, dict):
            raise ExecutorUnavailable("malformed response: operation has no result")
        state = result.get("state")
        if not isinstance(state, dict) or not isinstance(state.get("timed_out"), bool):
            raise ExecutorUnavailable("malformed response: operation has no state")
        exit_code = state.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ExecutorUnavailable("malformed response: exit code is not an integer")
        stdout, out_truncated = _decode_stream(result.get("stdout"), "stdout")
        stderr, err_truncated = _decode_stream(result.get("stderr"), "stderr")
        resources = result.get("resources")
        resources = resources if isinstance(resources, dict) else {}
        duration = _number(data.get("duration"))
        duration = wall_s if duration is None else duration
        cost = _number(resources.get("cost"))
        if cost is not None and cost < 0:
            cost = None
        logger.info(
            "sandbox operation done: duration=%.3f consumed_cpu=%s consumed_memory=%s cost=%s",
            duration,
            data.get("consumed_cpu"),
            data.get("consumed_memory"),
            cost,
        )
        timed_out = state["timed_out"]
        saved: ImageRef | None = None
        if save and not timed_out:
            raw = data.get("result_image_uuid")
            if raw is not None:
                saved = ImageRef(kind="sandbox", ref=_uuid_text(raw, "result image"))
            elif exit_code == 0:
                raise ExecutorUnavailable("malformed response: no result image for a saved run")
        exec_result = ExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=out_truncated or err_truncated,
            duration_s=duration,
            cost_usd=0.0,
            image=saved,
        )
        return _Outcome(exec_result, cost)

    async def discard(self, image: ImageRef) -> None:
        """Untag a saved image. Failures are logged, never raised."""
        if image.kind != "sandbox":
            return
        try:
            image_id = _uuid_text(image.ref, "image")
            client, base_url = self._http()
            async with client:
                response = await client.delete(f"{base_url}/v1/images/{image_id}/tag")
            if response.status_code >= 400:
                logger.warning("untagging an image failed: HTTP %s", response.status_code)
        except Exception as exc:  # discard never raises; the cause is logged
            logger.warning("untagging an image failed: %s", type(exc).__name__)
