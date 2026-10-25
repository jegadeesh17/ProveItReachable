"""Spend metering (ARCHITECTURE sections 7.8 and 11): ledger protocol, file ledger, meter, budget.

`SpendMeter.reserve` and `settle` run under one `asyncio.Lock`. Each is a pure mutation of
`LedgerState` applied as load -> mutate -> save(parent=revision); a stale parent
(`LedgerConflict`) is reloaded and re-applied, at most 3 attempts with jitter, then the meter
fails closed. Any ledger that cannot be read or written pauses live scans (`LedgerUnavailable`).
"""

import asyncio
import json
import logging
import math
import os
import random
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TextIO, get_args

from pir.models import Pool
from pir.models.ledger import (
    DayState,
    LedgerState,
    PoolState,
    Reservation,
    SpenderState,
)
from pir.models.ledger import Spender as Spender

if TYPE_CHECKING:
    from pir.config import Settings

logger = logging.getLogger("pir.spend")

POOLS: tuple[Pool, ...] = get_args(Pool)
MAX_ATTEMPTS = 3
JITTER_MAX_S = 0.05
_EPS = 1e-9

RefusalReason = Literal["paused", "daily_limit", "ledger_unavailable"]
DenialReason = Literal["spend_cap_reached", "scan_budget_reached"]


class LedgerConflict(Exception):
    """`save` was given a stale parent revision: someone else wrote since the load."""


class LedgerUnavailable(Exception):
    """The ledger cannot be loaded or saved. The meter fails closed."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class ScanBudgetLimits:
    """Per-scan reservation per pool and the pause threshold (SCAN_BUDGET_*, SCAN_MIN_START_USD)."""

    usd: Mapping[Pool, float]
    min_start_usd: float = 0.05

    @classmethod
    def from_settings(cls, settings: "Settings") -> "ScanBudgetLimits":
        return cls(
            usd={
                "nebius": settings.scan_budget_nebius_usd,
                "tavily": settings.scan_budget_tavily_usd,
            },
            min_start_usd=settings.scan_min_start_usd,
        )


@dataclass(frozen=True)
class Refused:
    """`reserve` did not reserve. `retry_after_s` is set for "daily_limit" only."""

    reason: RefusalReason
    retry_after_s: int | None = None


@dataclass(frozen=True)
class SpendHold:
    pool: Pool
    amount_usd: float
    hold_id: int


@dataclass(frozen=True)
class SpendDenied:
    """The hold would exceed the scan's reservation (or the scan overran an earlier hold)."""

    pool: Pool
    reason: DenialReason


class SpendLedger(Protocol):
    """`load` raises LedgerUnavailable; `save` raises LedgerConflict (stale parent) or
    LedgerUnavailable."""

    async def load(self) -> tuple[LedgerState, str]: ...

    async def save(self, state: LedgerState, parent: str) -> str: ...


def initial_state(caps: Mapping[Pool, float], now: datetime) -> LedgerState:
    """File-ledger start state: only the `local` spender, whose allowance is the build cap."""
    caps_usd = {p: float(caps[p]) for p in POOLS}
    return LedgerState(
        schema_version=2,
        caps_usd=caps_usd,
        spenders={
            "local": SpenderState(
                allowance_usd=dict(caps_usd),
                pools={p: PoolState(spent_usd=0.0, reserved_usd=0.0) for p in POOLS},
                day=None,
            )
        },
        reservations=[],
        cas_verified=None,
        updated_at=now,
    )


_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_REPLACE_TRIES = 5
_REPLACE_PAUSE_S = 0.01


def _lock_for(path: Path) -> threading.Lock:
    """One lock per ledger file for the whole process, however many instances point at it."""
    key = os.path.normcase(os.path.abspath(path))
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


def _replace_with_retry(src: str, dst: Path) -> None:
    """`os.replace`, retried briefly: on Windows it fails while another process has `dst` open."""
    for attempt in range(1, _REPLACE_TRIES + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES:
                raise
            time.sleep(_REPLACE_PAUSE_S * attempt)


class FileSpendLedger:
    """Local JSON ledger. Revision = a counter in the file; saves go to a temp file, then
    `os.replace`, so a reader never sees a partial file. A save whose parent is not the file's
    current revision raises `LedgerConflict`. The check and replace are serialised per file
    within this process only, which is enough for the single-user build ledger."""

    def __init__(
        self, path: str | Path, caps: Mapping[Pool, float], clock: Clock | None = None
    ) -> None:
        self._path = Path(path)
        self._caps = dict(caps)
        self._clock = clock or SystemClock()
        self._lock = _lock_for(self._path)

    @classmethod
    def from_settings(cls, settings: "Settings", clock: Clock | None = None) -> "FileSpendLedger":
        caps: dict[Pool, float] = {
            "nebius": settings.spend_cap_nebius_usd,
            "tavily": settings.spend_cap_tavily_usd,
        }
        return cls(settings.spend_ledger_path, caps, clock)

    async def load(self) -> tuple[LedgerState, str]:
        state, revision = await asyncio.to_thread(self._load_sync)
        return state, str(revision)

    async def save(self, state: LedgerState, parent: str) -> str:
        return str(await asyncio.to_thread(self._save_sync, state, parent))

    def _read(self) -> tuple[LedgerState, int]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return initial_state(self._caps, self._clock.now()), 0
        except OSError as exc:
            raise LedgerUnavailable(f"ledger file unreadable: {type(exc).__name__}") from exc
        try:
            data = json.loads(text)
            revision = data["revision"]
            if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
                raise ValueError("revision")
            state = LedgerState.model_validate_json(json.dumps(data["state"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise LedgerUnavailable("ledger file is not valid") from exc
        return state, revision

    def _load_sync(self) -> tuple[LedgerState, int]:
        with self._lock:
            return self._read()

    def _save_sync(self, state: LedgerState, parent: str) -> int:
        with self._lock:
            _, current = self._read()
            if parent != str(current):
                raise LedgerConflict(f"stale revision {parent!r}, current {current}")
            revision = current + 1
            payload = json.dumps(
                {"revision": revision, "state": json.loads(state.model_dump_json())}, indent=2
            )
            tmp_name: str | None = None
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(
                    dir=self._path.parent, prefix=self._path.name + ".", suffix=".tmp"
                )
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                _replace_with_retry(tmp_name, self._path)
                tmp_name = None
            except OSError as exc:
                raise LedgerUnavailable(f"ledger file not writable: {type(exc).__name__}") from exc
            finally:
                if tmp_name is not None:
                    Path(tmp_name).unlink(missing_ok=True)
            return revision


class ScanBudget:
    """One scan's reservation. `try_spend` holds an estimate against it, `record` swaps the
    hold for the actual cost. Everything is synchronous, so concurrent callers cannot overshoot.
    Once any call costs more than its hold the scan is overrun and every later hold is denied."""

    def __init__(
        self,
        scan_id: str,
        reserved_usd: Mapping[Pool, float],
        denial_reason: Mapping[Pool, DenialReason],
    ) -> None:
        self.scan_id = scan_id
        self._reserved = {p: float(reserved_usd.get(p, 0.0)) for p in POOLS}
        self._denial = {p: denial_reason.get(p, "scan_budget_reached") for p in POOLS}
        self._spent: dict[Pool, float] = dict.fromkeys(POOLS, 0.0)
        self._held: dict[Pool, float] = dict.fromkeys(POOLS, 0.0)
        self._open: dict[int, SpendHold] = {}
        self._next_id = 1
        self._overrun = False
        self.settled = False

    @property
    def reserved_usd(self) -> dict[Pool, float]:
        return dict(self._reserved)

    @property
    def overrun(self) -> bool:
        return self._overrun

    def usage_usd(self) -> dict[Pool, float]:
        """Recorded actuals plus holds still open (an unrecorded call may have been paid)."""
        return {p: self._spent[p] + self._held[p] for p in POOLS}

    def try_spend(self, pool: Pool, estimate_usd: float) -> SpendHold | SpendDenied:
        if pool not in POOLS:
            raise ValueError(f"unknown pool: {pool!r}")
        if not math.isfinite(estimate_usd) or estimate_usd < 0:
            raise ValueError("estimate_usd must be a finite number, 0 or more")
        if self.settled or self._overrun:
            return SpendDenied(pool, "scan_budget_reached")
        if self._spent[pool] + self._held[pool] + estimate_usd > self._reserved[pool] + _EPS:
            return SpendDenied(pool, self._denial[pool])
        hold = SpendHold(pool, float(estimate_usd), self._next_id)
        self._next_id += 1
        self._open[hold.hold_id] = hold
        self._held[pool] += hold.amount_usd
        return hold

    def record(self, hold: SpendHold, actual_usd: float) -> None:
        if not math.isfinite(actual_usd) or actual_usd < 0:
            raise ValueError("actual_usd must be a finite number, 0 or more")
        if self._open.pop(hold.hold_id, None) is None:
            raise ValueError("hold is unknown or already recorded")
        self._held[hold.pool] = max(0.0, self._held[hold.pool] - hold.amount_usd)
        self._spent[hold.pool] += float(actual_usd)
        if actual_usd > hold.amount_usd + _EPS:
            self._overrun = True


@dataclass
class _Plan:
    """What one mutation produced: the new state to save (None = nothing to save) and a result."""

    state: LedgerState | None
    result: object = field(default=None)


def _seconds_to_next_utc_midnight(now: datetime) -> int:
    utc = now.astimezone(UTC)
    midnight = datetime.combine(utc.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    return max(1, math.ceil((midnight - utc).total_seconds()))


def _pool(spender_state: SpenderState, pool: Pool) -> PoolState:
    return spender_state.pools.get(pool, PoolState(spent_usd=0.0, reserved_usd=0.0))


def _expire_stale(state: LedgerState, now: datetime, max_age_s: float) -> LedgerState:
    """A reservation older than `max_age_s` (any spender) counts as fully spent."""
    keep: list[Reservation] = []
    spenders = dict(state.spenders)
    changed = False
    for res in state.reservations:
        if (now - res.created_at).total_seconds() <= max_age_s or res.spender not in spenders:
            keep.append(res)
            continue
        changed = True
        ss = spenders[res.spender]
        pools = dict(ss.pools)
        for pool, amount in res.amounts.items():
            ps = _pool(ss, pool)
            pools[pool] = PoolState(
                spent_usd=ps.spent_usd + amount,
                reserved_usd=max(0.0, ps.reserved_usd - amount),
            )
        spenders[res.spender] = ss.model_copy(update={"pools": pools})
    if not changed:
        return state
    return state.model_copy(update={"spenders": spenders, "reservations": keep})


def _exhausted(state: LedgerState, spender: Spender, min_start_usd: float) -> bool:
    ss = state.spenders.get(spender)
    if ss is None:
        return True
    for pool in POOLS:
        ps = _pool(ss, pool)
        allowance = ss.allowance_usd.get(pool, 0.0)
        if ps.spent_usd + ps.reserved_usd + min_start_usd > allowance + _EPS:
            return True
    return False


class SpendMeter:
    """Admission for one spender (`serve` = hosted; `scan`, `eval`, `record` = local)."""

    def __init__(
        self,
        ledger: SpendLedger,
        spender: Spender,
        per_scan: ScanBudgetLimits,
        clock: Clock,
        *,
        scan_timeout_s: float = 900.0,
        end_date: date = date(2026, 12, 15),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ledger = ledger
        self._spender: Spender = spender
        self._limits = per_scan
        self._clock = clock
        self._scan_timeout_s = scan_timeout_s
        self._end_date = end_date
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._state: LedgerState | None = None
        self._ledger_failed = False

    @classmethod
    def from_settings(
        cls, ledger: SpendLedger, spender: Spender, settings: "Settings", clock: Clock
    ) -> "SpendMeter":
        return cls(
            ledger,
            spender,
            ScanBudgetLimits.from_settings(settings),
            clock,
            scan_timeout_s=settings.scan_timeout_s,
            end_date=settings.spend_end_date,
        )

    def status(self) -> Literal["enabled", "paused"]:
        """ "paused" after a ledger failure, or when the allowance cannot cover another scan."""
        if self._ledger_failed:
            return "paused"
        if self._state is not None and _exhausted(
            self._state, self._spender, self._limits.min_start_usd
        ):
            return "paused"
        return "enabled"

    def remaining_fractions(self) -> dict[Pool, float] | None:
        """clamp((cap - settled spent over all spenders) / cap, 0, 1); None before any load."""
        state = self._state
        if state is None:
            return None
        fractions: dict[Pool, float] = {}
        for pool in POOLS:
            cap = state.caps_usd.get(pool, 0.0)
            if cap <= 0:
                fractions[pool] = 0.0
                continue
            spent = sum(_pool(s, pool).spent_usd for s in state.spenders.values())
            fractions[pool] = min(1.0, max(0.0, (cap - spent) / cap))
        return fractions

    def summary_lines(self) -> list[str]:
        if self._state is None:
            return ["spend: ledger not read"]
        return spend_summary_lines(self._state, self._spender)

    async def reserve(self, scan_id: str) -> ScanBudget | Refused:
        if not scan_id:
            raise ValueError("scan_id must not be empty")
        async with self._lock:
            try:
                plan = await self._apply(lambda state: self._plan_reserve(state, scan_id))
            except LedgerUnavailable as exc:
                self._ledger_failed = True
                logger.error("spend ledger unavailable on reserve: %s", exc)
                return Refused("ledger_unavailable")
            self._ledger_failed = False
            assert isinstance(plan.result, ScanBudget | Refused)
            return plan.result

    async def settle(self, budget: ScanBudget) -> None:
        """Move the scan's actual spend to `spent` and release the rest. A ledger failure pauses
        the meter and is logged, not raised: the report must still complete (NFR-06). The
        reservation then stays in the ledger and counts as spent once it is stale."""
        async with self._lock:
            if budget.settled:
                return
            try:
                await self._apply(lambda state: self._plan_settle(state, budget))
            except LedgerUnavailable as exc:
                self._ledger_failed = True
                logger.error("spend ledger unavailable on settle: %s", exc)
                return
            self._ledger_failed = False
            budget.settled = True

    async def _apply(self, plan_fn: Callable[[LedgerState], _Plan]) -> _Plan:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            state, revision = await self._ledger.load()
            state = _expire_stale(state, self._clock.now(), 2 * self._scan_timeout_s)
            self._state = state
            plan = plan_fn(state)
            if plan.state is None:
                return plan
            try:
                await self._ledger.save(plan.state, revision)
            except LedgerConflict:
                if attempt == MAX_ATTEMPTS:
                    break
                await self._sleep(random.uniform(0.0, JITTER_MAX_S * attempt))
                continue
            self._state = plan.state
            return plan
        raise LedgerUnavailable(f"lost the compare-and-swap {MAX_ATTEMPTS} times")

    def _plan_reserve(self, state: LedgerState, scan_id: str) -> _Plan:
        now = self._clock.now()
        ss = state.spenders.get(self._spender)
        if ss is None:
            return _Plan(None, Refused("paused"))
        min_start = self._limits.min_start_usd
        pools = {p: _pool(ss, p) for p in POOLS}
        allowance = {p: ss.allowance_usd.get(p, 0.0) for p in POOLS}
        for p in POOLS:
            if pools[p].spent_usd + pools[p].reserved_usd + min_start > allowance[p] + _EPS:
                return _Plan(None, Refused("paused"))

        day = ss.day
        daily_remaining: dict[Pool, float] = {}
        if self._spender == "hosted":
            today = now.astimezone(UTC).date()
            if day is None or day.day != today:
                day = DayState(
                    day=today, start_spent_usd={p: float(pools[p].spent_usd) for p in POOLS}
                )
            days_left = max(1, (self._end_date - today).days + 1)
            for p in POOLS:
                start = day.start_spent_usd.get(p, pools[p].spent_usd)
                ceiling = max(0.0, (allowance[p] - start) / days_left)
                used_today = pools[p].spent_usd + pools[p].reserved_usd - start
                if used_today + min_start > ceiling + _EPS:
                    return _Plan(
                        None,
                        Refused("daily_limit", retry_after_s=_seconds_to_next_utc_midnight(now)),
                    )
                daily_remaining[p] = ceiling - used_today

        amounts: dict[Pool, float] = {}
        reasons: dict[Pool, DenialReason] = {}
        new_pools: dict[Pool, PoolState] = {}
        for p in POOLS:
            allowance_left = allowance[p] - pools[p].spent_usd - pools[p].reserved_usd
            per_scan = float(self._limits.usd.get(p, 0.0))
            daily_left = daily_remaining.get(p, math.inf)
            amounts[p] = max(0.0, min(per_scan, allowance_left, daily_left))
            cut_by_allowance = (
                allowance_left < per_scan - _EPS and allowance_left <= daily_left + _EPS
            )
            reasons[p] = "spend_cap_reached" if cut_by_allowance else "scan_budget_reached"
            new_pools[p] = PoolState(
                spent_usd=pools[p].spent_usd, reserved_usd=pools[p].reserved_usd + amounts[p]
            )

        reservation = Reservation(
            scan_id=scan_id, spender=self._spender, created_at=now, amounts=dict(amounts)
        )
        spenders = dict(state.spenders)
        spenders[self._spender] = ss.model_copy(update={"pools": new_pools, "day": day})
        new_state = state.model_copy(
            update={
                "spenders": spenders,
                "reservations": [*state.reservations, reservation],
                "updated_at": now,
            }
        )
        return _Plan(new_state, ScanBudget(scan_id, amounts, reasons))

    def _plan_settle(self, state: LedgerState, budget: ScanBudget) -> _Plan:
        now = self._clock.now()
        ss = state.spenders.get(self._spender)
        if ss is None:
            return _Plan(None)
        usage = budget.usage_usd()
        index = next(
            (
                i
                for i, r in enumerate(state.reservations)
                if r.scan_id == budget.scan_id and r.spender == self._spender
            ),
            None,
        )
        new_pools: dict[Pool, PoolState] = {}
        for p in POOLS:
            ps = _pool(ss, p)
            if index is not None:
                held = state.reservations[index].amounts.get(p, 0.0)
                new_pools[p] = PoolState(
                    spent_usd=ps.spent_usd + usage[p],
                    reserved_usd=max(0.0, ps.reserved_usd - held),
                )
            else:
                # Expired: the whole reservation was already counted as spent at load.
                over = max(0.0, usage[p] - budget.reserved_usd[p])
                new_pools[p] = PoolState(
                    spent_usd=ps.spent_usd + over, reserved_usd=ps.reserved_usd
                )
        reservations = [r for i, r in enumerate(state.reservations) if i != index]
        spenders = dict(state.spenders)
        spenders[self._spender] = ss.model_copy(update={"pools": new_pools})
        new_state = state.model_copy(
            update={"spenders": spenders, "reservations": reservations, "updated_at": now}
        )
        return _Plan(new_state)


def spend_summary_lines(state: LedgerState, spender: Spender) -> list[str]:
    """One line per pool: spend used and remaining allowance for `spender`."""
    ss = state.spenders.get(spender)
    if ss is None:
        return [f"spend: no allowance for {spender}"]
    lines = []
    for pool in POOLS:
        ps = _pool(ss, pool)
        allowance = ss.allowance_usd.get(pool, 0.0)
        remaining = max(0.0, allowance - ps.spent_usd - ps.reserved_usd)
        lines.append(
            f"spend {pool}: used ${ps.spent_usd:.4f}, "
            f"remaining ${remaining:.4f} of ${allowance:.2f} allowance ({spender})"
        )
    return lines


def print_spend_summary(meter: SpendMeter, file: TextIO | None = None) -> None:
    """Print the meter's spend lines to stderr (CLI scan, eval and record call this at the end)."""
    out = file if file is not None else sys.stderr
    for line in meter.summary_lines():
        out.write(line + "\n")
