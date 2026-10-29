"""The scan report (ARCHITECTURE section 6.2) and its validation rules."""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any, Literal, get_args

from pydantic import Field, field_validator, model_validator

from pir.models import (
    PACKAGE_NAME_RE,
    SHA_RE,
    UUID_HEX_RE,
    VERDICTS,
    Confidence,
    DependencySource,
    Outcome,
    ReasonCode,
    Severity,
    StepName,
    StepState,
    Strict,
    ToolName,
    Verdict,
    check_repo_path,
    is_pep440,
)

SCHEMA_VERSION = "1.0"
STEP_NAMES: tuple[str, ...] = get_args(StepName)
FINAL_STEP_STATES = ("done", "failed", "skipped")
SCOPE_PREFIX = "Scope:"
NOT_IDENTIFIED_PREFIX = "vulnerable function not identified"
COMPILED_NOTE = "Versions were compiled with uv pip compile, not locked."

# Step tools rule (section 6.2): the tools a step may list, in the order they are listed.
STEP_TOOL_ORDER: dict[str, tuple[str, ...]] = {
    "resolve": ("sandbox", "lockfile", "uv"),
    "osv": ("osv",),
    "research": ("tavily", "nemotron"),
    "analysis": ("sandbox", "ast"),
    "reasoning": ("nemotron", "ast"),
    "report": ("verdict_gate",),
}

# Report order of verdicts (AC-23, OQ-10).
VERDICT_ORDER: dict[str, int] = {
    "likely reachable": 0,
    "uncertain": 1,
    "no reachable path detected": 2,
}

_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_GROUP_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_ADVISORY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
_URL_RE = re.compile(r"https?://\S+")


def compute_finding_id(package: str, version: str, advisory_id: str | None) -> str:
    """sha256("{package}|{version}|{advisory_id or 'not_checked'}")[:16]."""
    text = f"{package}|{version}|{advisory_id or 'not_checked'}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class RepoRef(Strict):
    url: str
    owner: str
    name: str
    ref_requested: str | None
    default_branch: str
    commit_sha: str

    @model_validator(mode="after")
    def _check(self) -> "RepoRef":
        if not _OWNER_RE.fullmatch(self.owner):
            raise ValueError("owner does not match the GitHub owner pattern")
        if not _NAME_RE.fullmatch(self.name) or self.name in (".", ".."):
            raise ValueError("name does not match the GitHub repository pattern")
        if self.url != f"https://github.com/{self.owner}/{self.name}":
            raise ValueError("url must be https://github.com/{owner}/{name}")
        if not SHA_RE.fullmatch(self.commit_sha):
            raise ValueError("commit_sha must be 40 lowercase hex characters")
        return self


class StepStatus(Strict):
    name: StepName
    state: StepState
    tools: list[ToolName]
    started_at: datetime | None
    finished_at: datetime | None
    message: str | None = Field(max_length=300)

    @model_validator(mode="after")
    def _check_tools(self) -> "StepStatus":
        order = STEP_TOOL_ORDER[self.name]
        if any(t not in order for t in self.tools):
            raise ValueError(f"tools {self.tools} are not allowed for step {self.name}")
        positions = [order.index(t) for t in self.tools]
        if positions != sorted(set(positions)):
            raise ValueError(f"tools {self.tools} must be unique and in order {list(order)}")
        if "lockfile" in self.tools and "uv" in self.tools:
            raise ValueError("resolve uses lockfile or uv, not both")
        if self.state == "skipped" and self.tools:
            raise ValueError("a skipped step has no tools")
        return self


class Resolution(Strict):
    method: Literal["lockfile", "compiled", "failed"]
    python_version: str | None
    lockfile: str | None
    compiled_from: str | None
    notes: list[str]

    @model_validator(mode="after")
    def _check(self) -> "Resolution":
        if (self.method == "lockfile") != (self.lockfile is not None):
            raise ValueError("lockfile is set iff method == 'lockfile'")
        if (self.method == "compiled") != (self.compiled_from is not None):
            raise ValueError("compiled_from is set iff method == 'compiled'")
        for path in (self.lockfile, self.compiled_from):
            if path is not None:
                check_repo_path(path)
        return self


class Dependency(Strict):
    name: str
    source: DependencySource
    groups: list[str]
    version: str | None
    resolved: bool
    unresolved_reason: str | None
    direct: bool | None
    osv_status: Literal["checked", "not_checked", "not_queried"]
    osv_error: str | None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if len(value) > 100 or not PACKAGE_NAME_RE.fullmatch(value):
            raise ValueError("name must be a PEP 503 normalized package name")
        return value

    @field_validator("groups")
    @classmethod
    def _groups(cls, value: list[str]) -> list[str]:
        if any(not _GROUP_RE.fullmatch(group) for group in value):
            raise ValueError("invalid dependency group name")
        return value

    @model_validator(mode="after")
    def _check(self) -> "Dependency":
        if self.resolved:
            if self.version is None or not is_pep440(self.version):
                raise ValueError("a resolved dependency needs a PEP 440 version")
            if self.unresolved_reason is not None:
                raise ValueError("unresolved_reason is set only when not resolved")
            if self.osv_status == "not_queried":
                raise ValueError("a resolved dependency is checked or not_checked")
        else:
            if self.version is not None:
                raise ValueError("version is None when not resolved")
            if not self.unresolved_reason:
                raise ValueError("an unresolved dependency needs unresolved_reason")
            if self.osv_status != "not_queried":
                raise ValueError("an unresolved dependency is never queried")
        if (self.osv_status == "not_checked") != (self.osv_error is not None):
            raise ValueError("osv_error is set iff osv_status == 'not_checked'")
        return self


class Advisory(Strict):
    id: str
    aliases: list[str]
    summary: str | None = Field(max_length=500)
    severity: Severity
    fixed_versions: list[str]
    published: datetime | None
    modified: datetime | None

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ADVISORY_ID_RE.fullmatch(value):
            raise ValueError("advisory id has unexpected characters")
        return value

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, value: list[str]) -> list[str]:
        if value != sorted(value):
            raise ValueError("aliases must be sorted")
        return value


class AdvisoryLink(Strict):
    url: str = Field(max_length=2048)
    source: Literal["osv", "tavily"]
    title: str | None = Field(max_length=200)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        if not _URL_RE.fullmatch(value):
            raise ValueError("url must start with http:// or https://")
        return value


class VulnerableSymbol(Strict):
    identified: bool
    symbols: list[str]
    not_identified_reason: str | None
    evidence_urls: list[str]

    @model_validator(mode="after")
    def _check(self) -> "VulnerableSymbol":
        if self.identified != bool(self.symbols):
            raise ValueError("symbols are non-empty iff identified")
        if self.identified:
            if self.not_identified_reason is not None:
                raise ValueError("not_identified_reason is set only when not identified")
        elif not (self.not_identified_reason or "").startswith(NOT_IDENTIFIED_PREFIX):
            raise ValueError(f"not_identified_reason must start with '{NOT_IDENTIFIED_PREFIX}'")
        return self


class SourceLocation(Strict):
    file: str
    line: int = Field(ge=1)
    kind: Literal["import", "call", "reference", "definition"]
    snippet: str | None = Field(max_length=200)

    @field_validator("file")
    @classmethod
    def _file(cls, value: str) -> str:
        return check_repo_path(value)


class CallPathStep(Strict):
    file: str
    line: int = Field(ge=1)
    edge: Literal["entry", "call", "heuristic_call", "vulnerable_call"]
    symbol: str = Field(min_length=1)

    @field_validator("file")
    @classmethod
    def _file(cls, value: str) -> str:
        return check_repo_path(value)


class Proof(Strict):  # M2, cuttable
    status: Literal["proven", "not_proven"]
    reason: str
    ran_at: datetime
    entry: str
    target: str
    recorded_call: str | None


class Remediation(Strict):  # ADR-0044: deterministic upgrade guidance
    package: str
    target_version: str | None
    guidance: str

    def __init__(
        self,
        package: str,
        target_version: str | None = None,
        guidance: str | None = None,
        **data: Any,
    ) -> None:
        if "guidance" in data:
            guidance = data.pop("guidance")
        if "package" in data:
            package = data.pop("package")
        if "target_version" in data:
            target_version = data.pop("target_version")
        if guidance is None:
            raise TypeError("missing required argument: 'guidance'")
        super().__init__(package=package, target_version=target_version, guidance=guidance, **data)


class Finding(Strict):
    finding_id: str
    kind: Literal["advisory", "not_checked"]
    verdict: Verdict
    confidence: Confidence
    package: str
    version: str
    advisory: Advisory | None
    vulnerable_symbol: VulnerableSymbol | None
    advisory_links: list[AdvisoryLink]
    source_locations: list[SourceLocation]
    call_path: list[CallPathStep] | None
    explanation: str = Field(max_length=2000)
    limitations: list[str] = Field(min_length=1)
    reason_code: ReasonCode | None
    reason: str | None
    proof: Proof | None = None
    remediation: Remediation | None = None

    @model_validator(mode="after")
    def _check(self) -> "Finding":
        if not PACKAGE_NAME_RE.fullmatch(self.package) or len(self.package) > 100:
            raise ValueError("package must be a PEP 503 normalized name")
        if not is_pep440(self.version):
            raise ValueError("version must be a PEP 440 version")
        advisory_id = self.advisory.id if self.advisory else None
        if self.finding_id != compute_finding_id(self.package, self.version, advisory_id):
            raise ValueError("finding_id does not match package, version and advisory")
        if self.remediation is not None and self.remediation.package != self.package:
            raise ValueError("remediation package does not match finding package")
        self._check_kind()
        self._check_verdict()
        return self

    def _check_kind(self) -> None:
        if self.kind == "advisory":
            if self.advisory is None or self.vulnerable_symbol is None:
                raise ValueError("an advisory finding needs advisory and vulnerable_symbol")
            if not self.advisory_links:
                raise ValueError("an advisory finding needs at least one advisory link")
            urls = {link.url for link in self.advisory_links}
            if not set(self.vulnerable_symbol.evidence_urls) <= urls:
                raise ValueError("evidence_urls must be a subset of the advisory link urls")
        elif self.advisory or self.vulnerable_symbol or self.advisory_links or self.remediation:
            raise ValueError("a not_checked finding has no advisory, symbol, links or remediation")

    def _check_verdict(self) -> None:
        if self.verdict == "likely reachable":
            if not self.call_path:
                raise ValueError("likely reachable needs a non-empty call_path")
            if not self.source_locations:
                raise ValueError("likely reachable needs source_locations")
        elif self.call_path is not None:
            raise ValueError("call_path is only for likely reachable")
        if self.verdict == "uncertain":
            if self.reason_code is None or not self.reason:
                raise ValueError("uncertain needs reason_code and reason")
            if self.confidence != "low":
                raise ValueError("uncertain findings have low confidence")
        elif self.reason_code is not None or self.reason is not None:
            raise ValueError("reason_code and reason are only for uncertain")
        if self.verdict == "no reachable path detected" and not any(
            text.startswith(SCOPE_PREFIX) for text in self.limitations
        ):
            raise ValueError("a no-path finding needs the scope statement limitation")


class ReportSummary(Strict):
    dependencies_total: int = Field(ge=0)
    dependencies_checked: int = Field(ge=0)
    dependencies_unresolved: int = Field(ge=0)
    dependencies_not_checked: int = Field(ge=0)
    findings_total: int = Field(ge=0)
    by_verdict: dict[Verdict, int]

    @field_validator("by_verdict")
    @classmethod
    def _by_verdict(cls, value: dict[Verdict, int]) -> dict[Verdict, int]:
        if set(value) != set(VERDICTS):
            raise ValueError("by_verdict needs all three verdict keys")
        if any(count < 0 for count in value.values()):
            raise ValueError("by_verdict counts must not be negative")
        return value


def expected_outcome(
    steps: list[StepStatus], dependencies: list[Dependency], findings: list[Finding]
) -> Outcome:
    """clean / incomplete / findings, per the rule in section 6.2 (AC-11)."""
    if findings:
        return "findings"
    resolve_done = any(s.name == "resolve" and s.state == "done" for s in steps)
    unresolved = any(not d.resolved for d in dependencies)
    not_checked = any(d.osv_status == "not_checked" for d in dependencies)
    if unresolved or not_checked or not resolve_done:
        return "incomplete"
    return "clean"


class NemotronUsage(Strict):  # ADR-0045: metered, not invoiced
    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    usd: float = Field(ge=0, allow_inf_nan=False)


class TavilyUsage(Strict):
    searches: int = Field(ge=0)
    usd: float = Field(ge=0, allow_inf_nan=False)


class SandboxUsage(Strict):
    operations: int = Field(ge=0)


class OsvUsage(Strict):
    queries: int = Field(ge=0)


class ScanUsage(Strict):
    """What one scan used (ADR-0045). All four services are present; an unused one has zeros."""

    nemotron: NemotronUsage
    tavily: TavilyUsage
    sandbox: SandboxUsage
    osv: OsvUsage
    trace_url: str | None = None  # always serialized; null when not shared


class ScanReport(Strict):
    schema_version: Literal["1.0"]
    scan_id: str
    tool_version: str
    model: str
    generated_at: datetime
    repo: RepoRef
    outcome: Outcome
    resolution: Resolution
    steps: list[StepStatus]
    dependencies: list[Dependency]
    findings: list[Finding]
    summary: ReportSummary
    limitations: list[str]
    usage: ScanUsage | None = None

    @field_validator("scan_id")
    @classmethod
    def _scan_id(cls, value: str) -> str:
        if not UUID_HEX_RE.fullmatch(value):
            raise ValueError("scan_id must be a uuid4 hex string")
        return value

    @field_validator("generated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("generated_at must be a UTC timestamp")
        return value

    @model_validator(mode="after")
    def _check(self) -> "ScanReport":
        if [s.name for s in self.steps] != list(STEP_NAMES):
            raise ValueError(f"steps must be exactly {list(STEP_NAMES)} in order")
        if any(s.state not in FINAL_STEP_STATES for s in self.steps):
            raise ValueError("a final report has only done, failed or skipped steps")
        names = [d.name for d in self.dependencies]
        if names != sorted(names):
            raise ValueError("dependencies must be sorted by name")
        ids = [f.finding_id for f in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding_ids must be unique")
        order = [VERDICT_ORDER[f.verdict] for f in self.findings]
        if order != sorted(order):
            raise ValueError("findings must be ranked: likely reachable, uncertain, no path")
        self._check_summary()
        if self.outcome != expected_outcome(self.steps, self.dependencies, self.findings):
            raise ValueError("outcome does not match the report content")
        return self

    def _check_summary(self) -> None:
        deps, summary = self.dependencies, self.summary
        expected = {
            "dependencies_total": len(deps),
            "dependencies_checked": sum(d.osv_status == "checked" for d in deps),
            "dependencies_unresolved": sum(not d.resolved for d in deps),
            "dependencies_not_checked": sum(d.osv_status == "not_checked" for d in deps),
            "findings_total": len(self.findings),
        }
        for key, value in expected.items():
            if getattr(summary, key) != value:
                raise ValueError(f"summary.{key} does not match the report content")
        by_verdict = {v: sum(f.verdict == v for f in self.findings) for v in VERDICTS}
        if summary.by_verdict != by_verdict:
            raise ValueError("summary.by_verdict does not match the findings")
