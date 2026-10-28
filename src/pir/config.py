"""Settings loaded from environment variables and the local env file (ARCHITECTURE config table).

Secrets are `SecretStr`, so `repr`/`str` of `Settings` never shows a value. An invalid value
stops startup and the message names the variable, never its value.
"""

import re
from datetime import date
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DIGEST_REF = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """A required configuration value is missing or invalid. Never carries a value."""

    def __init__(self, message: str, names: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.names = names


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
        case_sensitive=False,
    )

    # Nebius Token Factory
    nebius_api_key: SecretStr | None = None
    nebius_project_id: str | None = None
    nebius_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nebius_price_input_per_mtok: float = 0.60
    nebius_price_output_per_mtok: float = 2.40

    # Executor and Sandbox
    executor: Literal["sandbox", "fake"] = "sandbox"
    # Version tag (imported) and index digest (checked through the fingerprint file, ADR-0033);
    # Python 3.13.11, uv 0.9.30. Digest read from the registry on 2026-10-25.
    sandbox_base_image: str | None = (
        "ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim"
        "@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca"
    )
    sandbox_usd_per_second: float = 0.001
    sandbox_max_concurrent_ops: int = 4

    # Tavily
    tavily_api_key: SecretStr | None = None
    tavily_search_depth: Literal["basic", "advanced"] = "basic"
    tavily_max_results: int = 5
    tavily_price_per_credit_usd: float = 0.008

    # LangSmith
    langsmith_api_key: SecretStr | None = None
    langsmith_tracing: bool = False
    langsmith_project: str = "ProveItReachable"
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_dataset: str = "proveitreachable-eval-v1"

    # GitHub
    github_token: SecretStr | None = None

    # Repo intake and resolution
    max_repo_mb: int = 50
    max_repo_files: int = 5000
    max_py_file_bytes: int = 1_000_000
    resolve_python_version: str = "3.12"

    # Reasoning bounds
    max_findings_reasoned: int = 20
    finding_concurrency: int = 4
    max_tool_rounds: int = 8
    max_model_retries: int = 2
    max_completion_tokens: int = 1500
    llm_hold_completion_tokens: int = 16000

    # Timeouts (seconds)
    timeout_github_s: float = 10
    timeout_osv_s: float = 15
    timeout_tavily_s: float = 20
    timeout_nebius_s: float = 60
    scan_timeout_s: float = 900
    timeout_sandbox_fetch_s: float = 120
    timeout_sandbox_resolve_s: float = 60
    timeout_sandbox_compile_s: float = 180
    timeout_sandbox_index_s: float = 120
    timeout_proof_install_s: float = 300
    timeout_proof_build_s: float = 300
    timeout_proof_run_s: float = 60

    # Spend
    spend_cap_nebius_usd: float = 10
    spend_cap_tavily_usd: float = 5
    spend_end_date: date = date(2026, 12, 15)
    scan_budget_nebius_usd: float = 0.75
    scan_budget_tavily_usd: float = 0.40
    scan_min_start_usd: float = 0.05
    spend_ledger_backend: Literal["file", "hf_dataset"] = "file"
    spend_ledger_path: str = "./data/spend_ledger.json"
    spend_ledger_hf_repo: str | None = None
    hf_token: SecretStr | None = None

    # Serving and limits
    live_scans_enabled: bool = True
    trusted_proxy_hops: int = 0
    rate_limit_per_client_hour: int = 3
    rate_limit_per_client_day: int = 10
    rate_limit_global_day: int = 10
    max_concurrent_scans: int = 2
    max_concurrent_replays: int = 10
    max_sse_streams: int = 40
    rate_limit_replays_per_client_hour: int = 30

    # Feature switches
    feature_progress_feed: bool = True
    feature_markdown_export: bool = True
    feature_proof: bool = False

    # Data and replays
    replay_dir: str = "./demo/replays"
    replay_max_seconds: float = 20
    data_dir: str = "./data"
    job_ttl_s: int = 3600
    max_jobs: int = 200

    # Process
    port: int = 8000
    log_level: str = "INFO"
    pir_live_tests: str | None = None

    @field_validator("nebius_model")
    @classmethod
    def _model_is_nemotron(cls, value: str) -> str:
        if "nemotron" not in value.lower():
            raise ValueError("NEBIUS_MODEL must contain 'nemotron'")
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level_known(cls, value: str) -> str:
        upper = value.upper()
        if upper not in _LOG_LEVELS:
            raise ValueError("LOG_LEVEL must be one of " + ", ".join(_LOG_LEVELS))
        return upper

    @model_validator(mode="after")
    def _cross_field_rules(self) -> "Settings":
        image = self.sandbox_base_image
        if self.executor == "sandbox" and image is not None and not _DIGEST_REF.match(image):
            raise ValueError("SANDBOX_BASE_IMAGE must end in @sha256:<64 hex digits>")
        if self.spend_ledger_backend == "file" and self.spend_ledger_hf_repo:
            raise ValueError(
                "SPEND_LEDGER_BACKEND=file is refused while SPEND_LEDGER_HF_REPO is set"
            )
        return self


def load_settings() -> Settings:
    """Build Settings from the environment and the local env file."""
    return Settings()


def _is_set(settings: Settings, name: str) -> bool:
    value = getattr(settings, name.lower())
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return value is not None and value != ""


def missing_names(settings: Settings, names: list[str] | tuple[str, ...]) -> list[str]:
    """Names (upper case, as in the env file) from `names` that have no value."""
    return [name for name in names if not _is_set(settings, name)]


def require(settings: Settings, names: list[str] | tuple[str, ...]) -> None:
    """Raise ConfigError naming the first missing variable (all of them in `.names`)."""
    missing = missing_names(settings, names)
    if missing:
        raise ConfigError(
            f"Missing required environment variable: {missing[0]}", names=tuple(missing)
        )


def required_for(settings: Settings, command: str) -> list[str]:
    """Variables the `scan`, `eval` or `serve` command needs (ARCHITECTURE required sets)."""
    if command not in ("scan", "eval", "serve"):
        raise ValueError(f"Unknown command: {command}")
    if command == "serve" and not settings.live_scans_enabled:
        return []
    names = ["NEBIUS_API_KEY", "NEBIUS_PROJECT_ID", "TAVILY_API_KEY"]
    if command == "eval":
        names.append("LANGSMITH_API_KEY")
    if settings.spend_ledger_backend == "hf_dataset":
        names += ["HF_TOKEN", "SPEND_LEDGER_HF_REPO"]
    return names
