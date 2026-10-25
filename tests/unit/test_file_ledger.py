"""FileSpendLedger: atomic writes, revision counter, stale-revision detection (AC-51, AC-53)."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fakes.deps import capture_json_logs, log_lines
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


async def test_ac51_not_a_directory_on_read_means_missing_as_on_linux(
    tmp_path, frozen_clock, monkeypatch
):
    # Linux raises NotADirectoryError where Windows raises FileNotFoundError; both mean "no file".
    def linux_read_text(self, *args, **kwargs):
        raise NotADirectoryError(20, "Not a directory")

    monkeypatch.setattr("pathlib.Path.read_text", linux_read_text)
    state, revision = await _ledger(tmp_path, frozen_clock).load()
    assert revision == "0" and state == initial_state(CAPS, frozen_clock.now())


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


# ---- M1-FIX-02: missing-file warning and the cross-process lock ----------------------------


async def test_ac51_a_missing_ledger_file_logs_a_warning(tmp_path, frozen_clock):
    with capture_json_logs() as buffer:
        await _ledger(tmp_path, frozen_clock).load()
    lines = [x for x in log_lines(buffer) if x.get("event") == "ledger_missing"]
    assert len(lines) == 1 and lines[0]["level"].upper() == "WARNING"


async def test_ac51_an_existing_ledger_file_logs_no_missing_warning(tmp_path, frozen_clock):
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    await ledger.save(state, revision)
    with capture_json_logs() as buffer:
        await ledger.load()
    assert [x for x in log_lines(buffer) if x.get("event") == "ledger_missing"] == []


async def test_ac51_a_held_lock_file_makes_the_ledger_unavailable(
    tmp_path, frozen_clock, monkeypatch
):
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    lock = tmp_path / "spend_ledger.json.lock"
    lock.write_text("1234", encoding="ascii")  # a live holder: fresh mtime
    monkeypatch.setattr(FileSpendLedger, "LOCK_WAIT_S", 0.1)
    with pytest.raises(LedgerUnavailable):
        await ledger.save(state, revision)
    assert lock.exists()  # a live holder's lock is never removed
    assert not (tmp_path / "spend_ledger.json").exists()  # nothing was written


async def test_ac51_a_stale_lock_file_is_taken_over_and_released(tmp_path, frozen_clock):
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    lock = tmp_path / "spend_ledger.json.lock"
    lock.write_text("dead", encoding="ascii")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert await ledger.save(state, revision) == "1"
    assert not lock.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["spend_ledger.json"]


async def test_ac51_the_lock_is_released_after_a_conflict(tmp_path, frozen_clock):
    ledger = _ledger(tmp_path, frozen_clock)
    state, _ = await ledger.load()
    with pytest.raises(LedgerConflict):
        await ledger.save(state, "7")
    assert not (tmp_path / "spend_ledger.json.lock").exists()


def _stale_lock(tmp_path, owner="999:dead") -> Path:
    lock = tmp_path / "spend_ledger.json.lock"
    lock.write_text(owner, encoding="ascii")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    return lock


def test_ac51_two_concurrent_takeovers_of_one_stale_lock_leave_exactly_one_holder(
    tmp_path, frozen_clock, monkeypatch
):
    """Both waiters see the stale lock and rename it at the same moment: one wins, and the
    other must not remove the lock the winner then creates."""
    _stale_lock(tmp_path)
    lock = tmp_path / "spend_ledger.json.lock"
    ledgers = [_ledger(tmp_path, frozen_clock), _ledger(tmp_path, frozen_clock)]
    real_rename = os.rename
    both_ready = threading.Barrier(2, timeout=10)

    def synced_rename(src, dst):
        if ".stale-" in str(dst):
            both_ready.wait()  # neither renames before both have seen the stale lock
        return real_rename(src, dst)

    monkeypatch.setattr("pir.spend.os.rename", synced_rename)
    guard = threading.Lock()
    inside = 0
    peak = 0
    entered = 0
    errors: list[BaseException] = []

    def worker(ledger: FileSpendLedger) -> None:
        nonlocal inside, peak, entered
        try:
            with ledger._file_lock():
                with guard:
                    inside += 1
                    entered += 1
                    peak = max(peak, inside)
                time.sleep(0.2)
                with guard:
                    inside -= 1
        except BaseException as exc:  # reported by the assertions below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(ledger,)) for ledger in ledgers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert entered == 2 and peak == 1  # they took the lock one after the other, never together
    assert not lock.exists()
    assert [p.name for p in tmp_path.iterdir()] == []  # no renamed stale file is left behind


def test_ac51_a_takeover_that_claims_a_fresh_lock_puts_it_back(tmp_path, frozen_clock):
    ledger = _ledger(tmp_path, frozen_clock)
    lock = tmp_path / "spend_ledger.json.lock"
    lock.write_text("1:live", encoding="ascii")  # fresh: not stale
    ledger._drop_if_stale(lock)
    assert lock.read_text(encoding="ascii") == "1:live"
    # the stale-looking stat then a swap to a fresh lock: claim it, see it is fresh, restore it
    fresh = tmp_path / "x.claimed"
    fresh.write_text("1:live", encoding="ascii")
    lock.unlink()
    ledger._restore(fresh, lock)
    assert lock.read_text(encoding="ascii") == "1:live" and not fresh.exists()


def test_ac51_release_does_not_remove_a_lock_owned_by_someone_else(tmp_path, frozen_clock):
    ledger = _ledger(tmp_path, frozen_clock)
    lock = tmp_path / "spend_ledger.json.lock"
    with ledger._file_lock():
        mine = lock.read_text(encoding="ascii")
        assert mine.startswith(f"{os.getpid()}:") and len(mine) > len(f"{os.getpid()}:")
        # our lock went stale and another process took over: its lock is in place now
        lock.unlink()
        lock.write_text("4242:other-token", encoding="ascii")
    assert lock.read_text(encoding="ascii") == "4242:other-token"


def test_ac51_release_removes_the_lock_it_holds_and_tolerates_a_missing_one(tmp_path, frozen_clock):
    ledger = _ledger(tmp_path, frozen_clock)
    lock = tmp_path / "spend_ledger.json.lock"
    with ledger._file_lock():
        assert lock.exists()
    assert not lock.exists()
    with ledger._file_lock():
        lock.unlink()  # taken over and already removed: release must not raise
    assert not lock.exists()


async def test_ac51_a_lock_held_by_a_live_process_is_still_fail_closed(
    tmp_path, frozen_clock, monkeypatch
):
    ledger = _ledger(tmp_path, frozen_clock)
    state, revision = await ledger.load()
    lock = tmp_path / "spend_ledger.json.lock"
    lock.write_text("1:live", encoding="ascii")
    monkeypatch.setattr(FileSpendLedger, "LOCK_WAIT_S", 0.1)
    with pytest.raises(LedgerUnavailable):
        await ledger.save(state, revision)
    assert lock.read_text(encoding="ascii") == "1:live"
    assert [p.name for p in tmp_path.iterdir()] == ["spend_ledger.json.lock"]


_WORKER = """
import asyncio, sys
from datetime import UTC, datetime
from pir.spend import FileSpendLedger, LedgerConflict, initial_state

class Clock:
    def now(self):
        return datetime(2026, 1, 1, tzinfo=UTC)

async def main(path, n):
    ledger = FileSpendLedger(path, {"nebius": 10.0, "tavily": 5.0}, Clock())
    done = 0
    while done < n:
        state, revision = await ledger.load()
        try:
            await ledger.save(state, revision)
            done += 1
        except LedgerConflict:
            pass

asyncio.run(main(sys.argv[1], int(sys.argv[2])))
"""


@pytest.mark.slow
def test_ac51_concurrent_processes_never_lose_a_save(tmp_path):
    path = tmp_path / "ledger.json"
    src = str(Path(__file__).resolve().parents[2] / "src")
    env = {**os.environ, "PYTHONPATH": src}
    procs = [
        subprocess.Popen([sys.executable, "-c", _WORKER, str(path), "15"], env=env)
        for _ in range(4)
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    assert json.loads(path.read_text(encoding="utf-8"))["revision"] == 60
    assert not (tmp_path / "ledger.json.lock").exists()
