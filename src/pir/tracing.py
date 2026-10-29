"""LangSmith tracing for a scan (ARCHITECTURE section 19.2, trace correlation).

A scan runs inside one root `@traceable` run whose metadata carries `scan_id`; the `scan_started`
log line carries that run's id. The client hides inputs and outputs through `redact`, so no secret
value leaves the process in a trace. Tracing never decides the outcome of a scan: with tracing off
(no `LANGSMITH_TRACING`, or no key) the scan runs untraced.
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import re
import threading
import time
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
# A public run's `error` is cut to its first line, at most this long (ADR-0045).
_MAX_ERROR_CHARS = 500
# `smith.langchain.com` or one regional label in front of it, optionally with the default port.
_SHARE_NETLOC = re.compile(r"(?:[a-z0-9-]+\.)?smith\.langchain\.com(?::443)?")
_SHARE_PATH = re.compile(r"/public/[A-Za-z0-9._~/-]+")
_UNUSUAL = re.compile(r"[\x5c\s\x00-\x1f\x7f]")  # backslash, whitespace, control

# Flush and share run here, never on the default executor the spend ledger uses. One worker, and
# a new share is skipped while the previous one has not returned, so a hung request cannot pile up
# threads. A call that outlives its time limit keeps its worker until the SDK request returns.
_SHARE_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pir-share")
_share_lock = threading.Lock()
_pending: "concurrent.futures.Future[str] | None" = None


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


def make_anonymizer(*, first_line_errors: bool) -> Callable[[Any], Any]:
    """The client's `anonymizer`: `redact_payload` over every input, output, error and metadata.

    The SDK passes a run's `error` as `{"error": text}` (`text` is the exception and its
    traceback). With `first_line_errors` that text is cut to its first line, after redaction, so a
    public run carries no traceback.
    """

    def anonymize(payload: Any) -> Any:
        scrubbed = redact_payload(payload)
        if (
            first_line_errors
            and isinstance(scrubbed, dict)
            and set(scrubbed) == {"error"}
            and isinstance(scrubbed["error"], str)
        ):
            lines = scrubbed["error"].strip().splitlines()
            return {"error": (lines[0] if lines else "")[:_MAX_ERROR_CHARS]}
        return scrubbed

    return anonymize


def make_client(settings: "Settings") -> Client | None:
    """A LangSmith client that redacts what it uploads, or None when tracing is off.

    One `anonymizer` covers inputs, outputs, errors and metadata; with it set the SDK ignores
    `hide_inputs` / `hide_outputs`, which also never reach errors or metadata.
    """
    if not settings.langsmith_tracing:
        return None
    key = settings.langsmith_api_key
    if key is None or not key.get_secret_value():
        logger.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is not set; tracing is off")
        return None
    return Client(
        api_key=key.get_secret_value(),
        api_url=settings.langsmith_endpoint,
        anonymizer=make_anonymizer(first_line_errors=settings.langsmith_share_traces),
        # With public links on, keep SDK runtime and environment metadata out of shared runs.
        omit_traced_runtime_info=settings.langsmith_share_traces,
    )


def valid_share_url(url: object) -> bool:
    """True for an `https://` link on `smith.langchain.com` with a `/public/` path.

    The netloc must be exactly the host (with at most one regional label in front), or the host
    plus `:443`: no user info, other port, backslash, `%`, whitespace or control character.
    """
    if not isinstance(url, str) or _UNUSUAL.search(url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.query or parts.fragment:
        return False
    if not _SHARE_NETLOC.fullmatch(parts.netloc):
        return False
    return _SHARE_PATH.fullmatch(parts.path) is not None


def _flush_and_share(client: Client, run_id: uuid.UUID) -> str:
    """Runs on the share worker: flush within the time limit, then share the run once."""
    deadline = time.monotonic() + SHARE_TIMEOUT_S
    client.flush(timeout=SHARE_TIMEOUT_S)
    if time.monotonic() >= deadline:
        raise TimeoutError("flush used the whole time limit")
    # `share_run` makes one attempt (no retries); the SDK offers no per-call request timeout.
    return client.share_run(run_id)


async def _share_run(client: Client, run_id: uuid.UUID) -> str | None:
    """Flush, then share the root run; the public link, or None on any failure (ADR-0045).

    Flush and share run on the dedicated share worker, one at a time: while an earlier call has
    not returned this one is skipped. After a timeout the worker may still finish the share, so
    the run id is logged for the owner to unshare it.
    """
    global _pending
    with _share_lock:
        if _pending is not None and not _pending.done():
            logger.warning("langsmith share skipped: an earlier flush or share is still running")
            return None
        future = _SHARE_POOL.submit(_flush_and_share, client, run_id)
        # Read the outcome even when nobody waits for it any more.
        future.add_done_callback(lambda f: f.cancelled() or f.exception())
        _pending = future
    try:
        url = await asyncio.wait_for(asyncio.wrap_future(future), SHARE_TIMEOUT_S)
    except TimeoutError:
        logger.warning("langsmith share timed out; run_id=%s", run_id)
        return None
    except Exception as exc:  # best effort; the cause is logged without its message
        logger.warning("langsmith share failed: %s", type(exc).__name__)
        return None
    if not valid_share_url(url):
        logger.warning("langsmith share returned an unexpected link; run_id=%s", run_id)
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
