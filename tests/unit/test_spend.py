"""Spend meter and scan budget (AC-51, AC-53, AC-76 meter part; security findings 2 to 4)."""

import asyncio
import io
import math
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import FrozenClock
from fakes.ledger import MemorySpendLedger, make_state

from pir.config import Settings
from pir.models import Pool
from pir.models.ledger import DayState, PoolState, Reservation
from pir.spend import (
    Refused,
    ScanBudget,
    ScanBudgetLimits,
    SpendDenied,
    SpendHold,
    SpendMeter,
    print_spend_summary,
    spend_summary_lines,
)

SCAN_TIMEOUT_S = 900.0
END_DATE = date(2026, 12, 15)
LIMITS = ScanBudgetLimits(usd={"nebius": 0.75, "tavily": 0.40}, min_start_usd=0.05)
BIG: dict[Pool, float] = {"nebius": 10.0, "tavily": 5.0}


def _meter(
    ledger: MemorySpendLedger,
    spender: str,
    clock: FrozenClock,
    sleeps: list[float] | None = None,
) -> SpendMeter:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return SpendMeter(
        ledger,
        spender,  # type: ignore[arg-type]
        LIMITS,
        clock,
        scan_timeout_s=SCAN_TIMEOUT_S,
        end_date=END_DATE,
        sleep=sleep,
    )


def _local_ledger(
    clock: FrozenClock, spent: dict[Pool, float] | None = None, **kwargs
) -> MemorySpendLedger:
    state = make_state(clock.now(), {"local": BIG}, spent={"local": spent or {}})
    return MemorySpendLedger(state, **kwargs)


def _pool(ledger: MemorySpendLedger, spender: str, pool: Pool) -> PoolState:
    return ledger.state.spenders[spender].pools[pool]  # type: ignore[index]


async def _reserve(meter: SpendMeter, scan_id: str = "scan-1") -> ScanBudget:
    result = await meter.reserve(scan_id)
    assert isinstance(result, ScanBudget), result
    return result


# --- reserve: allowance check and persistence (AC-51, AC-53) ----------------------------


async def test_ac51_reserve_persists_before_return(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    budget = await _reserve(_meter(ledger, "local", frozen_clock))
    assert ledger.saves == 1
    assert [r.scan_id for r in ledger.state.reservations] == ["scan-1"]
    assert _pool(ledger, "local", "nebius").reserved_usd == pytest.approx(0.75)
    assert _pool(ledger, "local", "tavily").reserved_usd == pytest.approx(0.40)
    assert budget.reserved_usd == {"nebius": pytest.approx(0.75), "tavily": pytest.approx(0.40)}


@pytest.mark.parametrize("pool", ["nebius", "tavily"])
async def test_ac51_reserve_refuses_paused_when_either_pool_is_short(frozen_clock, pool):
    spent = {pool: BIG[pool] - 0.04}  # 0.04 left, below SCAN_MIN_START_USD
    ledger = _local_ledger(frozen_clock, spent=spent)
    meter = _meter(ledger, "local", frozen_clock)
    assert await meter.reserve("scan-1") == Refused("paused")
    assert ledger.saves == 0 and ledger.state.reservations == []
    assert meter.status() == "paused"


async def test_ac51_reserve_allowed_exactly_at_min_start(frozen_clock):
    ledger = _local_ledger(frozen_clock, spent={"nebius": 9.95})
    budget = await _reserve(_meter(ledger, "local", frozen_clock))
    assert budget.reserved_usd["nebius"] == pytest.approx(0.05)


async def test_ac51_reserve_counts_open_reservations(frozen_clock):
    ledger = _local_ledger(frozen_clock, spent={"nebius": 9.0})
    meter = _meter(ledger, "local", frozen_clock)
    first = await _reserve(meter, "a")
    assert first.reserved_usd["nebius"] == pytest.approx(0.75)
    second = await _reserve(meter, "b")  # 9.75 + 0.05 <= 10 -> 0.25 left for the second
    assert second.reserved_usd["nebius"] == pytest.approx(0.25)
    assert await meter.reserve("c") == Refused("paused")  # 10.0 + 0.05 > 10


async def test_ac51_empty_scan_id_is_rejected(frozen_clock):
    meter = _meter(_local_ledger(frozen_clock), "local", frozen_clock)
    with pytest.raises(ValueError):
        await meter.reserve("")


async def test_ac51_missing_spender_is_paused(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    assert await _meter(ledger, "hosted", frozen_clock).reserve("s") == Refused("paused")


async def test_ac51_concurrent_reserve_near_allowance_one_succeeds(frozen_clock):
    state = make_state(frozen_clock.now(), {"local": {"nebius": 0.78, "tavily": 5.0}})
    ledger = MemorySpendLedger(state)
    meter = _meter(ledger, "local", frozen_clock)
    results = await asyncio.gather(meter.reserve("a"), meter.reserve("b"))
    assert sorted(type(r).__name__ for r in results) == ["Refused", "ScanBudget"]
    assert Refused("paused") in results
    assert _pool(ledger, "local", "nebius").reserved_usd == pytest.approx(0.75)


async def test_ac51_two_meters_on_one_ledger_conflict_then_one_succeeds(frozen_clock):
    state = make_state(frozen_clock.now(), {"local": {"nebius": 0.78, "tavily": 5.0}})
    ledger = MemorySpendLedger(state, yield_io=True)
    meters = [_meter(ledger, "local", frozen_clock) for _ in range(2)]
    results = await asyncio.gather(meters[0].reserve("a"), meters[1].reserve("b"))
    assert sorted(type(r).__name__ for r in results) == ["Refused", "ScanBudget"]
    assert _pool(ledger, "local", "nebius").reserved_usd == pytest.approx(0.75)
    assert len(ledger.state.reservations) == 1
    assert ledger.save_attempts == 2  # the loser's first save hit a stale parent and never landed


# --- fail closed (finding 2) --------------------------------------------------------------


async def test_ac51_save_failure_pauses_and_refuses(frozen_clock):
    ledger = _local_ledger(frozen_clock, fail_saves=True)
    meter = _meter(ledger, "local", frozen_clock)
    assert await meter.reserve("s") == Refused("ledger_unavailable")
    assert meter.status() == "paused"
    assert ledger.state.reservations == []


async def test_ac51_load_failure_pauses_and_refuses(frozen_clock):
    ledger = _local_ledger(frozen_clock, fail_loads=True)
    meter = _meter(ledger, "local", frozen_clock)
    assert await meter.reserve("s") == Refused("ledger_unavailable")
    assert meter.status() == "paused"
    assert meter.remaining_fractions() is None


async def test_ac51_meter_recovers_when_the_ledger_comes_back(frozen_clock):
    ledger = _local_ledger(frozen_clock, fail_saves=1)
    meter = _meter(ledger, "local", frozen_clock)
    assert await meter.reserve("s") == Refused("ledger_unavailable")
    assert isinstance(await meter.reserve("s"), ScanBudget)
    assert meter.status() == "enabled"


async def test_ac51_conflict_is_reloaded_and_reapplied(frozen_clock):
    ledger = _local_ledger(frozen_clock, conflicts=1)
    sleeps: list[float] = []
    budget = await _reserve(_meter(ledger, "local", frozen_clock, sleeps))
    assert ledger.loads == 2 and ledger.save_attempts == 2 and ledger.saves == 1
    assert len(sleeps) == 1 and 0 <= sleeps[0] <= 0.1
    assert [r.scan_id for r in ledger.state.reservations] == ["scan-1"]
    assert budget.reserved_usd["nebius"] == pytest.approx(0.75)


async def test_ac51_reapply_sees_the_other_writers_change(frozen_clock):
    def other_writer(ledger: MemorySpendLedger) -> None:
        ledger.state = make_state(
            frozen_clock.now(), {"local": BIG}, spent={"local": {"nebius": 9.99}}
        )

    ledger = _local_ledger(frozen_clock, conflicts=1, conflict_hook=other_writer)
    assert await _meter(ledger, "local", frozen_clock).reserve("s") == Refused("paused")
    assert ledger.saves == 0


async def test_ac51_three_conflicts_fail_closed(frozen_clock):
    ledger = _local_ledger(frozen_clock, conflicts=3)
    sleeps: list[float] = []
    meter = _meter(ledger, "local", frozen_clock, sleeps)
    assert await meter.reserve("s") == Refused("ledger_unavailable")
    assert ledger.save_attempts == 3 and ledger.saves == 0
    assert len(sleeps) == 2
    assert meter.status() == "paused"
    assert ledger.state.reservations == []


# --- spenders independent in one ledger ---------------------------------------------------


async def test_ac51_hosted_and_local_allowances_are_independent(frozen_clock):
    state = make_state(
        frozen_clock.now(),
        {"hosted": {"nebius": 6.0, "tavily": 3.0}, "local": {"nebius": 4.0, "tavily": 2.0}},
        spent={"local": {"nebius": 3.98}},  # local is out of money, hosted untouched
    )
    ledger = MemorySpendLedger(state)
    hosted = _meter(ledger, "hosted", frozen_clock)
    local = _meter(ledger, "local", frozen_clock)
    assert await local.reserve("l") == Refused("paused")
    hosted_budget = await _reserve(hosted, "h")
    assert hosted_budget.reserved_usd["nebius"] > 0
    assert _pool(ledger, "local", "nebius").reserved_usd == 0
    assert _pool(ledger, "hosted", "nebius").reserved_usd > 0
    assert ledger.state.caps_usd == {"nebius": 10.0, "tavily": 5.0}


async def test_ac51_one_spenders_allowance_does_not_borrow_from_the_other(frozen_clock):
    state = make_state(
        frozen_clock.now(),
        {"hosted": {"nebius": 9.0, "tavily": 4.0}, "local": {"nebius": 1.0, "tavily": 1.0}},
        spent={"local": {"nebius": 0.5}},
    )
    ledger = MemorySpendLedger(state)
    budget = await _reserve(_meter(ledger, "local", frozen_clock))
    assert budget.reserved_usd["nebius"] == pytest.approx(0.5)  # 1.0 - 0.5, not the global 9.5


# --- daily ceiling (finding 3) ------------------------------------------------------------


def _hosted_ledger(clock: FrozenClock, spent: dict[Pool, float] | None = None, **allow):
    allowances = {"hosted": allow or BIG, "local": {"nebius": 0.0, "tavily": 0.0}}
    return MemorySpendLedger(make_state(clock.now(), allowances, spent={"hosted": spent or {}}))


async def test_nfr09_daily_ceiling_formula_and_day_state(frozen_clock):
    ledger = _hosted_ledger(frozen_clock, spent={"nebius": 2.0})
    meter = _meter(ledger, "hosted", frozen_clock)
    days_left = (END_DATE - frozen_clock.now().date()).days + 1
    assert days_left == 52
    budget = await _reserve(meter)
    assert budget.reserved_usd["nebius"] == pytest.approx((10.0 - 2.0) / days_left)
    assert budget.reserved_usd["tavily"] == pytest.approx(5.0 / days_left)
    day = ledger.state.spenders["hosted"].day
    assert day == DayState(
        day=frozen_clock.now().date(), start_spent_usd={"nebius": 2.0, "tavily": 0.0}
    )


async def test_nfr09_ceiling_cut_is_scan_budget_reached_not_spend_cap(frozen_clock):
    budget = await _reserve(_meter(_hosted_ledger(frozen_clock), "hosted", frozen_clock))
    ceiling = 10.0 / 52
    assert budget.try_spend("nebius", ceiling + 0.01) == SpendDenied(
        "nebius", "scan_budget_reached"
    )
    assert isinstance(budget.try_spend("nebius", ceiling), SpendHold)


async def test_nfr09_daily_limit_refusal_with_retry_after(frozen_clock):
    ledger = _hosted_ledger(frozen_clock)
    meter = _meter(ledger, "hosted", frozen_clock)
    await _reserve(meter, "first")  # takes the whole day's ceiling
    refused = await meter.reserve("second")
    assert refused == Refused("daily_limit", retry_after_s=12 * 3600)  # 12:00 -> 24:00 UTC
    assert meter.status() == "enabled"  # a daily limit is not a pause
    assert len(ledger.state.reservations) == 1


async def test_nfr09_retry_after_rounds_up_to_whole_seconds(frozen_clock):
    ledger = _hosted_ledger(frozen_clock)
    meter = _meter(ledger, "hosted", frozen_clock)
    await _reserve(meter, "first")
    frozen_clock.advance(0.4)
    refused = await meter.reserve("second")
    assert isinstance(refused, Refused) and refused.retry_after_s == 12 * 3600  # ceil(43199.6)


async def test_nfr09_next_utc_day_resets_and_rolls_unspent_budget_forward(frozen_clock):
    ledger = _hosted_ledger(frozen_clock)
    meter = _meter(ledger, "hosted", frozen_clock)
    first = await _reserve(meter, "first")  # takes the whole day's ceiling
    assert await meter.reserve("same-day") == Refused("daily_limit", retry_after_s=43200)
    await meter.settle(first)  # nothing spent: the whole day's money rolls forward
    frozen_clock.advance(12 * 3600)  # 00:00 UTC next day
    budget = await _reserve(meter, "next-day")
    assert budget.reserved_usd["nebius"] == pytest.approx(10.0 / 51)
    assert ledger.state.spenders["hosted"].day.day == date(2026, 10, 26)  # type: ignore[union-attr]


async def test_nfr09_last_day_is_inclusive_and_after_it_days_left_is_one(frozen_clock):
    ledger = _hosted_ledger(frozen_clock)
    meter = _meter(ledger, "hosted", frozen_clock)
    frozen_clock._now = datetime(2026, 12, 15, 9, 0, tzinfo=UTC)
    budget = await _reserve(meter, "last-day")  # days_left = 1: ceiling = the whole remainder
    assert budget.reserved_usd["nebius"] == pytest.approx(0.75)  # per-scan budget binds
    ledger2 = _hosted_ledger(frozen_clock)
    meter2 = _meter(ledger2, "hosted", frozen_clock)
    frozen_clock._now = datetime(2026, 12, 20, 9, 0, tzinfo=UTC)
    budget2 = await _reserve(meter2, "after-end")
    assert budget2.reserved_usd["nebius"] == pytest.approx(0.75)


async def test_nfr09_allowance_cut_on_the_last_day_is_spend_cap_reached(frozen_clock):
    frozen_clock._now = datetime(2026, 12, 15, 9, 0, tzinfo=UTC)
    ledger = _hosted_ledger(frozen_clock, nebius=0.5, tavily=5.0)
    budget = await _reserve(_meter(ledger, "hosted", frozen_clock))
    assert budget.reserved_usd["nebius"] == pytest.approx(0.5)
    assert budget.try_spend("nebius", 0.6) == SpendDenied("nebius", "spend_cap_reached")


async def test_nfr09_daily_ceiling_does_not_apply_to_local(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    meter = _meter(ledger, "local", frozen_clock)
    budget = await _reserve(meter)
    assert budget.reserved_usd["nebius"] == pytest.approx(0.75)
    assert ledger.state.spenders["local"].day is None
    assert isinstance(await meter.reserve("again"), ScanBudget)


# --- ScanBudget: holds, records, denial reasons -------------------------------------------


async def test_ac53_try_spend_denies_any_hold_over_the_reservation(frozen_clock):
    budget = await _reserve(_meter(_local_ledger(frozen_clock), "local", frozen_clock))
    hold = budget.try_spend("nebius", 0.5)
    assert isinstance(hold, SpendHold)
    assert budget.try_spend("nebius", 0.3) == SpendDenied("nebius", "scan_budget_reached")
    budget.record(hold, 0.1)  # the hold is swapped for the actual, freeing 0.4
    assert isinstance(budget.try_spend("nebius", 0.65), SpendHold)
    assert isinstance(budget.try_spend("tavily", 0.4), SpendHold)  # pools are separate
    assert budget.try_spend("tavily", 0.01) == SpendDenied("tavily", "scan_budget_reached")


async def test_ac53_concurrent_holds_cannot_overshoot(frozen_clock):
    budget = await _reserve(_meter(_local_ledger(frozen_clock), "local", frozen_clock))

    async def spend() -> SpendHold | SpendDenied:
        await asyncio.sleep(0)
        return budget.try_spend("nebius", 0.2)

    results = await asyncio.gather(*(spend() for _ in range(6)))
    assert sum(isinstance(r, SpendHold) for r in results) == 3  # 0.6 of 0.75; a fourth is 0.8


async def test_ac53_denial_is_spend_cap_reached_when_the_allowance_cut_the_reservation(
    frozen_clock,
):
    ledger = _local_ledger(frozen_clock, spent={"nebius": 9.7})  # 0.30 left, per-scan is 0.75
    budget = await _reserve(_meter(ledger, "local", frozen_clock))
    assert budget.reserved_usd["nebius"] == pytest.approx(0.30)
    assert budget.try_spend("nebius", 0.31) == SpendDenied("nebius", "spend_cap_reached")
    assert budget.try_spend("tavily", 0.41) == SpendDenied("tavily", "scan_budget_reached")


async def test_ac53_total_spend_never_exceeds_the_cap(frozen_clock):
    state = make_state(frozen_clock.now(), {"local": {"nebius": 1.0, "tavily": 5.0}})
    ledger = MemorySpendLedger(state)
    meter = _meter(ledger, "local", frozen_clock)
    total = 0.0
    for n in range(5):
        result = await meter.reserve(f"scan-{n}")
        if isinstance(result, Refused):
            assert result.reason == "paused"
            break
        while isinstance(hold := result.try_spend("nebius", 0.3), SpendHold):
            result.record(hold, 0.3)
            total += 0.3
        await meter.settle(result)
    assert total <= 1.0 + 1e-9
    assert _pool(ledger, "local", "nebius").spent_usd == pytest.approx(total)
    assert _pool(ledger, "local", "nebius").spent_usd <= 1.0 + 1e-9


async def test_ac53_overrun_denies_every_later_try_spend_in_that_scan(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    meter = _meter(ledger, "local", frozen_clock)
    budget = await _reserve(meter)
    hold = budget.try_spend("nebius", 0.1)
    assert isinstance(hold, SpendHold)
    budget.record(hold, 0.2)
    assert budget.overrun
    assert budget.try_spend("nebius", 0.01) == SpendDenied("nebius", "scan_budget_reached")
    assert budget.try_spend("tavily", 0.01) == SpendDenied("tavily", "scan_budget_reached")
    await meter.settle(budget)
    assert _pool(ledger, "local", "nebius").spent_usd == pytest.approx(0.2)  # actual is kept
    other = await _reserve(meter, "another-scan")  # a different scan is not affected
    assert isinstance(other.try_spend("nebius", 0.01), SpendHold)


@pytest.mark.parametrize("estimate", [-0.01, math.nan, math.inf])
async def test_ac53_invalid_estimate_is_rejected(frozen_clock, estimate):
    budget = await _reserve(_meter(_local_ledger(frozen_clock), "local", frozen_clock))
    with pytest.raises(ValueError):
        budget.try_spend("nebius", estimate)


async def test_ac53_record_rejects_bad_input(frozen_clock):
    budget = await _reserve(_meter(_local_ledger(frozen_clock), "local", frozen_clock))
    hold = budget.try_spend("nebius", 0.1)
    assert isinstance(hold, SpendHold)
    with pytest.raises(ValueError):
        budget.record(hold, -1.0)
    budget.record(hold, 0.05)
    with pytest.raises(ValueError):
        budget.record(hold, 0.05)  # already recorded
    with pytest.raises(ValueError):
        budget.record(SpendHold("nebius", 0.1, 999), 0.1)  # not from this budget
    with pytest.raises(ValueError):
        budget.try_spend("bogus", 0.1)  # type: ignore[arg-type]


# --- settle -------------------------------------------------------------------------------


async def test_ac51_settle_moves_actual_to_spent_and_releases_the_rest(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    meter = _meter(ledger, "local", frozen_clock)
    budget = await _reserve(meter)
    for pool, hold_usd, actual in (("nebius", 0.3, 0.12), ("tavily", 0.2, 0.016)):
        hold = budget.try_spend(pool, hold_usd)  # type: ignore[arg-type]
        assert isinstance(hold, SpendHold)
        budget.record(hold, actual)
    await meter.settle(budget)
    assert ledger.saves == 2  # reserve + settle
    assert ledger.state.reservations == []
    assert _pool(ledger, "local", "nebius") == PoolState(spent_usd=0.12, reserved_usd=0.0)
    assert _pool(ledger, "local", "tavily").spent_usd == pytest.approx(0.016)
    assert _pool(ledger, "local", "tavily").reserved_usd == pytest.approx(0.0)
    await meter.settle(budget)  # idempotent
    assert ledger.saves == 2
    assert budget.try_spend("nebius", 0.01) == SpendDenied("nebius", "scan_budget_reached")


async def test_ac53_settle_counts_an_unrecorded_hold_as_spent(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    meter = _meter(ledger, "local", frozen_clock)
    budget = await _reserve(meter)
    assert isinstance(budget.try_spend("nebius", 0.25), SpendHold)  # call may have been paid
    await meter.settle(budget)
    assert _pool(ledger, "local", "nebius").spent_usd == pytest.approx(0.25)


async def test_ac51_settle_failure_pauses_without_raising(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    meter = _meter(ledger, "local", frozen_clock)
    budget = await _reserve(meter)
    ledger.fail_saves = True
    await meter.settle(budget)
    assert meter.status() == "paused"
    assert not budget.settled
    assert len(ledger.state.reservations) == 1  # stays; counted as spent once stale


async def test_ac51_settle_retries_on_conflict(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    meter = _meter(ledger, "local", frozen_clock)
    budget = await _reserve(meter)
    ledger.conflicts = 2
    hold = budget.try_spend("nebius", 0.1)
    assert isinstance(hold, SpendHold)
    budget.record(hold, 0.1)
    await meter.settle(budget)
    assert budget.settled
    assert _pool(ledger, "local", "nebius").spent_usd == pytest.approx(0.1)


# --- stale reservations (finding 2 / section 11.2 rule 4) ---------------------------------


def _with_reservation(ledger: MemorySpendLedger, spender: str, created_at: datetime) -> None:
    amounts: dict[Pool, float] = {"nebius": 0.75, "tavily": 0.4}
    state = ledger.state
    ss = state.spenders[spender]  # type: ignore[index]
    pools = {
        p: PoolState(spent_usd=ps.spent_usd, reserved_usd=amounts[p]) for p, ps in ss.pools.items()
    }
    ledger.state = state.model_copy(
        update={
            "spenders": {**state.spenders, spender: ss.model_copy(update={"pools": pools})},
            "reservations": [
                Reservation(
                    scan_id="old",
                    spender=spender,
                    created_at=created_at,
                    amounts=amounts,  # type: ignore[arg-type]
                )
            ],
        }
    )


async def test_ac51_reservation_older_than_twice_scan_timeout_counts_as_spent(frozen_clock):
    state = make_state(
        frozen_clock.now(),
        {"hosted": {"nebius": 5.0, "tavily": 2.0}, "local": {"nebius": 5.0, "tavily": 3.0}},
    )
    ledger = MemorySpendLedger(state)
    _with_reservation(
        ledger, "hosted", frozen_clock.now() - timedelta(seconds=2 * SCAN_TIMEOUT_S + 1)
    )
    meter = _meter(ledger, "local", frozen_clock)  # another spender's stale reservation counts too
    budget = await _reserve(meter)
    hosted = ledger.state.spenders["hosted"]
    assert hosted.pools["nebius"] == PoolState(spent_usd=0.75, reserved_usd=0.0)
    assert hosted.pools["tavily"].spent_usd == pytest.approx(0.4)
    assert [r.scan_id for r in ledger.state.reservations] == ["scan-1"]
    assert budget.reserved_usd["nebius"] == pytest.approx(0.75)
    assert meter.remaining_fractions() == {
        "nebius": pytest.approx(0.925),
        "tavily": pytest.approx(0.92),
    }


async def test_ac51_reservation_within_twice_scan_timeout_stays_reserved(frozen_clock):
    state = make_state(
        frozen_clock.now(),
        {"hosted": {"nebius": 5.0, "tavily": 2.0}, "local": {"nebius": 5.0, "tavily": 3.0}},
    )
    ledger = MemorySpendLedger(state)
    _with_reservation(ledger, "hosted", frozen_clock.now() - timedelta(seconds=2 * SCAN_TIMEOUT_S))
    await _reserve(_meter(ledger, "local", frozen_clock))
    hosted = ledger.state.spenders["hosted"]
    assert hosted.pools["nebius"] == PoolState(spent_usd=0.0, reserved_usd=0.75)
    assert len(ledger.state.reservations) == 2


async def test_ac51_settling_after_the_reservation_went_stale_does_not_double_count(frozen_clock):
    ledger = _local_ledger(frozen_clock)
    meter = _meter(ledger, "local", frozen_clock)
    budget = await _reserve(meter)
    hold = budget.try_spend("nebius", 0.5)
    assert isinstance(hold, SpendHold)
    budget.record(hold, 0.4)
    frozen_clock.advance(2 * SCAN_TIMEOUT_S + 1)
    await meter.settle(budget)
    assert ledger.state.reservations == []
    assert _pool(ledger, "local", "nebius") == PoolState(spent_usd=0.75, reserved_usd=0.0)


# --- remaining_fractions and status (AC-76) ----------------------------------------------


async def test_ac76_fractions_are_none_before_any_load(frozen_clock):
    meter = _meter(_local_ledger(frozen_clock), "local", frozen_clock)
    assert meter.remaining_fractions() is None
    assert meter.status() == "enabled"


async def test_ac76_fractions_use_settled_spend_of_all_spenders_and_exclude_reservations(
    frozen_clock,
):
    state = make_state(
        frozen_clock.now(),
        {"hosted": {"nebius": 7.0, "tavily": 3.0}, "local": {"nebius": 3.0, "tavily": 2.0}},
        spent={"hosted": {"nebius": 2.0, "tavily": 1.0}, "local": {"nebius": 1.0}},
    )
    ledger = MemorySpendLedger(state)
    meter = _meter(ledger, "local", frozen_clock)
    budget = await _reserve(meter)
    expected = {"nebius": pytest.approx(0.7), "tavily": pytest.approx(0.8)}
    assert meter.remaining_fractions() == expected  # the open reservation is excluded
    hold = budget.try_spend("nebius", 0.2)
    assert isinstance(hold, SpendHold)
    budget.record(hold, 0.2)
    assert meter.remaining_fractions() == expected  # in-memory, unchanged until settle
    await meter.settle(budget)
    assert meter.remaining_fractions() == {
        "nebius": pytest.approx(0.68),
        "tavily": pytest.approx(0.8),
    }


@pytest.mark.parametrize(("spent", "expected"), [(1.0, 0.5), (3.0, 0.0)])
async def test_ac76_fraction_is_clamped_and_zero_for_a_zero_cap(frozen_clock, spent, expected):
    state = make_state(
        frozen_clock.now(),
        {"local": {"nebius": 2.0, "tavily": 0.0}},  # tavily cap is 0
        spent={"local": {"nebius": spent}},  # 3.0: an overrun pushed spent past the cap
    )
    meter = _meter(MemorySpendLedger(state), "local", frozen_clock)
    assert await meter.reserve("s") == Refused("paused")  # tavily allowance is 0
    assert meter.remaining_fractions() == {"nebius": expected, "tavily": 0.0}


async def test_ac76_fractions_never_exceed_one(frozen_clock):
    meter = _meter(_local_ledger(frozen_clock), "local", frozen_clock)
    await _reserve(meter)
    assert meter.remaining_fractions() == {"nebius": 1.0, "tavily": 1.0}


async def test_ac51_status_pauses_when_settled_spend_exhausts_the_allowance(frozen_clock):
    state = make_state(frozen_clock.now(), {"local": {"nebius": 0.8, "tavily": 5.0}})
    meter = _meter(MemorySpendLedger(state), "local", frozen_clock)
    budget = await _reserve(meter)
    assert budget.reserved_usd["nebius"] == pytest.approx(0.75)
    hold = budget.try_spend("nebius", 0.75)
    assert isinstance(hold, SpendHold)
    budget.record(hold, 0.76)  # a small overrun: 0.76 + 0.05 > 0.80
    await meter.settle(budget)
    assert meter.status() == "paused"
    assert await meter.reserve("next") == Refused("paused")


# --- CLI summary helper -------------------------------------------------------------------


async def test_cli_summary_prints_used_and_remaining_per_pool_to_the_given_stream(frozen_clock):
    ledger = _local_ledger(frozen_clock, spent={"nebius": 1.25, "tavily": 0.5})
    meter = _meter(ledger, "local", frozen_clock)
    assert meter.summary_lines() == ["spend: ledger not read"]
    await meter.settle(await _reserve(meter))
    out = io.StringIO()
    print_spend_summary(meter, out)
    text = out.getvalue()
    assert "spend nebius: used $1.2500, remaining $8.7500 of $10.00 allowance (local)" in text
    assert "spend tavily: used $0.5000, remaining $4.5000 of $5.00 allowance (local)" in text


def test_cli_summary_defaults_to_stderr(capsys, frozen_clock):
    ledger = _local_ledger(frozen_clock)
    meter = _meter(ledger, "local", frozen_clock)
    meter._state = ledger.state
    print_spend_summary(meter)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "spend nebius" in captured.err


def test_cli_summary_without_an_allowance_for_the_spender(frozen_clock):
    state = make_state(frozen_clock.now(), {"local": BIG})
    assert spend_summary_lines(state, "hosted") == ["spend: no allowance for hosted"]


def test_scan_budget_limits_from_settings():
    settings = Settings(_env_file=None)
    limits = ScanBudgetLimits.from_settings(settings)
    assert limits.usd == {"nebius": 0.75, "tavily": 0.40}
    assert limits.min_start_usd == 0.05
