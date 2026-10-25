"""Live gate for the hosted ledger (ARCHITECTURE 22.1; NFR-09). Run with PIR_LIVE_TESTS=1.

GATE, not fail-safe: the whole spend cap depends on the Hub rejecting a commit on a stale parent.
If this fails, the hosted ledger stays off, `ledger set-caps` writes nothing and live scans stay
paused until an ADR resolves it.

It commits two small files named `cas_probe.json` to the REAL private ledger dataset named by
SPEND_LEDGER_HF_REPO, using HF_TOKEN (both from Settings). It never touches `ledger.json`.
"""

import secrets

import pytest
from huggingface_hub import CommitOperationAdd, HfApi
from huggingface_hub.errors import HfHubHTTPError

from pir.config import Settings
from pir.spend import STALE_PARENT_STATUSES, HfDatasetSpendLedger

pytestmark = [pytest.mark.slow, pytest.mark.live]


def _settings() -> Settings:
    settings = Settings()
    if settings.hf_token is None or not settings.spend_ledger_hf_repo:
        pytest.fail("HF_TOKEN and SPEND_LEDGER_HF_REPO must be set for the live ledger gate")
    return settings


def test_hf_ledger_rejects_stale_parent():
    settings = _settings()
    assert settings.hf_token is not None and settings.spend_ledger_hf_repo
    token = settings.hf_token.get_secret_value()
    repo = settings.spend_ledger_hf_repo
    api = HfApi()
    info = api.repo_info(repo, repo_type="dataset", token=token)
    assert info.private, "the ledger dataset must be private"
    head = info.sha

    def probe() -> None:
        api.create_commit(
            repo,
            [
                CommitOperationAdd(
                    path_in_repo="cas_probe.json",
                    path_or_fileobj=f'{{"probe": "{secrets.token_hex(8)}"}}'.encode(),
                )
            ],
            commit_message="stale-parent probe (live test)",
            repo_type="dataset",
            parent_commit=head,
            token=token,
        )

    probe()  # moves the head past `head`
    with pytest.raises(HfHubHTTPError) as caught:
        probe()  # a commit on the now-stale parent must be rejected
    assert caught.value.response.status_code in STALE_PARENT_STATUSES, (
        f"the Hub rejected the stale parent with HTTP {caught.value.response.status_code}, "
        f"which the ledger does not treat as a conflict: {sorted(STALE_PARENT_STATUSES)}"
    )


async def test_hf_ledger_probe_stale_parent_passes_on_the_real_hub():
    settings = _settings()
    ledger = HfDatasetSpendLedger.from_settings(settings)
    await ledger.probe_stale_parent()  # raises CasProbeFailed if the Hub accepts a stale parent
