"""LangSmith tracing for a scan (ARCHITECTURE section 19.2, trace correlation).

A scan runs inside one root `@traceable` run whose metadata carries `scan_id`; the `scan_started`
log line carries that run's id. The client hides inputs and outputs through `redact`, so no secret
value leaves the process in a trace. Tracing never decides the outcome of a scan: with tracing off
(no `LANGSMITH_TRACING`, or no key) the scan runs untraced.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from urllib.parse import urlsplit

from langsmith import Client, trace, traceable, tracing_context
from langsmith.run_helpers import get_current_run_tree
from langsmith.run_trees import RunTree

from pir.logs import redact

if TYPE_CHECKING:
    from pir.config import Settings

logger = logging.getLogger("pir.tracing")

T = TypeVar("T")

# Flush and share together may take this long, so the report stays inside the UI's
# `report_not_ready` retry window (ADR-0045).
SHARE_TIMEOUT_S = 3.0
_SHARE_HOST = "smith.langchain.com"


def redact_payload(value: Any) -> Any:
    """`value` with `redact()` applied to every string in it (dicts, lists and tuples walked)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact_payload(k) if isinstance(k, str) else k: redact_payload(v)
                for k, v in value.items()}  # fmt: skip
    if isinstance(value, list | tuple):
        return [redact_payload(v) for v in value]
    return value


def make_client(settings: "Settings") -> Client | None:
    """A LangSmith client that redacts inputs and outputs, or None when tracing is off."""
    if not settings.langsmith_tracing:
        return None
    key = settings.langsmith_api_key
    if key is None or not key.get_secret_value():
        logger.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is not set; tracing is off")
        return None
    return Client(
        api_key=key.get_secret_value(),
        api_url=settings.langsmith_endpoint,
        hide_inputs=redact_payload,
        hide_outputs=redact_payload,
        # With public links on, keep SDK runtime and environment metadata out of shared runs.
        omit_traced_runtime_info=settings.langsmith_share_traces,
    )


def valid_share_url(url: object) -> bool:
    """True for an `https://` link on a `smith.langchain.com` host with a `/public/` path."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if port not in (None, 443):
        return False
    if host != _SHARE_HOST and not host.endswith("." + _SHARE_HOST):
        return False
    return parts.path.startswith("/public/")


async def _share_run(client: Client, run_id: uuid.UUID) -> str | None:
    """Flush, then share the root run; the public link, or None on any failure (ADR-0045)."""
    try:
        async with asyncio.timeout(SHARE_TIMEOUT_S):
            await flush(client)
            url = await asyncio.to_thread(client.share_run, run_id)
    except TimeoutError:
        logger.warning("langsmith share timed out")
        return None
    except Exception as exc:  # best effort; the cause is logged without its message
        logger.warning("langsmith share failed: %s", type(exc).__name__)
        return None
    if not valid_share_url(url):
        logger.warning("langsmith share returned an unexpected link")
        return None
    return url


def _summarize_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return {"scan_id": inputs.get("scan_id")}


def _summarize_outputs(outputs: Any) -> dict[str, Any]:
    report = outputs.get("output") if isinstance(outputs, dict) else outputs
    outcome = getattr(report, "outcome", None)
    findings = getattr(report, "findings", None)
    return {"outcome": outcome, "findings": len(findings) if findings is not None else None}


@traceable(
    name="pir.scan",
    run_type="chain",
    process_inputs=_summarize_inputs,
    process_outputs=_summarize_outputs,
)
async def _root(scan_id: str, body: Callable[[], Awaitable[Any]]) -> Any:
    return await body()


async def run_traced(
    settings: "Settings",
    scan_id: str,
    body: Callable[[], Awaitable[T]],
    *,
    client: Client | None = None,
    parent: RunTree | None = None,
    with_trace_url: Callable[[T, str], T] | None = None,
) -> T:
    """Run `body` as the root run of a trace and log `scan_started` with the root run id.

    `client` is given by tests (a fake); otherwise one is made from `settings` when tracing is on.
    `parent` (the evaluation runner's case trace) makes the scan a child run of that run, so the
    scan's step, LLM and tool runs sit beneath it; it is ignored when tracing is off.
    `with_trace_url` receives the result and the public link, and returns the result to hand
    back. It is called only when `langsmith_share_traces` is on, the run is the trace root (no
    `parent`) and a valid link was obtained; a failed share leaves the result as it is.
    """
    client = client if client is not None else make_client(settings)
    run_id = uuid.uuid4() if client is not None else None
    # Under eval the scan is a child run, so the trace id (the case root's) differs from run_id.
    trace_id = (getattr(parent, "trace_id", None) or run_id) if run_id else None
    logger.info(
        "scan started",
        extra={
            "event": "scan_started",
            "run_id": str(run_id) if run_id else None,
            "trace_id": str(trace_id) if trace_id else None,
        },
    )
    if client is None:
        with tracing_context(enabled=False):
            return await body()
    extra: dict[str, Any] = {
        "run_id": run_id,
        "client": client,
        "project_name": settings.langsmith_project,
        "metadata": {"scan_id": scan_id},
    }
    if parent is not None:
        extra["parent"] = parent
    with tracing_context(enabled=True, client=client, project_name=settings.langsmith_project):
        result = await _root(scan_id, body, langsmith_extra=extra)
    if with_trace_url is not None and parent is None and settings.langsmith_share_traces:
        assert run_id is not None
        url = await _share_run(client, run_id)
        if url is not None:
            result = with_trace_url(result, url)
    return result


class RunHandle:
    """The run a helper below opened, or nothing when tracing is off (every method is a no-op)."""

    def __init__(self, run: RunTree | None) -> None:
        self._run = run

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        if self._run is not None:
            self._run.end(outputs=outputs)

    def add_metadata(self, **metadata: Any) -> None:
        if self._run is not None:
            self._run.add_metadata(metadata)


@contextlib.contextmanager
def _child_run(
    name: str,
    run_type: Literal["chain", "tool"],
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[RunHandle]:
    """A child of the run that is open now (ADR-0038); a no-op with tracing off.

    With no run open (tracing off, or called outside a scan) nothing is created, so a run is never
    posted as a root. Inputs and outputs go through the client's `hide_inputs` / `hide_outputs`.
    """
    if get_current_run_tree() is None:
        yield RunHandle(None)
        return
    with trace(name, run_type, inputs=inputs or {}, metadata=metadata) as run:
        yield RunHandle(run)


def step_run(name: str) -> contextlib.AbstractContextManager[RunHandle]:
    """`pir.step.<name>`: one run per pipeline step; the caller sets `{"state": ...}`."""
    return _child_run(f"pir.step.{name}", "chain")


def advisory_run(advisory_id: str, package: str) -> contextlib.AbstractContextManager[RunHandle]:
    """`pir.research.advisory`: one advisory researched (`from_cache` is added when known)."""
    return _child_run(
        "pir.research.advisory",
        "chain",
        {"advisory_id": advisory_id, "package": package},
        {"advisory_id": advisory_id},
    )


def finding_run(
    package: str, version: str, advisory_id: str
) -> contextlib.AbstractContextManager[RunHandle]:
    """`pir.reasoning.finding`: one finding reasoned, holding its LLM and tool runs."""
    return _child_run(
        "pir.reasoning.finding",
        "chain",
        {"package": package, "version": version, "advisory_id": advisory_id},
        {"package": package, "version": version, "advisory_id": advisory_id},
    )


def tool_run(
    name: str, inputs: dict[str, Any] | None = None
) -> contextlib.AbstractContextManager[RunHandle]:
    """`pir.tool.<name>` of run type tool: the arguments in, the size of the answer out."""
    return _child_run(f"pir.tool.{name}", "tool", inputs)


async def flush(client: Client | None) -> None:
    """Send queued trace data. Never raises: a failed upload must not fail a scan."""
    if client is None:
        return
    try:
        await asyncio.to_thread(client.flush)
    except Exception as exc:  # tracing is best effort; the cause is logged without its message
        logger.warning("langsmith flush failed: %s", type(exc).__name__)
