"""Hosted spend ledger on a private HF dataset (NFR-09, AC-51 hosted part; security findings 2-4).

Everything runs against the fake Hub in tests/fakes/hf_hub.py: no network, no real repo.
"""

import pytest
from conftest import FrozenClock
from fakes.hf_hub import FakeHfApi, http_error
from fakes.ledger import make_state
from huggingface_hub.errors import HfHubHTTPError

from pir.config import Settings
from pir.deps import build_ledger
from pir.models.ledger import CasCheck, LedgerState
from pir.spend import (
    CasProbeFailed,
    FileSpendLedger,
    HfDatasetSpendLedger,
    LedgerConflict,
    LedgerNotVerified,
    LedgerUnavailable,
    Refused,
    ScanBudgetLimits,
    SpendMeter,
)

REPO = "user/pir-spend-ledger"
TOKEN = "hf_testtoken_not_real"
HUB = "2.3.0"
LIMITS = ScanBudgetLimits(usd={"nebius": 0.75, "tavily": 0.40}, min_start_usd=0.05)


def _state(clock: FrozenClock, *, hub_version: str | None = HUB) -> LedgerState:
    state = make_state(
        clock.now(),
        {"hosted": {"nebius": 20.0, "tavily": 10.0}, "local": {"nebius": 5.0, "tavily": 2.0}},
    )
    if hub_version is None:
        return state
    return state.model_copy(
        update={"cas_verified": CasCheck(verified_at=clock.now(), hub_version=hub_version)}
    )


def _seed(api: FakeHfApi, state: LedgerState) -> None:
    api.external_commit("ledger.json", state.model_dump_json().encode("utf-8"))


def _ledger(
    api: FakeHfApi, clock: FrozenClock | None = None, hub: str = HUB
) -> HfDatasetSpendLedger:
    return HfDatasetSpendLedger(REPO, TOKEN, api=api, hub_version=hub, clock=clock)


def _meter(ledger, clock, spender="hosted") -> SpendMeter:
    async def no_sleep(_: float) -> None:
        return None

    return SpendMeter(ledger, spender, LIMITS, clock, sleep=no_sleep)


async def test_ac51_load_reads_the_file_at_the_head_commit(frozen_clock):
    api = FakeHfApi()
    state = _state(frozen_clock)
    _seed(api, state)
    loaded, revision = await _ledger(api).load()
    assert loaded == state
    assert revision == api.head
    read = [kw for name, kw in api.calls if name == "hf_hub_download"][0]
    assert read["revision"] == api.head and read["repo_type"] == "dataset"
    assert read["filename"] == "ledger.json" and read["token"] == TOKEN


async def test_nfr09_save_commits_on_the_loaded_revision_and_returns_the_new_head(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock))
    ledger = _ledger(api)
    state, revision = await ledger.load()
    changed = state.model_copy(update={"updated_at": frozen_clock.now().replace(minute=5)})
    new_revision = await ledger.save(changed, revision)
    assert new_revision == api.head != revision
    commit = [kw for name, kw in api.calls if name == "create_commit"][0]
    assert commit["parent_commit"] == revision and commit["repo_type"] == "dataset"
    assert commit["paths"] == ["ledger.json"] and commit["token"] == TOKEN
    assert (await ledger.load())[0] == changed


async def test_nfr09_save_on_a_stale_parent_is_a_conflict_and_changes_nothing(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock))
    ledger = _ledger(api)
    state, revision = await ledger.load()
    api.external_commit("other.txt", b"someone else wrote")
    head_before = api.head
    with pytest.raises(LedgerConflict):
        await ledger.save(state.model_copy(update={"updated_at": frozen_clock.now()}), revision)
    assert api.head == head_before and api.files["ledger.json"].startswith(b"{")


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
async def test_nfr09_other_commit_errors_are_unavailable_not_conflicts(frozen_clock, status):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock))
    ledger = _ledger(api)
    state, revision = await ledger.load()
    api.fail["create_commit"] = [http_error(HfHubHTTPError, status)]
    with pytest.raises(LedgerUnavailable) as caught:
        await ledger.save(state, revision)
    assert not isinstance(caught.value, LedgerConflict)
    assert TOKEN not in str(caught.value)


async def test_nfr09_network_failures_are_unavailable(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock))
    ledger = _ledger(api)
    api.fail["repo_info"] = [ConnectionError("boom")]
    with pytest.raises(LedgerUnavailable):
        await ledger.load()
    api.fail["hf_hub_download"] = [TimeoutError("slow")]
    with pytest.raises(LedgerUnavailable):
        await ledger.load()
    state, revision = await ledger.load()
    api.fail["create_commit"] = [OSError("reset")]
    with pytest.raises(LedgerUnavailable):
        await ledger.save(state, revision)


async def test_nfr09_a_corrupt_ledger_file_is_unavailable(frozen_clock):
    api = FakeHfApi()
    api.external_commit("ledger.json", b'{"schema_version": 2, "oops": true}')
    with pytest.raises(LedgerUnavailable, match="not valid"):
        await _ledger(api).load()


async def test_ac51_a_missing_ledger_file_refuses_all_spending(frozen_clock):
    api = FakeHfApi()  # no ledger.json yet
    ledger = _ledger(api)
    with pytest.raises(LedgerNotVerified):
        await ledger.load()
    state, head = await ledger.read_head()  # set-caps can still see the empty ledger
    assert state is None and head == api.head


async def test_ac51_without_cas_verified_the_ledger_refuses_every_spend(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock, hub_version=None))
    ledger = _ledger(api)
    with pytest.raises(LedgerNotVerified, match="set-caps"):
        await ledger.load()
    meter = _meter(ledger, frozen_clock)
    refused = await meter.reserve("scan-1")
    assert isinstance(refused, Refused) and refused.reason == "ledger_unavailable"
    assert meter.status() == "paused"  # live scans paused
    assert api.count("create_commit") == 0  # nothing was spent or written


async def test_ac51_a_different_hub_version_refuses_every_spend(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock, hub_version="2.2.0"))
    ledger = _ledger(api, hub="2.3.0")
    with pytest.raises(LedgerNotVerified, match="2.2.0"):
        await ledger.load()
    meter = _meter(ledger, frozen_clock)
    refused = await meter.reserve("scan-1")
    assert isinstance(refused, Refused) and refused.reason == "ledger_unavailable"
    assert meter.status() == "paused"


async def test_ac51_save_refuses_an_unverified_state_too(frozen_clock):
    api = FakeHfApi()
    ledger = _ledger(api)
    with pytest.raises(LedgerNotVerified):
        await ledger.save(_state(frozen_clock, hub_version=None), api.head)
    with pytest.raises(LedgerNotVerified):
        await ledger.save(_state(frozen_clock, hub_version="9.9.9"), api.head)
    assert api.count("create_commit") == 0


async def test_ac51_a_verified_ledger_serves_reserve_and_settle(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock))
    meter = _meter(_ledger(api), frozen_clock)
    budget = await meter.reserve("scan-1")
    assert not isinstance(budget, Refused)
    persisted, _ = await _ledger(api).load()
    reserved = persisted.spenders["hosted"].pools["nebius"].reserved_usd
    assert 0 < reserved <= 0.75  # cut by the daily ceiling, never above the per-scan budget
    assert reserved == pytest.approx(budget.reserved_usd["nebius"])
    await meter.settle(budget)
    persisted, _ = await _ledger(api).load()
    assert persisted.reservations == []
    assert persisted.cas_verified is not None  # the check survives every save


async def test_ac51_a_stale_parent_save_is_reapplied_by_the_meter(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock))
    api.before_commit = lambda a: a.external_commit("other.txt", b"x")  # a racing writer
    meter = _meter(_ledger(api), frozen_clock)
    budget = await meter.reserve("scan-1")
    assert not isinstance(budget, Refused)
    assert api.count("create_commit") == 2  # first lost the race, second re-applied on the new head
    persisted, _ = await _ledger(api).load()
    assert [r.scan_id for r in persisted.reservations] == ["scan-1"]
    assert api.files["other.txt"] == b"x"  # the other writer's commit is kept


async def test_ac51_three_stale_parent_saves_fail_closed(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock))
    api.fail["create_commit"] = [http_error(HfHubHTTPError, 412) for _ in range(3)]
    meter = _meter(_ledger(api), frozen_clock)
    refused = await meter.reserve("scan-1")
    assert isinstance(refused, Refused) and refused.reason == "ledger_unavailable"
    assert meter.status() == "paused"
    assert api.count("create_commit") == 3
    persisted, _ = await _ledger(api).load()
    assert persisted.reservations == []


async def test_ac51_two_stale_parent_saves_then_success(frozen_clock):
    api = FakeHfApi()
    _seed(api, _state(frozen_clock))
    api.fail["create_commit"] = [http_error(HfHubHTTPError, 409) for _ in range(2)]
    budget = await _meter(_ledger(api), frozen_clock).reserve("scan-1")
    assert not isinstance(budget, Refused)
    assert api.count("create_commit") == 3


async def test_nfr09_probe_passes_when_the_hub_rejects_the_stale_parent(frozen_clock):
    api = FakeHfApi()
    start = api.head
    await _ledger(api, frozen_clock).probe_stale_parent()
    probe_commits = [c for c in api.history if "cas_probe.json" in c[1]]
    assert len(probe_commits) == 1  # only the first probe commit landed
    assert api.head != start and "ledger.json" not in api.files
    parents = [kw["parent_commit"] for name, kw in api.calls if name == "create_commit"]
    assert parents == [start, start]  # both on the same parent H


async def test_nfr09_probe_fails_when_the_stale_commit_succeeds(frozen_clock):
    api = FakeHfApi(enforce_parent=False)  # a Hub without compare-and-swap
    with pytest.raises(CasProbeFailed, match="accepted a commit on a stale parent"):
        await _ledger(api, frozen_clock).probe_stale_parent()
    # The probe files differ, so the second commit was not skipped as an empty commit.
    assert len([c for c in api.history if "cas_probe.json" in c[1]]) == 2


async def test_nfr09_probe_fails_on_other_errors(frozen_clock):
    api = FakeHfApi()
    ledger = _ledger(api, frozen_clock)
    api.fail["create_commit"] = [http_error(HfHubHTTPError, 500)]
    with pytest.raises(CasProbeFailed, match="first probe commit"):
        await ledger.probe_stale_parent()
    original = api.create_commit
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise http_error(HfHubHTTPError, 500)
        return original(*args, **kwargs)

    api.create_commit = flaky  # type: ignore[method-assign]
    with pytest.raises(CasProbeFailed, match="otherwise"):
        await ledger.probe_stale_parent()


async def test_nfr09_probe_fails_when_the_head_cannot_be_read(frozen_clock):
    api = FakeHfApi()
    api.fail["repo_info"] = [ConnectionError("offline")]
    with pytest.raises(LedgerUnavailable):
        await _ledger(api, frozen_clock).probe_stale_parent()


def test_nfr09_constructor_rejects_bad_values():
    with pytest.raises(ValueError):
        HfDatasetSpendLedger("no-slash", TOKEN, api=FakeHfApi(), hub_version=HUB)
    with pytest.raises(ValueError):
        HfDatasetSpendLedger(REPO, "", api=FakeHfApi(), hub_version=HUB)


def test_nfr09_from_settings_needs_token_and_repo():
    with pytest.raises(LedgerUnavailable):
        HfDatasetSpendLedger.from_settings(
            Settings(_env_file=None, spend_ledger_backend="hf_dataset")
        )
    ok = Settings(
        _env_file=None,
        spend_ledger_backend="hf_dataset",
        spend_ledger_hf_repo=REPO,
        hf_token=TOKEN,
    )
    ledger = HfDatasetSpendLedger.from_settings(ok, api=FakeHfApi())
    assert ledger.hub_version  # the installed huggingface_hub version


def test_nfr09_settings_refuse_the_file_backend_while_the_hf_repo_is_set():
    with pytest.raises(ValueError, match="SPEND_LEDGER_BACKEND=file is refused"):
        Settings(_env_file=None, spend_ledger_backend="file", spend_ledger_hf_repo=REPO)


def test_nfr09_build_ledger_picks_the_backend_from_settings(tmp_path):
    file_settings = Settings(_env_file=None, spend_ledger_path=str(tmp_path / "l.json"))
    assert isinstance(build_ledger(file_settings), FileSpendLedger)
    hf_settings = Settings(
        _env_file=None,
        spend_ledger_backend="hf_dataset",
        spend_ledger_hf_repo=REPO,
        hf_token=TOKEN,
    )
    assert isinstance(build_ledger(hf_settings), HfDatasetSpendLedger)


def test_nfr09_build_ledger_names_the_missing_hf_variable():
    from pir.config import ConfigError

    with pytest.raises(ConfigError, match="HF_TOKEN"):
        build_ledger(Settings(_env_file=None, spend_ledger_backend="hf_dataset"))
    with pytest.raises(ConfigError, match="SPEND_LEDGER_HF_REPO"):
        build_ledger(Settings(_env_file=None, spend_ledger_backend="hf_dataset", hf_token=TOKEN))
