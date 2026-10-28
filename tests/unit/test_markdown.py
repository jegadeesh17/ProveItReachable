"""render_markdown: AC-62 content and order, and the escaping carry-over of
security review finding 7 (javascript:/data: links, HTML, backticks in snippets)."""

import json
import re
from pathlib import Path

import pytest

from pir.markdown import code, esc, esc_block, render_markdown, safe_url
from pir.models.report import AdvisoryLink, Remediation, ScanReport, SourceLocation

REPORTS = Path(__file__).resolve().parents[2] / "web" / "fixtures" / "reports"


def load(name: str) -> ScanReport:
    """A worked-example report; the prototype fixtures predate `StepStatus.tools`, so add it."""
    raw = json.loads((REPORTS / f"{name}.json").read_text("utf-8"))
    for step in raw["steps"]:
        step.setdefault("tools", [])
    return ScanReport.model_validate_json(json.dumps(raw))


def positions(text: str, needles: list[str], start: int = 0) -> list[int]:
    found = []
    for needle in needles:
        i = text.find(needle, start)
        assert i >= 0, f"missing {needle!r}"
        found.append(i)
        start = i + len(needle)
    return found


# ---- AC-62: content and UI order ------------------------------------------------------------


def test_ac62_every_finding_has_its_parts_in_ui_order():
    report = load("reachable")
    md = render_markdown(report)
    # Sections: summary, findings, method.
    positions(md, ["## Summary", "## Findings", "## Method and limitations"])
    heads = [m.start() for m in re.finditer(r"^### \d+\. ", md, re.M)]
    assert len(heads) == len(report.findings) == 6
    chunks = [
        md[a:b]
        for a, b in zip(
            heads,
            [
                *heads[1:],
                md.index("## Method"),
            ],
            strict=True,
        )
    ]
    for n, (finding, chunk) in enumerate(zip(report.findings, chunks, strict=True), 1):
        assert chunk.startswith(f"### {n}. {finding.verdict}: ")
        needles = ["**Evidence**"]
        if finding.vulnerable_symbol and finding.vulnerable_symbol.identified:
            needles.append("Vulnerable function: `" + finding.vulnerable_symbol.symbols[0])
        for step in finding.call_path or []:
            needles.append(f"`{step.file}:{step.line}`")
        needles += [f"`{loc.file}:{loc.line}`" for loc in finding.source_locations[:1]]
        needles += [f"<{finding.advisory_links[0].url}>"]
        needles += [f"**Confidence:** {finding.confidence}"]
        if finding.reason:
            needles.append("**Reason:**")
        needles += ["**Limitations**", "**Explanation**"]
        positions(chunk, needles)
        for link in finding.advisory_links:
            assert f"<{link.url}>" in chunk
        for loc in finding.source_locations:
            assert f"`{loc.file}:{loc.line}`" in chunk
        for text in finding.limitations:
            assert esc(text) in chunk
    reachable = chunks[0]
    assert "- Call path:" in reachable
    assert "(vulnerable call)" in reachable and "(entry)" in reachable
    assert "heuristic call" in chunks[1]


def test_findings_keep_the_ranked_order_of_the_report():
    report = load("reachable")
    md = render_markdown(report)
    order = [m.group(1) for m in re.finditer(r"^### \d+\. ([a-z ]+):", md, re.M)]
    assert order == [f.verdict for f in report.findings]
    assert order[0] == "likely reachable" and order[-1] == "no reachable path detected"


def test_uncertain_finding_shows_reason_and_not_identified_function():
    md = render_markdown(load("reachable"))
    assert "Vulnerable function: not identified (vulnerable function not identified" in md
    assert "**Reason:** vulnerable function not identified" in md
    assert "(vulnerable\\_function\\_not\\_identified)" in md


def test_not_checked_finding_and_unexamined_list():
    md = render_markdown(load("partial-failure"))
    assert re.search(r"^### \d+\. uncertain: pillow 8\.2\.0 \(not checked\)$", md, re.M)
    assert "no advisory was retrieved" in md.lower()
    positions(md, ["## Findings", "## Dependencies not examined", "## Method and limitations"])
    assert "- pillow 8.2.0: not checked. OSV did not answer" in md


def test_incomplete_and_clean_outcomes():
    incomplete = render_markdown(load("incomplete"))
    assert "- Outcome: incomplete" in incomplete
    assert "Not a clean result" in incomplete
    assert "## Findings" not in incomplete
    assert "- internal\\-metrics" not in incomplete  # a hyphen inside a word needs no escape
    assert "- internal-metrics: unresolved. Declared as a git dependency" in incomplete
    assert "- legacy\\_orm" not in incomplete and "- legacy-orm: unresolved." in incomplete
    assert "Versions were compiled with uv pip compile, not locked." in incomplete

    clean = render_markdown(load("clean"))
    assert "- Outcome: clean" in clean
    assert "No known-vulnerable dependencies were found." in clean
    assert "## Dependencies not examined" not in clean
    assert "## Findings" not in clean


def test_header_lines_and_single_trailing_newline():
    report = load("reachable")
    md = render_markdown(report)
    assert md.startswith("# ProveItReachable report: placeholder\\-demo/flask\\-notes\n") is False
    assert md.startswith("# ProveItReachable report: placeholder-demo/flask-notes\n")
    assert f"- Repository: <{report.repo.url}>" in md
    assert f"- Commit: `{report.repo.commit_sha}`" in md
    assert "- Generated: 2026-10-12T09:14:52Z by ProveItReachable" in md
    assert md.endswith("\n") and not md.endswith("\n\n")


# ---- security carry-over (finding 7) --------------------------------------------------------


def hostile_report() -> ScanReport:
    report = load("reachable")
    first = report.findings[0]
    links = [
        AdvisoryLink.model_construct(url="javascript:alert(1)", source="tavily", title="click"),
        AdvisoryLink.model_construct(
            url="data:text/html,<script>x</script>", source="osv", title=None
        ),
        AdvisoryLink.model_construct(url="https://ok.example/a b", source="osv", title="space"),
        AdvisoryLink.model_construct(
            url="https://good.example/advisory?id=1",
            source="osv",
            title="<img src=x onerror=alert(1)>",
        ),
    ]
    locs = [
        SourceLocation.model_construct(
            file="app/x.py",
            line=3,
            kind="call",
            snippet="a = `evil` + ``` ```\n# [x](javascript:y)",
        )
    ]
    advisory = first.advisory.model_copy(update={"summary": "<script>alert(1)</script>"})
    finding = first.model_copy(
        update={
            "advisory": advisory,
            "advisory_links": links,
            "source_locations": locs,
            "explanation": (
                "<script>alert(1)</script>\n# Heading\n- bullet\n1. one\n    code\n"
                "[click](javascript:alert(1)) ![i](https://evil.example/p.png) "
                "https://bare.example/x www.bare.example"
            ),
            "limitations": ["<b>bold</b> *star* _under_ `tick`"],
        }
    )
    return report.model_copy(update={"findings": [finding]})


def test_javascript_and_data_links_are_never_written_as_links():
    md = render_markdown(hostile_report())
    outside_code = re.sub(r"`[^`]*`", "", md.split("**Explanation**")[0])
    assert "javascript:" not in outside_code and "data:text" not in outside_code
    assert "<javascript:" not in md and "<data:" not in md
    plain = re.sub(r"`[^`]*`", "", md)  # code spans are inert; links and text must not carry them
    assert "](javascript:" not in plain and "](data:" not in plain
    assert md.count("(link omitted: not a safe http(s) URL)") == 3
    assert "<https://good.example/advisory?id=1>" in md


def test_html_in_untrusted_text_is_escaped():
    md = render_markdown(hostile_report())
    assert "<script" not in md and "<img" not in md and "<b>" not in md
    assert "&lt;img src=x onerror=alert\\(1\\)&gt;" in md
    assert "&lt;script&gt;alert\\(1\\)&lt;/script&gt;" in md
    # only the autolinks this module writes may contain a raw "<"
    for m in re.finditer(r"<[^>\n]*>", md):
        assert re.fullmatch(r"<https?://[^\s<>]+>", m.group(0)), m.group(0)


def test_explanation_cannot_open_structures_or_links():
    md = render_markdown(hostile_report())
    block = md.split("**Explanation**")[1].split("## Method")[0]
    lines = [ln for ln in block.splitlines() if ln.strip()]
    assert not any(ln.startswith(("#", "- ", "1.", "    ")) for ln in lines)
    assert "](javascript:" not in block and "\\[click\\]" in block
    assert "https://" not in block and "www." not in block.replace("www\\.", "")
    assert "!\\[" in block  # the image marker lost its bracket syntax


def test_backticks_in_snippets_cannot_break_the_code_span():
    md = render_markdown(hostile_report())
    row = next(ln for ln in md.splitlines() if "`app/x.py:3`" in ln)
    spans = re.findall(r"`+", row)
    assert all(len(s) == 1 for s in spans)  # no run of two or more backticks anywhere
    assert len(spans) % 2 == 0
    assert "a = evil +  # [x](javascript:y)".replace("  ", " ") in row.replace("  ", " ")


def test_helpers():
    assert code("a`b``c") == "`abc`"
    assert code("a\nb\t c") == "`a b c`"
    assert code("``") == "`(empty)`"
    assert esc("a\n\nb") == "a b"
    assert esc("# x") == "\\# x"
    assert esc("2. x", start=True) == "2\\. x"
    assert esc("5.3.1") == "5.3.1"
    assert esc_block("a\n- b") == "a\n\\- b"
    assert esc("x\x00y\x1b") == "xy"
    assert esc("a_b*c") == "a\\_b\\*c"


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://example.com/a?b=1#c", True),
        ("http://example.com", True),
        ("javascript:alert(1)", False),
        ("JAVASCRIPT:alert(1)", False),
        ("data:text/html;base64,AAAA", False),
        ("https://", False),
        ("https:///x", False),
        ("https://exa mple.com", False),
        ("https://example.com/>x", False),
        ("https://example.com/\n", False),
        ("ftp://example.com", False),
        ("//example.com", False),
        ("", False),
        ("https://example.com/" + "a" * 2100, False),
    ],
)
def test_safe_url(url, ok):
    assert (safe_url(url) is not None) is ok


# ---- AC-85: remediation guidance markdown rendering (ADR-0044) -----------------------------


def test_ac85_markdown_renders_remediation_guidance():
    report = load("reachable")
    first = report.findings[0]
    rem = Remediation(
        package=first.package,
        target_version="5.4",
        guidance=f"Upgrade {first.package} to version 5.4 or newer.",
    )
    with_rem = first.model_copy(update={"remediation": rem})
    rep_with_rem = report.model_copy(update={"findings": [with_rem, *report.findings[1:]]})

    md = render_markdown(rep_with_rem)
    assert f"**Remediation:** Upgrade {first.package} to version 5.4 or newer." in md
    positions(md, [f"**Confidence:** {first.confidence}", "**Remediation:**", "**Limitations**"])


def test_ac85_markdown_escapes_remediation_guidance_safely():
    report = load("reachable")
    first = report.findings[0]
    rem = Remediation(
        package=first.package,
        target_version=None,
        guidance="Inspect <script>alert(1)</script> fix commit at https://github.com/foo/bar/commit/123",
    )
    with_rem = first.model_copy(update={"remediation": rem})
    rep_with_rem = report.model_copy(update={"findings": [with_rem]})

    md = render_markdown(rep_with_rem)
    assert "<script>" not in md
    assert "&lt;script&gt;alert\\(1\\)&lt;/script&gt;" in md
