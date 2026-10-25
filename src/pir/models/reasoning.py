"""Reasoning inputs and outputs (ARCHITECTURE section 6.4)."""

from pydantic import Field

from pir.models import Confidence, ReasonCode, Strict, Verdict
from pir.models.report import CallPathStep, SourceLocation


class ModelVerdict(Strict):
    """The `submit_verdict` arguments."""

    verdict: Verdict
    confidence: Confidence
    call_path: list[CallPathStep] | None
    source_locations: list[SourceLocation]
    explanation: str = Field(max_length=2000)
    limitations: list[str] = Field(min_length=1)


class ToolCallRecord(Strict):
    name: str
    arguments: dict[str, str | int | list[str]]
    ok: bool
    result_bytes: int = Field(ge=0)


class ReasoningOutcome(Strict):
    verdict: ModelVerdict | None
    tool_calls: list[ToolCallRecord]
    attempts: int = Field(ge=0)
    failure: ReasonCode | None
    failure_detail: str | None
