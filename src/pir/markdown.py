"""Markdown export of a `ScanReport` (J4, ARCHITECTURE sections 7.7 and "URL boundary...").

The order is the UI's: summary, findings (ranked, as stored), dependencies not examined, method.
Per finding: verdict heading, evidence (vulnerable function, call path, source locations, advisory
links), confidence, the reason (uncertain only), limitations, explanation. See
`web/fixtures/reports/*.md` for worked examples.

Every string that did not come from this module is untrusted (advisory text, Tavily titles, model
output, repository snippets). Rules:

* prose is escaped: Markdown metacharacters get a backslash, `<`, `>` and `&` become entities
  (so no raw HTML), and `://` and `www.` are broken so no renderer turns bare text into a link;
* snippets, paths and symbols go in code spans with backticks removed (they cannot end the span);
* links are written only as `<https://...>` autolinks of URLs that pass `safe_url`; any other URL
  (`javascript:`, `data:`, malformed, over-long) is not written at all.
"""

import re
from datetime import UTC
from urllib.parse import urlsplit

from pir.models.report import AdvisoryLink, Dependency, Finding, ScanReport

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACE = re.compile(r"\s+")
_META = re.compile(r"[\\`*_\[\]()#|~]")
_ENTITIES = {"<": "&lt;", ">": "&gt;", "&": "&amp;"}
_ENTITY_CHARS = re.compile(r"[<>&]")
_LINE_START = re.compile(r"^(?:([-+=])|(\d+)([.)]))")
_MAX_URL = 2048
_EDGE = {
    "entry": "entry",
    "call": "call",
    "heuristic_call": "heuristic call",
    "vulnerable_call": "vulnerable call",
}


def safe_url(url: str) -> str | None:
    """The URL if it may be written as an autolink, else None.

    Only `http` and `https` with a host, no whitespace, control characters, `<` or `>`.
    """
    if not isinstance(url, str) or not url or len(url) > _MAX_URL:
        return None
    if _CONTROL.search(url) or any(c.isspace() or c in "<>" for c in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return url


def _clean(text: str) -> str:
    return _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def _escape_line(line: str, *, start: bool) -> str:
    """`start`: the text begins a line or list item, so it must not open a list or rule."""
    line = line.strip()
    out = _META.sub(lambda m: "\\" + m.group(0), line)
    out = _ENTITY_CHARS.sub(lambda m: _ENTITIES[m.group(0)], out)
    out = out.replace("://", ":&#47;/").replace("www.", "www\\.")
    match = _LINE_START.match(out) if start else None
    if match:
        if match.group(1):
            out = "\\" + out
        else:
            end = match.end(2)
            out = out[:end] + "\\" + out[end:]
    return out


def esc(text: object, *, start: bool = False) -> str:
    """Untrusted text as one escaped line of prose (whitespace collapsed).

    Pass `start=True` when the text begins a line or a list item.
    """
    return _escape_line(_SPACE.sub(" ", _clean(str(text))), start=start)


def esc_block(text: object) -> str:
    """Untrusted multi-line prose: each line escaped and stripped of indentation."""
    lines = [_escape_line(line, start=True) for line in _clean(str(text)).split("\n")]
    return "\n".join(lines).strip()


def code(text: object) -> str:
    """A code span: backticks and control characters removed, whitespace collapsed."""
    body = _SPACE.sub(" ", _CONTROL.sub("", str(text)).replace("`", "")).strip()
    return f"`{body or '(empty)'}`"


def _link(link: AdvisoryLink) -> str:
    url = safe_url(link.url)
    meta = link.source if not link.title else f"{link.source}, {esc(link.title)}"
    if url is None:
        return f"- (link omitted: not a safe http(s) URL) ({meta})"
    return f"- <{url}> ({meta})"


def _evidence(f: Finding) -> list[str]:
    lines = ["**Evidence**", ""]
    if f.kind != "advisory":
        lines.append(
            "- No advisory was retrieved for this package, so there is no evidence to show."
        )
        return lines
    vs = f.vulnerable_symbol
    if vs is not None:
        if vs.identified:
            lines.append("- Vulnerable function: " + ", ".join(code(s) for s in vs.symbols))
        else:
            reason = vs.not_identified_reason or "vulnerable function not identified"
            lines.append(f"- Vulnerable function: not identified ({esc(reason)})")
    if f.call_path:
        lines.append("- Call path:")
        for i, step in enumerate(f.call_path, 1):
            where = code(f"{step.file}:{step.line}")
            edge = _EDGE.get(step.edge, esc(step.edge))
            lines.append(f"  {i}. {where} {code(step.symbol)} ({edge})")
    if f.source_locations:
        lines.append("- Source locations:")
        for loc in f.source_locations:
            row = f"  - {code(f'{loc.file}:{loc.line}')} {esc(loc.kind)}"
            if loc.snippet:
                row += f": {code(loc.snippet)}"
            lines.append(row)
    else:
        lines.append("- Source locations: none found in repository code")
    if f.advisory_links:
        lines.append("- Advisory links:")
        lines.extend("  " + _link(link) for link in f.advisory_links)
    return lines


def _finding(n: int, f: Finding) -> list[str]:
    ident = esc(f.advisory.id) if f.advisory is not None else "not checked"
    lines = [f"### {n}. {esc(f.verdict)}: {esc(f.package)} {esc(f.version)} ({ident})", ""]
    lines += _evidence(f)
    lines += ["", f"**Confidence:** {esc(f.confidence)}", ""]
    if f.reason:
        code_part = f" ({esc(f.reason_code)})" if f.reason_code else ""
        lines += [f"**Reason:** {esc(f.reason)}{code_part}", ""]
    if f.remediation:
        lines += [f"**Remediation:** {esc(f.remediation.guidance)}", ""]
    lines += ["**Limitations**", ""]
    lines += [f"- {esc(x, start=True)}" for x in f.limitations]
    lines += ["", "**Explanation**", "", esc_block(f.explanation), ""]
    return lines


def _not_examined(dependencies: list[Dependency]) -> list[str]:
    rows = []
    for d in dependencies:
        if not d.resolved:
            rows.append(
                f"- {esc(d.name)}: unresolved. {esc(d.unresolved_reason or 'No reason recorded.')}"
            )
        elif d.osv_status == "not_checked":
            name = f"{esc(d.name)} {esc(d.version)}" if d.version else esc(d.name)
            rows.append(f"- {name}: not checked. {esc(d.osv_error or 'No reason recorded.')}")
    if not rows:
        return []
    return ["## Dependencies not examined", "", *rows, ""]


def render_markdown(report: ScanReport) -> str:
    """The report as Markdown, in the same order as the UI. Ends with one newline."""
    s = report.summary
    repo = report.repo
    generated = report.generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = safe_url(repo.url)
    lines = [
        f"# ProveItReachable report: {esc(repo.owner)}/{esc(repo.name)}",
        "",
        f"- Repository: {f'<{url}>' if url else code(repo.url)}",
    ]
    if repo.ref_requested is not None:
        lines.append(f"- Ref requested: {code(repo.ref_requested)}")
    lines += [
        f"- Commit: {code(repo.commit_sha)}",
        f"- Outcome: {esc(report.outcome)}",
        f"- Generated: {generated} by ProveItReachable {esc(report.tool_version)} "
        f"(model {esc(report.model)})",
        f"- Dependencies: {s.dependencies_total} total, {s.dependencies_checked} checked, "
        f"{s.dependencies_unresolved} unresolved, {s.dependencies_not_checked} not checked",
        "",
        "## Summary",
        "",
    ]
    for verdict in ("likely reachable", "uncertain", "no reachable path detected"):
        lines.append(f"- {verdict}: {s.by_verdict.get(verdict, 0)}")
    lines.append("")
    if report.outcome == "clean":
        lines += [
            "No known-vulnerable dependencies were found. "
            f"All {s.dependencies_checked} dependencies were checked against OSV at their exact "
            "versions.",
            "",
        ]
    elif report.outcome == "incomplete":
        lines += ["Not a clean result: some dependencies were not examined (see below).", ""]
    if report.findings:
        lines += ["## Findings", ""]
        for n, finding in enumerate(report.findings, 1):
            lines += _finding(n, finding)
    lines += _not_examined(report.dependencies)
    lines += ["## Method and limitations", ""]
    lines += [f"- {esc(x, start=True)}" for x in [*report.resolution.notes, *report.limitations]]
    return "\n".join(lines) + "\n"
