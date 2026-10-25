"""LangSmith tracing for a scan (ARCHITECTURE section 19.2, trace correlation).

A scan runs inside one root `@traceable` run whose metadata carries `scan_id`; the `scan_started`
log line carries that run's id. The client hides inputs and outputs through `redact`, so no secret
value leaves the process in a trace. Tracing never decides the outcome of a scan: with tracing off
(no `LANGSMITH_TRACING`, or no key) the scan runs untraced.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from langsmith import Client, traceable, tracing_context

from pir.logs import redact

if TYPE_CHECKING:
    from pir.config import Settings

logger = logging.getLogger("pir.tracing")

T = TypeVar("T")


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
    )


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
) -> T:
    """Run `body` as the root run of a trace and log `scan_started` with the root run id.

    `client` is given by tests (a fake); otherwise one is made from `settings` when tracing is on.
    """
    client = client if client is not None else make_client(settings)
    run_id = uuid.uuid4() if client is not None else None
    logger.info(
        "scan started",
        extra={"event": "scan_started", "run_id": str(run_id) if run_id else None},
    )
    if client is None:
        with tracing_context(enabled=False):
            return await body()
    with tracing_context(enabled=True, client=client, project_name=settings.langsmith_project):
        return await _root(
            scan_id,
            body,
            langsmith_extra={
                "run_id": run_id,
                "client": client,
                "project_name": settings.langsmith_project,
                "metadata": {"scan_id": scan_id},
            },
        )


async def flush(client: Client | None) -> None:
    """Send queued trace data. Never raises: a failed upload must not fail a scan."""
    if client is None:
        return
    try:
        await asyncio.to_thread(client.flush)
    except Exception as exc:  # tracing is best effort; the cause is logged without its message
        logger.warning("langsmith flush failed: %s", type(exc).__name__)
