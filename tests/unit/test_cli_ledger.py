"""`pir ledger set-caps` and the stale-parent gate (NFR-09, AC-51; security round 2 finding 3).

Runs the real CLI entry point against the fake Hub: no network, no real repo.
"""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fakes.hf_hub import FakeHfApi, http_error
from fakes.ledger import make_state
from huggingface_hub.errors import HfHubHTTPError

from pir import cli
from pir.config import Settings
from pir.logs import clear_secrets
from pir.models.ledger import LedgerState
from pir.spend import (
    HEADROOM_MIN_USD,
    CapsRefused,
    HfDatasetSpendLedger,
    Refused,
    ScanBudgetLimits,
    SpendMeter,
    SystemClock,
    plan_set_caps,
)

REPO = "user/pir-spend-ledger"
HUB = "2.3.0"
TOKEN = "hf_test_token_not_real_0123"
NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
BALANCES = ["--nebius-balance", "30", "--tavily-balance", "15"]


@pytest.fixture(autouse=True)
def _no_leaked_secrets():
    """`configure_logging` registers the token for redaction; do not leak it into other tests."""
    clear_secrets()
    yield
    clear_secrets()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> FakeHfApi:
    api = FakeHfApi()
    settings = Settings(
        _env_file=None, spend_ledger_backend="hf_dataset", spend_ledger_hf_repo=REPO, hf_token=TOKEN
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "_hf_ledger",
        lambda _settings: HfDatasetSpendLedger(REPO, TOKEN, api=api, hub_version=HUB),
    )
    return api


def _written(api: FakeHfApi) -> LedgerState:
    return LedgerState.model_validate_json(api.files["ledger.json"])


def _seed(api: FakeHfApi, state: LedgerState) -> None:
    api.external_commit("ledger.json", state.model_dump_json().encode("utf-8"))


def test_nfr09_set_caps_writes_default_caps_and_the_local_allowance(hub, capsys):
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 0
    state = _written(hub)
    assert state.caps_usd == {"nebius": 25.0, "tavily": 12.0}  # balance - headroom 5 / 3
    assert state.spenders["local"].allowance_usd == {"nebius": 5.0, "tavily": 2.0}
    assert state.spenders["hosted"].allowance_usd == {"nebius": 20.0, "tavily": 10.0}
    assert state.cas_verified is not None and state.cas_verified.hub_version == HUB
    assert "ledger.json" in hub.files and "cas_probe.json" in hub.files
    err = capsys.readouterr().err
    assert "hosted" in err and "local" in err


def test_nfr09_set_caps_runs_the_stale_parent_check_before_writing_caps(hub):
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 0
    paths = [kw["paths"] for name, kw in hub.calls if name == "create_commit"]
    assert paths == [["cas_probe.json"], ["cas_probe.json"], ["ledger.json"]]
    parents = [kw["parent_commit"] for name, kw in hub.calls if name == "create_commit"]
    assert parents[0] == parents[1] != parents[2]  # the ledger goes on the head after the probe
    assert parents[2] == hub.history[-2][0]


def test_nfr09_set_caps_exits_1_and_writes_nothing_when_the_stale_commit_succeeds(hub, capsys):
    hub.enforce_parent = False  # a Hub that would let two writers overwrite each other
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 1
    assert "ledger.json" not in hub.files
    assert [kw["paths"] for name, kw in hub.calls if name == "create_commit"] == [
        ["cas_probe.json"],
        ["cas_probe.json"],
    ]
    assert "nothing was written" in capsys.readouterr().err


def test_nfr09_set_caps_keeps_recorded_spend_and_adds_the_balance_to_it(hub):
    seeded = make_state(
        NOW,
        {"hosted": {"nebius": 20.0, "tavily": 10.0}, "local": {"nebius": 5.0, "tavily": 2.0}},
        spent={"hosted": {"nebius": 4.0, "tavily": 1.0}, "local": {"nebius": 1.5, "tavily": 0.5}},
    )
    _seed(hub, seeded)
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 0
    state = _written(hub)
    # maximum = recorded spend + balance - headroom
    assert state.caps_usd == {
        "nebius": pytest.approx(5.5 + 25.0),
        "tavily": pytest.approx(1.5 + 12.0),
    }
    assert state.spenders["hosted"].pools["nebius"].spent_usd == 4.0  # spend is kept
    assert state.spenders["local"].pools["tavily"].spent_usd == 0.5
    assert state.spenders["local"].allowance_usd == {"nebius": 5.0, "tavily": 2.0}
    assert state.spenders["hosted"].allowance_usd["nebius"] == pytest.approx(30.5 - 5.0)


def test_nfr09_set_caps_writes_hosted_as_cap_minus_local_for_an_explicit_cap(hub):
    args = ["ledger", "set-caps", *BALANCES, "--nebius", "20", "--tavily", "8"]
    assert cli.main([*args, "--local-nebius", "4", "--local-tavily", "1"]) == 0
    state = _written(hub)
    assert state.caps_usd == {"nebius": 20.0, "tavily": 8.0}
    assert state.spenders["hosted"].allowance_usd == {"nebius": 16.0, "tavily": 7.0}
    assert state.spenders["local"].allowance_usd == {"nebius": 4.0, "tavily": 1.0}


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        (["--nebius", "25.01"], "above the maximum"),
        (["--tavily", "12.5"], "above the maximum"),
        (["--headroom-nebius", "4.99"], "below the minimum"),
        (["--headroom-tavily", "2.99"], "below the minimum"),
        (["--nebius", "-1"], "below recorded spend"),
        (["--local-nebius", "-1"], "finite number, 0 or more"),
        (["--local-tavily", "13"], "above the cap"),
    ],
)
def test_nfr09_set_caps_refuses_bad_values_before_touching_the_hub(hub, capsys, extra, needle):
    head = hub.head
    assert cli.main(["ledger", "set-caps", *BALANCES, *extra]) == 2
    assert needle in capsys.readouterr().err
    assert hub.head == head and hub.count("create_commit") == 0  # not even a probe commit


def test_nfr09_set_caps_refuses_a_cap_below_recorded_spend(hub, capsys):
    seeded = make_state(
        NOW,
        {"hosted": {"nebius": 20.0, "tavily": 10.0}, "local": {"nebius": 5.0, "tavily": 2.0}},
        spent={"hosted": {"nebius": 9.0}},
    )
    _seed(hub, seeded)
    assert cli.main(["ledger", "set-caps", *BALANCES, "--nebius", "8"]) == 2
    assert "below recorded spend" in capsys.readouterr().err
    assert json.loads(hub.files["ledger.json"])["caps_usd"]["nebius"] == 25.0  # unchanged


def test_nfr09_set_caps_accepts_a_larger_headroom_and_lowers_the_maximum(hub):
    assert cli.main(["ledger", "set-caps", *BALANCES, "--headroom-nebius", "10"]) == 0
    assert _written(hub).caps_usd["nebius"] == 20.0


def test_nfr09_set_caps_refuses_a_balance_that_does_not_cover_the_headroom(hub, capsys):
    args = ["ledger", "set-caps", "--nebius-balance", "4", "--tavily-balance", "15"]
    assert cli.main(args) == 2
    assert "does not cover" in capsys.readouterr().err


def test_nfr09_set_caps_exits_1_when_the_hub_is_unavailable(hub, capsys):
    hub.fail["repo_info"] = [http_error(HfHubHTTPError, 503)]
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 1
    assert "unavailable" in capsys.readouterr().err
    assert "ledger.json" not in hub.files


def test_nfr09_set_caps_exits_1_when_the_ledger_commit_loses_a_race(hub, capsys):
    original = hub.create_commit

    def racing(*args, **kwargs):
        ops = args[1]
        if ops[0].path_in_repo == "ledger.json":
            hub.external_commit("other.txt", b"x")  # someone else commits just before
        return original(*args, **kwargs)

    hub.create_commit = racing  # type: ignore[method-assign]
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 1
    assert "ledger.json" not in hub.files
    assert "Run it again" in capsys.readouterr().err


@pytest.mark.parametrize("missing", [["--nebius-balance"], ["--tavily-balance"]])
def test_nfr09_set_caps_requires_both_balances(hub, missing):
    args = ["ledger", "set-caps", "--nebius-balance", "30", "--tavily-balance", "15"]
    index = args.index(missing[0])
    with pytest.raises(SystemExit) as caught:
        cli.main(args[:index] + args[index + 2 :])
    assert caught.value.code == 2


def test_nfr09_set_caps_needs_the_hf_backend(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None))
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 1
    assert "SPEND_LEDGER_BACKEND=hf_dataset" in capsys.readouterr().err


def test_nfr09_set_caps_names_the_missing_variable(monkeypatch, capsys):
    settings = Settings(_env_file=None, spend_ledger_backend="hf_dataset")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 1
    assert "HF_TOKEN" in capsys.readouterr().err


def test_nfr09_ledger_without_a_subcommand_prints_help(capsys):
    assert cli.main(["ledger"]) == 2


def test_ac51_after_set_caps_the_hosted_meter_can_reserve(hub):
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 0
    ledger = HfDatasetSpendLedger(REPO, TOKEN, api=hub, hub_version=HUB)
    limits = ScanBudgetLimits(usd={"nebius": 0.75, "tavily": 0.40}, min_start_usd=0.05)
    hosted = SpendMeter(ledger, "hosted", limits, SystemClock())
    local = SpendMeter(ledger, "local", limits, SystemClock())

    async def run() -> None:
        assert not isinstance(await hosted.reserve("scan-h"), Refused)
        assert not isinstance(await local.reserve("scan-l"), Refused)
        await hosted.refresh()
        assert hosted.status() == "enabled"

    asyncio.run(run())


def test_ac51_a_hub_upgrade_after_set_caps_pauses_spending_until_it_is_rerun(hub):
    assert cli.main(["ledger", "set-caps", *BALANCES]) == 0
    upgraded = HfDatasetSpendLedger(REPO, TOKEN, api=hub, hub_version="2.4.0")
    limits = ScanBudgetLimits(usd={"nebius": 0.75, "tavily": 0.40})
    meter = SpendMeter(upgraded, "hosted", limits, SystemClock())
    refused = asyncio.run(meter.reserve("scan-1"))
    assert isinstance(refused, Refused) and refused.reason == "ledger_unavailable"


def test_nfr09_headroom_minimums_are_5_and_3_dollars():
    assert HEADROOM_MIN_USD == {"nebius": 5.0, "tavily": 3.0}


def test_nfr09_plan_set_caps_rejects_non_finite_numbers():
    base = {
        "headrooms": {"nebius": 5.0, "tavily": 3.0},
        "caps": {"nebius": None, "tavily": None},
        "local_allowance": {"nebius": 5.0, "tavily": 2.0},
        "now": NOW,
        "hub_version": HUB,
    }
    with pytest.raises(CapsRefused):
        plan_set_caps(None, balances={"nebius": float("nan"), "tavily": 15.0}, **base)
    with pytest.raises(CapsRefused):
        plan_set_caps(None, balances={"nebius": float("inf"), "tavily": 15.0}, **base)
    state = plan_set_caps(None, balances={"nebius": 30.0, "tavily": 15.0}, **base)
    assert state.caps_usd == {"nebius": 25.0, "tavily": 12.0} and state.reservations == []
