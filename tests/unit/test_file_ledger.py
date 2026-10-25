"""FileSpendLedger: atomic writes, revision counter, stale-revision detection (AC-51, AC-53)."""

import json

import pytest
from fakes.ledger import make_state

from pir.config import Settings
from pir.spend import (
    FileSpendLedger,
    LedgerConflict,
    LedgerUnavailable,
    Refused,
    ScanBudget,
    ScanBudgetLimits,
    SpendMeter,
    initial_state,
)

CAPS = {"nebius": 10.0, "tavily": 5.0}


def _ledger(tmp_path, clock, name="spend_ledger.json") -> FileSpendLedger:
    return FileSpendLedger(tmp_path / name, CAPS, clock)


async def test_ac51_missing_file_loads_the_build_caps_for_local_only(tmp_path, frozen_clock):
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    assert revision == "0"
    assert state == initial_state(CAPS, frozen_clock.now())
    assert list(state.spenders) == ["local"]
    assert state.spenders["local"].allowance_usd == CAPS
    assert state.caps_usd == CAPS
    assert state.cas_verified is None and state.reservations == []
    assert not (tmp_path / "spend_ledger.json").exists()  # loading creates nothing


async def test_ac51_save_round_trips_and_bumps_the_revision(tmp_path, frozen_clock):
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    changed = make_state(frozen_clock.now(), {"local": CAPS}, spent={"local": {"nebius": 1.5}})
    assert await ledger.save(changed, revision) == "1"
    assert await ledger.save(changed, "1") == "2"
    loaded, revision = await _ledger(tmp_path, frozen_clock).load()  # a fresh instance reads it
    assert (loaded, revision) == (changed, "2")
    data = json.loads((tmp_path / "spend_ledger.json").read_text(encoding="utf-8"))
    assert data["revision"] == 2 and data["state"]["schema_version"] == 2


async def test_ac51_save_with_a_stale_revision_is_a_conflict_and_changes_nothing(
    tmp_path, frozen_clock
):
    path = tmp_path / "spend_ledger.json"
    first, second = _ledger(tmp_path, frozen_clock), _ledger(tmp_path, frozen_clock)
    _, rev_a = await first.load()
    _, rev_b = await second.load()
    state_a = make_state(frozen_clock.now(), {"local": CAPS}, spent={"local": {"nebius": 1.0}})
    state_b = make_state(frozen_clock.now(), {"local": CAPS}, spent={"local": {"nebius": 9.0}})
    await first.save(state_a, rev_a)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(LedgerConflict):
        await second.save(state_b, rev_b)
    assert path.read_text(encoding="utf-8") == before
    with pytest.raises(LedgerConflict):
        await second.save(state_b, "not-a-revision")


async def test_ac51_save_writes_a_temp_file_then_replaces_and_leaves_no_temp_file(
    tmp_path, frozen_clock, monkeypatch
):
    calls: list[tuple[str, str]] = []
    real_replace = __import__("os").replace

    def spy(src, dst):
        calls.append((str(src), str(dst)))
        assert str(src) != str(dst)
        real_replace(src, dst)

    monkeypatch.setattr("pir.spend.os.replace", spy)
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    await ledger.save(state, revision)
    assert len(calls) == 1 and calls[0][1] == str(tmp_path / "spend_ledger.json")
    assert [p.name for p in tmp_path.iterdir()] == ["spend_ledger.json"]


async def test_ac51_a_failed_replace_keeps_the_old_file_and_cleans_up(
    tmp_path, frozen_clock, monkeypatch
):
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    await ledger.save(state, revision)
    path = tmp_path / "spend_ledger.json"
    before = path.read_text(encoding="utf-8")

    def broken(src, dst):
        raise PermissionError("denied")

    monkeypatch.setattr("pir.spend.os.replace", broken)
    with pytest.raises(LedgerUnavailable):
        await ledger.save(state, "1")
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == ["spend_ledger.json"]


async def test_ac51_save_creates_missing_parent_directories(tmp_path, frozen_clock):
    ledger = FileSpendLedger(tmp_path / "data" / "nested" / "ledger.json", CAPS, frozen_clock)
    state, revision = await ledger.load()
    assert await ledger.save(state, revision) == "1"
    assert (tmp_path / "data" / "nested" / "ledger.json").is_file()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        "{}",
        '{"revision": "1", "state": {}}',
        '{"revision": -1, "state": {}}',
        '{"revision": true, "state": {}}',
        '{"revision": 1, "state": {"schema_version": 1}}',
        '{"revision": 1, "state": null}',
    ],
)
async def test_ac51_corrupt_file_is_ledger_unavailable(tmp_path, frozen_clock, content):
    (tmp_path / "spend_ledger.json").write_text(content, encoding="utf-8")
    with pytest.raises(LedgerUnavailable):
        await _ledger(tmp_path, frozen_clock).load()


async def test_ac51_state_breaking_the_allowance_sum_rule_is_ledger_unavailable(
    tmp_path, frozen_clock
):
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    await ledger.save(state, revision)
    path = tmp_path / "spend_ledger.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["state"]["caps_usd"]["nebius"] = 99.0  # allowances no longer sum to the cap
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LedgerUnavailable):
        await ledger.load()


async def test_ac51_unwritable_location_is_ledger_unavailable(tmp_path, frozen_clock):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    ledger = FileSpendLedger(blocker / "ledger.json", CAPS, frozen_clock)
    state, revision = await ledger.load()
    with pytest.raises(LedgerUnavailable):
        await ledger.save(state, revision)


def test_ac51_from_settings_uses_the_build_caps_and_path(tmp_path, frozen_clock):
    settings = Settings(
        _env_file=None,
        spend_cap_nebius_usd=3,
        spend_cap_tavily_usd=1.5,
        spend_ledger_path=str(tmp_path / "x.json"),
    )
    ledger = FileSpendLedger.from_settings(settings, frozen_clock)
    assert ledger._path == tmp_path / "x.json"
    assert ledger._caps == {"nebius": 3, "tavily": 1.5}


async def test_ac53_meter_on_the_file_ledger_reserves_and_settles_across_restarts(
    tmp_path, frozen_clock
):
    limits = ScanBudgetLimits(usd={"nebius": 0.75, "tavily": 0.40}, min_start_usd=0.05)

    def new_meter() -> SpendMeter:
        return SpendMeter(_ledger(tmp_path, frozen_clock), "local", limits, frozen_clock)

    meter = new_meter()
    budget = await meter.reserve("scan-1")
    assert isinstance(budget, ScanBudget)
    state, _ = await _ledger(tmp_path, frozen_clock).load()
    assert [r.scan_id for r in state.reservations] == ["scan-1"]  # persisted before returning
    hold = budget.try_spend("nebius", 0.5)
    budget.record(hold, 0.2)  # type: ignore[arg-type]
    await meter.settle(budget)

    restarted = new_meter()
    second = await restarted.reserve("scan-2")
    assert isinstance(second, ScanBudget)
    state, revision = await _ledger(tmp_path, frozen_clock).load()
    assert revision == "3"
    assert state.spenders["local"].pools["nebius"].spent_usd == pytest.approx(0.2)
    assert restarted.remaining_fractions()["nebius"] == pytest.approx(0.98)  # type: ignore[index]


async def test_ac51_two_meters_on_one_file_never_both_take_the_last_money(tmp_path, frozen_clock):
    import asyncio

    caps = {"nebius": 0.78, "tavily": 5.0}
    limits = ScanBudgetLimits(usd={"nebius": 0.75, "tavily": 0.40}, min_start_usd=0.05)
    meters = [
        SpendMeter(
            FileSpendLedger(tmp_path / "l.json", caps, frozen_clock), "local", limits, frozen_clock
        )
        for _ in range(2)
    ]
    results = await asyncio.gather(meters[0].reserve("a"), meters[1].reserve("b"))
    assert sorted(type(r).__name__ for r in results) == ["Refused", "ScanBudget"]
    assert Refused("paused") in results
