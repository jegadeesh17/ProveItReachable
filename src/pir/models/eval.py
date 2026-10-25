"""Evaluation cases and run summary (ARCHITECTURE section 6.6)."""

import re
from datetime import date

from pydantic import Field, field_validator

from pir.models import Strict, Verdict

_SOURCE_RE = re.compile(
    r"local:eval/fixtures/[A-Za-z0-9._-]+|github:[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]+@[0-9a-f]{40}"
)


class EvalCase(Strict):
    id: str
    repo: str
    package: str
    advisory: str
    label: Verdict
    rationale: str


class EvalRepo(Strict):
    id: str
    source: str

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if not _SOURCE_RE.fullmatch(value) or ".." in value:
            raise ValueError("source must be local:eval/fixtures/<dir> or github:<o>/<r>@<sha>")
        return value


class EvalRunSummary(Strict):
    scored: bool
    run_date: date
    model: str
    experiment: str | None
    n: int = Field(ge=0)
    errored: int = Field(ge=0)
    precision: float | None = Field(ge=0, le=1)
    recall: float | None = Field(ge=0, le=1)
    abstention_rate: float = Field(ge=0, le=1)
    bar: float = Field(ge=0, le=1)
    bar_met: bool
