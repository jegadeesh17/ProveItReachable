"""Static analysis index produced by the sandbox agent (ARCHITECTURE section 6.3)."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from pir.models import SHA_RE, Strict, check_repo_path


class IndexedFile(Strict):
    path: str
    lines: int = Field(ge=0)
    is_test: bool
    parsed: bool
    skip_reason: str | None

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return check_repo_path(value)

    @model_validator(mode="after")
    def _check(self) -> "IndexedFile":
        if self.parsed == (self.skip_reason is not None):
            raise ValueError("skip_reason is set iff the file was not parsed")
        return self


class ImportRecord(Strict):
    file: str
    line: int = Field(ge=1)
    module: str
    name: str | None
    asname: str | None
    bound_as: str


class DefRecord(Strict):
    id: str
    file: str
    line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    decorators: list[str]
    entry_kind: Literal["main_block", "route", "cli", "script", "module_level"] | None

    @model_validator(mode="after")
    def _check(self) -> "DefRecord":
        if self.end_line < self.line:
            raise ValueError("end_line must not precede line")
        return self


class TargetSite(Strict):
    file: str
    line: int = Field(ge=1)
    col: int = Field(ge=0)
    caller: str
    kind: Literal["call", "reference"]
    snippet: str = Field(max_length=200)
    target: str
    args: int | None = Field(default=None, ge=0)  # kind="call": positional count; None otherwise
    keywords: list[str] | None = None  # kind="call": sorted keyword names; None otherwise


class Edge(Strict):
    caller: str
    callee: str
    file: str
    line: int = Field(ge=1)
    heuristic: bool


class AnalysisIndex(Strict):
    agent_protocol: Literal[1]
    commit_sha: str
    target_modules: list[str]
    files: list[IndexedFile]
    imports: list[ImportRecord]
    defs: list[DefRecord]
    target_sites: list[TargetSite]
    edges: list[Edge]
    truncated: bool
    stats: dict[str, int]

    @field_validator("commit_sha")
    @classmethod
    def _sha(cls, value: str) -> str:
        if not SHA_RE.fullmatch(value):
            raise ValueError("commit_sha must be 40 lowercase hex characters")
        return value
