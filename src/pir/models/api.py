"""API request, response and event models (ARCHITECTURE section 6.5)."""

import re
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from pir.models import Mode, Outcome, Pool, StepName, StepState, Strict, ToolName, Verdict
from pir.models.report import StepStatus

_REF_RE = re.compile(r"[A-Za-z0-9._/-]{1,100}")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_SLUG_RE = re.compile(r"[a-z0-9-]{1,40}")


def check_ref(value: str) -> str:
    """A git ref as submitted: no I/O, never normalised (finding 11)."""
    if not _REF_RE.fullmatch(value):
        raise ValueError("ref has unexpected characters or length")
    if any(not seg or seg.startswith(".") for seg in value.split("/")):
        raise ValueError("ref segments must be non-empty and not start with '.'")
    if _HEX_RE.fullmatch(value) and len(value) > 40:
        raise ValueError("an all-hex ref over 40 characters is not accepted")
    return value


class ScanRequest(Strict):
    repo_url: str = Field(min_length=1, max_length=300)
    ref: str | None = None

    @field_validator("ref")
    @classmethod
    def _ref(cls, value: str | None) -> str | None:
        return None if value is None else check_ref(value)


class ScanAccepted(Strict):
    scan_id: str
    mode: Mode
    status_url: str
    events_url: str
    report_url: str


class ScanStatusResponse(Strict):
    scan_id: str
    mode: Mode
    state: Literal["queued", "running", "completed", "failed"]
    steps: list[StepStatus]
    outcome: Outcome | None
    created_at: datetime
    finished_at: datetime | None


ErrorCode = Literal[
    "invalid_url",
    "repo_not_found",
    "repo_too_large",
    "not_python_project",
    "rate_limited",
    "server_busy",
    "too_many_requests",
    "live_scans_paused",
    "github_unavailable",
    "scan_not_found",
    "report_not_ready",
    "demo_not_found",
    "finding_not_found",
    "request_invalid",
    "internal_error",
]


class ErrorBody(Strict):
    code: ErrorCode
    message: str
    details: dict[str, str | int | float] = Field(default_factory=dict)
    retry_after_s: int | None = Field(default=None, ge=0)


class ErrorResponse(Strict):
    error: ErrorBody


class ProgressEvent(Strict):
    seq: int = Field(ge=1)
    scan_id: str
    type: Literal["step", "done", "error"]
    step: StepName | None
    state: StepState | None
    message: str | None = Field(max_length=300)
    at: datetime
    outcome: Outcome | None
    tools: list[ToolName]

    @model_validator(mode="after")
    def _check(self) -> "ProgressEvent":
        if self.type == "step" and (self.step is None or self.state is None):
            raise ValueError("a step event needs step and state")
        if (self.type == "done") != (self.outcome is not None):
            raise ValueError("outcome is set iff type == 'done'")
        return self


class DemoSummary(Strict):
    slug: str
    title: str
    description: str
    repo_url: str
    commit_sha: str
    recorded_at: datetime
    by_verdict: dict[Verdict, int]

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG_RE.fullmatch(value):
            raise ValueError("slug must match ^[a-z0-9-]{1,40}$")
        return value


class EvalFigure(Strict):
    precision: float = Field(ge=0, le=1)
    n: int = Field(ge=0)
    run_date: date
    model: str


_LIMIT_KEYS = {
    "max_repo_mb",
    "max_repo_files",
    "live_scans_per_client_hour",
    "live_scans_per_client_day",
}


class AppConfigResponse(Strict):
    version: str
    live_scans_enabled: bool
    live_scans_paused_reason: str | None
    features: dict[Literal["progress_feed", "markdown_export", "proof"], bool]
    limits: dict[
        Literal[
            "max_repo_mb",
            "max_repo_files",
            "live_scans_per_client_hour",
            "live_scans_per_client_day",
        ],
        int,
    ]
    eval: EvalFigure | None

    @field_validator("limits")
    @classmethod
    def _limits(cls, value: dict[str, int]) -> dict[str, int]:
        if set(value) != _LIMIT_KEYS:
            raise ValueError("limits needs all four keys")
        return value


class HealthResponse(Strict):
    status: Literal["ok"]
    version: str
    live_scans: Literal["enabled", "paused", "disabled"]
    budget_remaining: dict[Pool, float] | None

    @field_validator("budget_remaining")
    @classmethod
    def _budget(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is not None and any(not 0 <= v <= 1 for v in value.values()):
            raise ValueError("budget_remaining fractions are between 0 and 1")
        return value


class ProofRequest(Strict):
    entry: str | None = None
