"""Report builder and the deterministic verdict gate (ARCHITECTURE sections 7.7 and 9.2).

The model proposes; this module decides. Nothing the model says reaches a report without passing
the rules below, and every output is validated by the strict models.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pir.fix_commit import _COMMIT_URL_RE
from pir.models import VERDICTS, Confidence, ReasonCode, Severity, Verdict
from pir.models.index import AnalysisIndex, TargetSite
from pir.models.reasoning import ModelVerdict, ReasoningOutcome
from pir.models.report import (
    NOT_IDENTIFIED_PREFIX,
    SCHEMA_VERSION,
    SCOPE_PREFIX,
    STEP_TOOL_ORDER,
    VERDICT_ORDER,
    Advisory,
    AdvisoryLink,
    CallPathStep,
    Dependency,
    Finding,
    Remediation,
    RepoRef,
    ReportSummary,
    Resolution,
    ScanReport,
    ScanUsage,
    SourceLocation,
    StepStatus,
    VulnerableSymbol,
    compute_finding_id,
    expected_outcome,
)
from pir.models.research import AdvisoryResearch
from pir.reasoning.ast_tool import best_edges
from pir.reasoning.equivalences import implied_symbols

_CONFIDENCE_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
_SEVERITY_RANK: dict[str, int] = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "unknown": 4}
_MAX_TEXT = 2000


@dataclass(frozen=True)
class Candidate:
    """One dependency (and advisory) to turn into a finding; `advisory=None` means not_checked."""

    package: str
    version: str
    advisory: Advisory | None
    advisory_links: list[AdvisoryLink] = field(default_factory=list)
    direct: bool | None = None  # False = transitive
    required_by: str | None = None  # a direct dependency that pulls this one in


@dataclass(frozen=True)
class ScanContext:
    """Everything build_report needs besides the findings."""

    scan_id: str
    tool_version: str
    model: str
    generated_at: datetime
    repo: RepoRef
    resolution: Resolution
    steps: list[StepStatus]
    dependencies: list[Dependency]
    limitations: list[str]
    usage: ScanUsage | None = None


def step_tools(step: str, used: Iterable[str]) -> list[str]:
    """The step-tools rule: tools a step used, in the canonical order of section 6.2."""
    order = STEP_TOOL_ORDER[step]
    used_set = set(used)
    unknown = used_set - set(order)
    if unknown:
        raise ValueError(f"tools {sorted(unknown)} are not allowed for step {step}")
    return [tool for tool in order if tool in used_set]


def _truncate(text: str, limit: int = _MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _merge_links(*groups: Iterable[AdvisoryLink]) -> list[AdvisoryLink]:
    seen: set[str] = set()
    merged: list[AdvisoryLink] = []
    for group in groups:
        for link in group:
            if link.url not in seen:
                seen.add(link.url)
                merged.append(link)
    return merged


def _vulnerable_symbol(research: AdvisoryResearch | None, urls: set[str]) -> VulnerableSymbol:
    if research is not None and research.identified:
        evidence = [u for u in research.evidence_urls if u in urls]
        return VulnerableSymbol(
            identified=True, symbols=list(research.symbols), not_identified_reason=None,
            evidence_urls=evidence,
        )  # fmt: skip
    detail = research.not_identified_reason if research is not None else None
    if detail and not detail.startswith(NOT_IDENTIFIED_PREFIX):
        detail = f"{NOT_IDENTIFIED_PREFIX}: {detail}"
    return VulnerableSymbol(
        identified=False,
        symbols=[],
        not_identified_reason=detail or f"{NOT_IDENTIFIED_PREFIX}: no research result",
        evidence_urls=[],
    )


def _scope_statement(index: AnalysisIndex, candidate: Candidate) -> str:
    parsed = sum(1 for f in index.files if f.parsed)
    skipped = sorted({f.skip_reason for f in index.files if f.skip_reason})
    notes = [f"{len(index.files) - parsed} skipped: " + ", ".join(skipped)] if skipped else []
    if index.truncated:
        notes.append("index truncated at the size limit")
    detail = f" ({'; '.join(notes)})" if notes else ""
    modules = ", ".join(f"`{m}`" for m in index.target_modules) or "the package"
    transitive = ""
    if candidate.direct is False:
        parent = f" of `{candidate.required_by}`" if candidate.required_by else ""
        transitive = f" (this is a transitive dependency{parent})"
    return (
        f"{SCOPE_PREFIX} {parsed} of {len(index.files)} Python files parsed{detail}; examined "
        f"imports, calls and references of {modules} in repository code. Not examined: dynamic "
        f"imports, getattr or reflection, code inside other dependencies{transitive}, C "
        "extensions, runtime configuration."
    )


def _min_confidence(a: str, b: str) -> Confidence:
    return a if _CONFIDENCE_RANK[a] >= _CONFIDENCE_RANK[b] else b  # type: ignore[return-value]


def _valid_locations(
    locations: list[SourceLocation], index: AnalysisIndex, seen: Collection[tuple[str, int]]
) -> list[SourceLocation]:
    lines = {f.path: f.lines for f in index.files}
    return [
        loc
        for loc in locations
        if loc.file in lines and 1 <= loc.line <= lines[loc.file] and (loc.file, loc.line) in seen
    ]


def _first_failed_reachability_rule(
    path: list[CallPathStep] | None,
    symbols: set[str],
    index: AnalysisIndex,
    seen: Collection[tuple[str, int]],
    package: str | None = None,
    version: str | None = None,
) -> tuple[str | None, TargetSite | None]:
    """Gate rule 2. Returns (failed rule text or None, the TargetSite the path ends on)."""
    if not path:
        return "rule 2: likely reachable has no call path", None
    lines = {f.path: f.lines for f in index.files}
    for step in path:
        if step.file not in lines:
            return f"rule 2: step {step.file}:{step.line} is not a file in the index", None
        if not 1 <= step.line <= lines[step.file]:
            return f"rule 2: step {step.file}:{step.line} is outside the file's line range", None
        if (step.file, step.line) not in seen:
            return f"rule 2: step {step.file}:{step.line} was never returned by the ast tool", None
    edges = best_edges(index.edges)
    for i in range(len(path) - 2):
        step_i = path[i]
        step_next = path[i + 1]
        edge = edges.get(step_i.symbol, {}).get(step_next.symbol)
        if (
            edge is None
            or (edge.file, edge.line) != (step_next.file, step_next.line)
            or edge.heuristic != (step_next.edge == "heuristic_call")
        ):
            return f"rule 2: step {i} does not call step {i + 1}", None

    last = path[-1]
    expected_site_caller = path[-2].symbol if len(path) >= 2 else None
    mismatched_site: TargetSite | None = None
    for site in index.target_sites:
        if (
            site.file == last.file
            and site.line == last.line
            and _matches(site, symbols, package, version)
        ):
            if expected_site_caller is None or site.caller == expected_site_caller:
                return None, site
            if mismatched_site is None:
                mismatched_site = site
    if mismatched_site is not None:
        step_idx = len(path) - 2 if len(path) >= 2 else 0
        detail = (
            f"rule 2: step {step_idx} does not call the target site "
            f"(caller is {mismatched_site.caller})"
        )
        return detail, None
    return f"rule 2: last step {last.file}:{last.line} is not a use of the vulnerable symbol", None


def _starts_at_entry(path: list[CallPathStep], index: AnalysisIndex) -> bool:
    first = path[0]
    entry_ids = {d.id for d in index.defs if d.entry_kind is not None}
    module_level = first.symbol == "<module>" or first.symbol.endswith(":<module>")
    return first.edge == "entry" and (first.symbol in entry_ids or module_level)


def _matches(site: TargetSite, symbols: set[str], package: str | None, version: str | None) -> bool:
    """A use of a vulnerable symbol, by name or through a default argument (ADR-0037)."""
    return site.target in symbols or not symbols.isdisjoint(implied_symbols(site, package, version))


def _contradicting_site(
    symbols: set[str], index: AnalysisIndex, package: str | None = None, version: str | None = None
) -> TargetSite | None:
    test_files = {f.path for f in index.files if f.is_test}
    for site in index.target_sites:
        if _matches(site, symbols, package, version) and site.file not in test_files:
            return site
    return None


def _parse_version(v: str) -> Any:
    try:
        from packaging.version import Version

        return Version(v)
    except Exception:
        return None


def compute_remediation(
    candidate: Candidate, links: Iterable[AdvisoryLink] = ()
) -> Remediation | None:
    """Deterministic finding remediation guidance (ADR-0044, AC-85)."""
    if candidate.advisory is None:
        return None
    pkg = candidate.package
    if candidate.advisory.fixed_versions:
        cand_v = _parse_version(candidate.version)
        greater: list[tuple[Any, str]] = []
        if cand_v is not None:
            for fv in candidate.advisory.fixed_versions:
                parsed = _parse_version(fv)
                if parsed is not None and parsed > cand_v:
                    greater.append((parsed, fv))
        if greater:
            target_version = min(greater, key=lambda pair: pair[0])[1]
        else:
            target_version = candidate.advisory.fixed_versions[0]
        return Remediation(
            package=pkg,
            target_version=target_version,
            guidance=f"Upgrade {pkg} to version {target_version} or newer.",
        )

    all_links = list(links)
    fix_commit_link = next(
        (
            link
            for link in all_links
            if link.source == "osv"
            and (
                (link.title and "fix commit" in link.title.lower())
                or bool(_COMMIT_URL_RE.match(link.url))
                or "/commit/" in link.url
            )
        ),
        None,
    )
    if fix_commit_link is not None:
        guidance = (
            f"No fixed version is published. Inspect the fix commit at {fix_commit_link.url} "
            "for remediation guidance."
        )
        return Remediation(package=pkg, target_version=None, guidance=guidance)

    guidance = f"No fixed version is published for {pkg}."
    return Remediation(package=pkg, target_version=None, guidance=guidance)


@dataclass(frozen=True)
class _Base:
    """What every finding of one candidate shares."""

    candidate: Candidate
    symbol: VulnerableSymbol
    links: list[AdvisoryLink]

    def make(
        self,
        verdict: Verdict,
        confidence: Confidence,
        *,
        explanation: str,
        limitations: list[str],
        source_locations: list[SourceLocation],
        call_path: list[CallPathStep] | None = None,
        reason_code: ReasonCode | None = None,
        reason: str | None = None,
        remediation: Remediation | None = None,
    ) -> Finding:
        cand = self.candidate
        advisory = cand.advisory
        if remediation is None and advisory is not None:
            remediation = compute_remediation(cand, self.links)
        return Finding(
            finding_id=compute_finding_id(
                cand.package, cand.version, advisory.id if advisory else None
            ),
            kind="advisory" if advisory else "not_checked",
            verdict=verdict,
            confidence=confidence,
            package=cand.package,
            version=cand.version,
            advisory=advisory,
            vulnerable_symbol=self.symbol if advisory else None,
            advisory_links=self.links if advisory else [],
            source_locations=source_locations,
            call_path=call_path,
            explanation=_truncate(explanation),
            limitations=limitations,
            reason_code=reason_code,
            reason=reason,
            remediation=remediation,
        )

    def uncertain(
        self,
        code: ReasonCode,
        reason: str,
        *,
        explanation: str | None = None,
        limitations: list[str] | None = None,
        source_locations: list[SourceLocation] | None = None,
    ) -> Finding:
        return self.make(
            "uncertain",
            "low",
            explanation=explanation or f"No reachability verdict was established: {reason}",
            limitations=limitations or [f"No verdict: {reason}"],
            source_locations=source_locations or [],
            reason_code=code,
            reason=reason,
        )


def build_finding(
    candidate: Candidate,
    research: AdvisoryResearch | None,
    outcome: ReasoningOutcome | None,
    index: AnalysisIndex | None,
    forced: tuple[ReasonCode, str] | None = None,
    *,
    seen_locations: Collection[tuple[str, int]] = (),
) -> Finding:
    """The verdict gate (section 9.2). `seen_locations` is `AstTool.seen_locations`."""
    links = _merge_links(candidate.advisory_links, research.links if research else [])
    symbol = _vulnerable_symbol(research, {link.url for link in links})
    base = _Base(candidate, symbol, links)
    if candidate.advisory is None:
        code, reason = forced or ("osv_failed", "the OSV query failed for this dependency")
        return base.uncertain(code, reason)
    if forced is not None:
        return base.uncertain(*forced)
    if not symbol.identified:
        code = research.failure if research and research.failure else None
        reason = symbol.not_identified_reason or NOT_IDENTIFIED_PREFIX
        return base.uncertain(code or "vulnerable_function_not_identified", reason)
    if outcome is None or outcome.verdict is None:
        code = outcome.failure if outcome and outcome.failure else "model_output_invalid"
        detail = (outcome.failure_detail if outcome else None) or "no valid model verdict"
        return base.uncertain(code, detail)
    if index is None:
        return base.uncertain("sandbox_failed", "no code index is available to check the claim")
    return _gate(base, outcome.verdict, index, seen_locations)


def _incomplete(index: AnalysisIndex) -> bool:
    return index.truncated or any(not f.parsed for f in index.files)


def _gate(
    base: _Base, verdict: ModelVerdict, index: AnalysisIndex, seen: Collection[tuple[str, int]]
) -> Finding:
    """Rules 2 to 5 for a model verdict on a grounded vulnerable symbol."""
    symbols = set(base.symbol.symbols)
    locations = _valid_locations(verdict.source_locations, index, seen)
    limitations = list(verdict.limitations)
    if len(locations) < len(verdict.source_locations):
        limitations.append(
            "Source locations given by the model that are not in the index were dropped."
        )

    def downgrade(code: ReasonCode, reason: str) -> Finding:
        if verdict.verdict == "uncertain":  # nothing was downgraded: the model itself said so
            lead = "The model answered 'uncertain'"
        else:
            lead = f"Downgraded from '{verdict.verdict}'"
        return base.uncertain(
            code,
            reason,
            explanation=f"{lead}: {reason}. {verdict.explanation}",
            limitations=limitations,
            source_locations=locations,
        )

    if verdict.verdict == "uncertain":
        return downgrade(
            "unsupported_reachability_claim", "the model could not establish a reachability claim"
        )
    if verdict.verdict == "likely reachable":
        failed, site = _first_failed_reachability_rule(
            verdict.call_path, symbols, index, seen, base.candidate.package, base.candidate.version
        )
        if failed is not None or site is None or verdict.call_path is None:
            return downgrade("unsupported_reachability_claim", failed or "rule 2: no call path")
        path = verdict.call_path
        heuristic = any(step.edge == "heuristic_call" for step in path)
        cap: Confidence = "high" if not heuristic and _starts_at_entry(path, index) else "medium"
        if heuristic:
            limitations.append(
                "The call path includes heuristic edges (name-based or an inferred import root)."
            )
        if _incomplete(index):
            limitations.append("Some files were skipped or the index was truncated.")
        locations = locations or [
            SourceLocation(file=site.file, line=site.line, kind=site.kind, snippet=site.snippet)
        ]
        return base.make(
            "likely reachable",
            _min_confidence(verdict.confidence, cap),
            explanation=verdict.explanation,
            limitations=limitations,
            source_locations=locations,
            call_path=path,
        )
    site = _contradicting_site(symbols, index, base.candidate.package, base.candidate.version)
    if site is not None:
        return downgrade(
            "contradicted_by_index",
            f"rule 3: the index has a use of {site.target} at {site.file}:{site.line}",
        )
    confidence = verdict.confidence
    if _incomplete(index):
        confidence = _min_confidence(confidence, "medium")
    if base.candidate.direct is False:
        confidence = _min_confidence(confidence, "low")
    limitations.append(_scope_statement(index, base.candidate))
    if not locations:
        limitations.append("No import or call site found.")
    return base.make(
        "no reachable path detected",
        confidence,
        explanation=verdict.explanation,
        limitations=limitations,
        source_locations=locations,
    )


def rank(findings: list[Finding]) -> list[Finding]:
    """Rule 6: verdict, then confidence, severity, package name and advisory ID."""

    def key(f: Finding) -> tuple[int, int, int, str, str]:
        severity: Severity = f.advisory.severity if f.advisory else "unknown"
        return (
            VERDICT_ORDER[f.verdict],
            _CONFIDENCE_RANK[f.confidence],
            _SEVERITY_RANK[severity],
            f.package,
            f.advisory.id if f.advisory else "",
        )

    return sorted(findings, key=key)


def build_report(ctx: ScanContext, findings: list[Finding]) -> ScanReport:
    """Rank, summarise and strictly validate the final report."""
    ranked = rank(findings)
    deps = sorted(ctx.dependencies, key=lambda d: d.name)
    summary = ReportSummary(
        dependencies_total=len(deps),
        dependencies_checked=sum(d.osv_status == "checked" for d in deps),
        dependencies_unresolved=sum(not d.resolved for d in deps),
        dependencies_not_checked=sum(d.osv_status == "not_checked" for d in deps),
        findings_total=len(ranked),
        by_verdict={v: sum(f.verdict == v for f in ranked) for v in VERDICTS},
    )
    return ScanReport(
        schema_version=SCHEMA_VERSION,  # type: ignore[arg-type]
        scan_id=ctx.scan_id,
        tool_version=ctx.tool_version,
        model=ctx.model,
        generated_at=ctx.generated_at,
        repo=ctx.repo,
        outcome=expected_outcome(ctx.steps, deps, ranked),
        resolution=ctx.resolution,
        steps=ctx.steps,
        dependencies=deps,
        findings=ranked,
        summary=summary,
        limitations=ctx.limitations,
        usage=ctx.usage,
    )
