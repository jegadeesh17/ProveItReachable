"""AC-31 / NFR-03: Settings validation, require(), secret hygiene, check-config."""

import pytest
from pydantic import ValidationError

from pir import cli
from pir.config import ConfigError, Settings, require, required_for

SENTINEL = "sk-SENTINEL-do-not-leak-0123456789"
DIGEST = "sha256:" + "a" * 64

# Every variable of the ARCHITECTURE table that the tests must not inherit from the host.
_VARS = [name.upper() for name in Settings.model_fields]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def make(**env) -> Settings:
    return Settings(_env_file=None, **env)


def test_defaults_match_the_variable_table():
    s = make()
    assert s.nebius_model == "nvidia/nemotron-3-super-120b-a12b"
    assert (s.nebius_price_input_per_mtok, s.nebius_price_output_per_mtok) == (0.60, 2.40)
    assert s.executor == "sandbox"
    assert s.sandbox_base_image == (
        "ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim"
        "@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca"
    )
    assert (s.sandbox_usd_per_second, s.sandbox_max_concurrent_ops) == (0.001, 4)
    assert (s.tavily_search_depth, s.tavily_max_results) == ("basic", 5)
    assert s.langsmith_tracing is False
    assert s.langsmith_dataset == "proveitreachable-eval-v1"
    assert (s.max_repo_mb, s.max_repo_files, s.max_py_file_bytes) == (50, 5000, 1_000_000)
    assert (s.spend_cap_nebius_usd, s.spend_cap_tavily_usd) == (10, 5)
    assert s.spend_end_date.isoformat() == "2026-12-15"
    assert s.spend_ledger_backend == "file"
    assert s.live_scans_enabled is True
    assert s.feature_proof is False
    assert (s.port, s.log_level) == (8000, "INFO")


def test_environment_overrides_and_empty_values_use_defaults(monkeypatch):
    monkeypatch.setenv("MAX_REPO_MB", "7")
    monkeypatch.setenv("NEBIUS_MODEL", "")
    s = make()
    assert s.max_repo_mb == 7
    assert s.nebius_model == "nvidia/nemotron-3-super-120b-a12b"


def test_ac31_model_without_nemotron_is_rejected():
    with pytest.raises(ValidationError, match="NEBIUS_MODEL"):
        make(nebius_model="meta/llama-3")


def test_ac31_model_check_is_case_insensitive():
    assert make(nebius_model="NVIDIA/Nemotron-X").nebius_model == "NVIDIA/Nemotron-X"


def test_ac31_tag_only_sandbox_image_is_rejected():
    with pytest.raises(ValidationError, match="SANDBOX_BASE_IMAGE"):
        make(sandbox_base_image="ghcr.io/astral-sh/uv:python3.13-bookworm-slim")


def test_ac31_short_digest_is_rejected():
    with pytest.raises(ValidationError, match="SANDBOX_BASE_IMAGE"):
        make(sandbox_base_image="ghcr.io/astral-sh/uv@sha256:abc123")


def test_ac31_digest_pinned_sandbox_image_is_accepted():
    ref = f"ghcr.io/astral-sh/uv:python3.13-bookworm-slim@{DIGEST}"
    assert make(sandbox_base_image=ref).sandbox_base_image == ref


def test_tag_only_image_is_allowed_for_the_fake_executor():
    assert make(executor="fake", sandbox_base_image="uv:tag").executor == "fake"


def test_ac31_file_ledger_refused_while_hf_repo_is_set():
    with pytest.raises(ValidationError, match="SPEND_LEDGER_HF_REPO"):
        make(spend_ledger_backend="file", spend_ledger_hf_repo="me/pir-spend-ledger")


def test_hf_ledger_with_repo_is_accepted():
    s = make(spend_ledger_backend="hf_dataset", spend_ledger_hf_repo="me/pir-spend-ledger")
    assert s.spend_ledger_backend == "hf_dataset"


def test_invalid_log_level_and_executor_are_rejected():
    with pytest.raises(ValidationError, match="log_level|LOG_LEVEL"):
        make(log_level="LOUD")
    with pytest.raises(ValidationError):
        make(executor="docker")


def test_nfr03_repr_and_str_never_contain_a_secret():
    s = make(
        nebius_api_key=SENTINEL,
        tavily_api_key=SENTINEL + "-t",
        langsmith_api_key=SENTINEL + "-l",
        github_token=SENTINEL + "-g",
        hf_token=SENTINEL + "-h",
    )
    for text in (repr(s), str(s), s.model_dump_json()):
        assert "SENTINEL" not in text
    assert s.nebius_api_key.get_secret_value() == SENTINEL


def test_ac31_require_names_only_the_missing_variable():
    s = make(nebius_api_key=SENTINEL, nebius_project_id="proj")
    require(s, ["NEBIUS_API_KEY", "NEBIUS_PROJECT_ID"])
    with pytest.raises(ConfigError) as info:
        require(s, ["NEBIUS_API_KEY", "TAVILY_API_KEY", "HF_TOKEN"])
    assert str(info.value) == "Missing required environment variable: TAVILY_API_KEY"
    assert info.value.names == ("TAVILY_API_KEY", "HF_TOKEN")
    assert SENTINEL not in str(info.value)


def test_require_treats_an_empty_secret_as_missing():
    s = make(nebius_api_key="")
    with pytest.raises(ConfigError, match="NEBIUS_API_KEY"):
        require(s, ["NEBIUS_API_KEY"])


def test_required_sets_follow_the_architecture():
    base = ["NEBIUS_API_KEY", "NEBIUS_PROJECT_ID", "TAVILY_API_KEY"]
    assert required_for(make(), "scan") == base
    assert required_for(make(), "eval") == base + ["LANGSMITH_API_KEY"]
    assert required_for(make(live_scans_enabled=False), "serve") == []
    hf = make(spend_ledger_backend="hf_dataset", spend_ledger_hf_repo="me/r")
    assert required_for(hf, "scan")[-2:] == ["HF_TOKEN", "SPEND_LEDGER_HF_REPO"]
    with pytest.raises(ValueError):
        required_for(make(), "record")


def test_check_config_names_missing_variables_and_never_prints_a_value(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("NEBIUS_API_KEY", SENTINEL)
    assert cli.main(["check-config", "--for", "scan"]) == 1
    out = capsys.readouterr().out
    assert "NEBIUS_PROJECT_ID" in out and "TAVILY_API_KEY" in out
    assert "NEBIUS_API_KEY" not in out
    assert SENTINEL not in out


def test_check_config_succeeds_when_complete(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEBIUS_API_KEY", SENTINEL)
    monkeypatch.setenv("NEBIUS_PROJECT_ID", "proj")
    monkeypatch.setenv("TAVILY_API_KEY", SENTINEL + "-t")
    assert cli.main(["check-config"]) == 0
    assert SENTINEL not in capsys.readouterr().out


def test_check_config_names_an_invalid_variable_without_its_value(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NEBIUS_MODEL", "secret-looking-llama")
    assert cli.main(["check-config"]) == 1
    out = capsys.readouterr().out
    assert "NEBIUS_MODEL" in out
    assert "secret-looking-llama" not in out


def test_cli_without_a_command_exits_2(capsys):
    assert cli.main([]) == 2
    assert "check-config" in capsys.readouterr().err


def test_cli_rejects_an_unknown_target():
    with pytest.raises(SystemExit) as info:
        cli.main(["check-config", "--for", "bogus"])
    assert info.value.code == 2
