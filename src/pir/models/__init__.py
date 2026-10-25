"""Strict data models (ARCHITECTURE section 6): the base class, shared literals and path checks.

Every model derives from `Strict`: unknown fields, coerced types and mutation are all rejected
(NFR-05). Submodules are imported explicitly by callers, for example `pir.models.report`.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    """Base for every model: no extra fields, no type coercion, immutable."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


Verdict = Literal[
    "likely reachable", "no reachable path detected", "uncertain"
]  # exact, case-sensitive
Confidence = Literal["high", "medium", "low"]
Mode = Literal["live", "replay", "proof"]
Pool = Literal["nebius", "tavily"]
StepName = Literal["resolve", "osv", "research", "analysis", "reasoning", "report"]
Outcome = Literal["findings", "clean", "incomplete"]
StepState = Literal["pending", "running", "done", "failed", "skipped"]
Severity = Literal["critical", "high", "moderate", "low", "unknown"]
ToolName = Literal["lockfile", "uv", "sandbox", "osv", "tavily", "nemotron", "ast", "verdict_gate"]
DependencySource = Literal[
    "uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt", "uv pip compile", "manifest"
]  # manifest = declared, unresolved
ReasonCode = Literal[
    "vulnerable_function_not_identified",
    "tavily_failed",
    "nebius_failed",
    "model_output_invalid",
    "unsupported_reachability_claim",
    "contradicted_by_index",
    "osv_failed",
    "sandbox_timeout",
    "sandbox_failed",
    "spend_cap_reached",
    "scan_budget_reached",
    "scan_timeout",
    "finding_limit_reached",
]

VERDICTS: tuple[str, ...] = ("likely reachable", "no reachable path detected", "uncertain")

SHA_RE = re.compile(r"[0-9a-f]{40}")
UUID_HEX_RE = re.compile(r"[0-9a-f]{32}")

# Anchored PEP 440 version (packaging's VERSION_PATTERN, case-insensitive, verbose form unrolled).
PEP440_RE = re.compile(
    r"v?"
    r"(?:"
    r"(?:[0-9]+!)?"
    r"[0-9]+(?:\.[0-9]+)*"
    r"(?:[-_\.]?(?:alpha|a|beta|b|preview|pre|c|rc)[-_\.]?[0-9]*)?"
    r"(?:(?:-[0-9]+)|(?:[-_\.]?(?:post|rev|r)[-_\.]?[0-9]*))?"
    r"(?:[-_\.]?dev[-_\.]?[0-9]*)?"
    r")"
    r"(?:\+[a-z0-9]+(?:[-_\.][a-z0-9]+)*)?",
    re.IGNORECASE,
)
PACKAGE_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def is_pep440(value: str) -> bool:
    """True for a full PEP 440 version string (no whitespace, specifiers or URLs)."""
    return len(value) <= 64 and PEP440_RE.fullmatch(value) is not None


def check_repo_path(value: str) -> str:
    """A repo-relative POSIX path: no "..", no leading "/", no backslash, no NUL."""
    if not value or len(value) > 1024:
        raise ValueError("path must be 1 to 1024 characters")
    if value.startswith("/") or "\\" in value or "\x00" in value:
        raise ValueError("path must be repo-relative POSIX")
    if any(part in ("..", "") for part in value.split("/")):
        raise ValueError("path must not contain '..' or empty segments")
    return value
