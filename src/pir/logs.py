"""Structured JSON logging (ARCHITECTURE 19.2): one JSON object per line on stderr.

Never logged: secret values, raw client IPs, repo snippets, prompt or model text, advisory text.
Every serialized line passes through `redact()` before it is returned.
"""

import contextlib
import json
import logging
import re
import sys
import time
from collections.abc import AsyncIterator, Iterable
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:
    from pir.config import Settings

REDACTED = "[REDACTED]"

scan_id_var: ContextVar[str | None] = ContextVar("scan_id", default=None)

_EXTRA_FIELDS = (
    "stage",
    "duration_ms",
    "outcome",
    "service",
    "error_id",
    "client",
    "cost_usd",
    "run_id",
)

_AUTHORIZATION = re.compile(
    r"(authorization\\?[\"']?\s*[:=]\s*\\?[\"']?)(?:[A-Za-z]+\s+)?[^\s\"',;}\\]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bbearer\s+[^\s\"',;}\\]+", re.IGNORECASE)

_secrets: set[str] = set()


def register_secrets(values: Iterable[str]) -> None:
    """Add secret values that `redact()` must remove. Empty values are ignored."""
    _secrets.update(v for v in values if v)


def clear_secrets() -> None:
    _secrets.clear()


def secrets_from_settings(settings: "Settings") -> list[str]:
    """Every non-empty SecretStr value held by `settings`."""
    values = []
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr) and value.get_secret_value():
            values.append(value.get_secret_value())
    return values


def redact(text: str, secrets: Iterable[str] | None = None) -> str:
    """Replace secret values (raw and JSON-escaped) and Authorization/Bearer values."""
    known = set(_secrets)
    if secrets is not None:
        known.update(s for s in secrets if s)
    # Longest first so a secret that contains another is removed whole.
    for secret in sorted(known, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
        escaped = json.dumps(secret)[1:-1]
        if escaped != secret:
            text = text.replace(escaped, REDACTED)
    text = _AUTHORIZATION.sub(lambda m: m.group(1) + REDACTED, text)
    return _BEARER.sub("Bearer " + REDACTED, text)


def bind_scan(scan_id: str | None) -> Token[str | None]:
    """Set the scan id for this context; asyncio tasks and `to_thread` copy it."""
    return scan_id_var.set(scan_id)


class ScanContextFilter(logging.Filter):
    """Copy the bound scan id onto each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "scan_id", None) is None:
            record.scan_id = scan_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        scan_id = getattr(record, "scan_id", None)
        if scan_id is None:
            scan_id = scan_id_var.get()
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "msg": record.getMessage(),
            "scan_id": scan_id,
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


class StageResult:
    """Handle yielded by `stage_timer`; set `outcome = "skipped"` to log a skipped step."""

    def __init__(self) -> None:
        self.outcome = "ok"


@contextlib.asynccontextmanager
async def stage_timer(stage: str) -> AsyncIterator[StageResult]:
    """Log `event=stage_end` with stage, duration_ms and outcome when the block exits."""
    result = StageResult()
    started = time.monotonic()
    try:
        yield result
    except TimeoutError:
        result.outcome = "timeout"
        raise
    except BaseException:
        result.outcome = "failed"
        raise
    finally:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        logging.getLogger("pir.stage").info(
            "stage %s %s",
            stage,
            result.outcome,
            extra={
                "event": "stage_end",
                "stage": stage,
                "duration_ms": duration_ms,
                "outcome": result.outcome,
            },
        )


_HANDLER_FLAG = "_pir_json_handler"


def configure_logging(settings: "Settings") -> None:
    """Install the JSON handler on the root logger (stderr, LOG_LEVEL). Safe to call twice."""
    register_secrets(secrets_from_settings(settings))
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_FLAG, True)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ScanContextFilter())
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
