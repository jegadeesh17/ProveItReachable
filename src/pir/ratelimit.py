"""Live-scan rate limits and client identity (ARCHITECTURE sections 7.8 and 11.2).

Counters live in memory. Only requests that pass are stored, so the state is bounded by the
global daily limit. Logs never hold a client key, only `ClientHasher.hash(key)`.
"""

import hashlib
import ipaddress
import math
import secrets
from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pir.config import Settings

_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)
UNKNOWN_CLIENT = "unknown"


@dataclass(frozen=True)
class Allowed:
    """The request counted against the limits and may proceed."""


@dataclass(frozen=True)
class Limited:
    """Refused; nothing was consumed. `scope` says which limit."""

    retry_after_s: int
    scope: Literal["hour", "day", "global"]


def _seconds(delta: timedelta) -> int:
    return max(1, math.ceil(delta.total_seconds()))


class RateLimiter:
    """Per client per hour and per day (sliding windows) and globally per UTC day."""

    def __init__(self, *, per_hour: int = 3, per_day: int = 10, global_per_day: int = 10) -> None:
        self._per_hour = per_hour
        self._per_day = per_day
        self._global_per_day = global_per_day
        self._hits: dict[str, deque[datetime]] = {}
        self._global_day: date | None = None
        self._global_count = 0

    @classmethod
    def from_settings(cls, settings: "Settings") -> "RateLimiter":
        return cls(
            per_hour=settings.rate_limit_per_client_hour,
            per_day=settings.rate_limit_per_client_day,
            global_per_day=settings.rate_limit_global_day,
        )

    def check_and_consume(self, client_key: str, now: datetime) -> Allowed | Limited:
        now = now.astimezone(UTC)
        self._prune(now)
        hits = self._hits.get(client_key, deque())
        in_hour = [t for t in hits if now - t < _HOUR]
        if self._per_hour <= 0 or len(in_hour) >= self._per_hour:
            wait = _HOUR if not in_hour else in_hour[0] + _HOUR - now
            return Limited(_seconds(wait), "hour")
        if self._per_day <= 0 or len(hits) >= self._per_day:
            wait = _DAY if not hits else hits[0] + _DAY - now
            return Limited(_seconds(wait), "day")
        today = now.date()
        if self._global_day != today:
            self._global_day, self._global_count = today, 0
        if self._global_count >= self._global_per_day:
            midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
            return Limited(_seconds(midnight - now), "global")
        self._global_count += 1
        self._hits.setdefault(client_key, deque()).append(now)
        return Allowed()

    def _prune(self, now: datetime) -> None:
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and now - hits[0] >= _DAY:
                hits.popleft()
            if not hits:
                del self._hits[key]


def client_key(connection_host: str | None, forwarded_for: str | None, trusted_hops: int) -> str:
    """The key a request is limited by (finding 8).

    With `trusted_hops = n > 0`: the n-th address from the right of `X-Forwarded-For`, only if the
    header has at least n entries and that entry is an IP address; otherwise the connection
    address. The leftmost entry is client-controlled and never used on its own.
    """
    fallback = connection_host or UNKNOWN_CLIENT
    if trusted_hops <= 0 or not forwarded_for:
        return fallback
    entries = [part.strip() for part in forwarded_for.split(",")]
    if len(entries) < trusted_hops:
        return fallback
    try:
        return str(ipaddress.ip_address(entries[-trusted_hops]))
    except ValueError:
        return fallback


class ClientHasher:
    """A salted hash of a client key for logs. The salt is random per process."""

    def __init__(self, salt: str | None = None) -> None:
        self._salt = salt or secrets.token_hex(16)

    def hash(self, key: str) -> str:
        return hashlib.sha256(f"{self._salt}:{key}".encode()).hexdigest()[:16]
