"""Demo recordings (ARCHITECTURE section 6.6)."""

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from pir.models import Strict
from pir.models.api import ProgressEvent
from pir.models.report import ScanReport

_SLUG_RE = re.compile(r"[a-z0-9-]{1,40}")


class RecordedEvent(Strict):
    offset_ms: int = Field(ge=0)
    event: ProgressEvent


class ScanRecording(Strict):
    schema_version: Literal["1.0"]
    slug: str
    title: str
    description: str
    recorded_at: datetime
    model: str
    events: list[RecordedEvent]
    report: ScanReport

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _SLUG_RE.fullmatch(value):
            raise ValueError("slug must match ^[a-z0-9-]{1,40}$")
        return value
