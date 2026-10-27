"""Static checks on `web/` (AC-60, ARCHITECTURE section 17, ADR-0026): untrusted text never reaches
the DOM as HTML, nothing is fetched from another origin, and the fonts are self-hosted. Fast."""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
JS_FILES = sorted(WEB.glob("js/**/*.js"))
CSS_FILES = sorted(WEB.glob("css/*.css"))
TEXT_FILES = [WEB / "index.html", *CSS_FILES, *JS_FILES, *sorted(WEB.glob("fixtures/**/*"))]

BANNED_JS = {
    "innerHTML": r"\binnerHTML\b",
    "outerHTML": r"\bouterHTML\b",
    "insertAdjacentHTML": r"\binsertAdjacentHTML\b",
    "document.write": r"\bdocument\s*\.\s*write(?:ln)?\s*\(",
    "eval(": r"\beval\s*\(",
    "new Function": r"\bnew\s+Function\b",
}
FONTS = [
    "Geist-Regular.woff2",
    "Geist-Medium.woff2",
    "Geist-SemiBold.woff2",
    "GeistMono-Regular.woff2",
    "GeistMono-Medium.woff2",
]


def test_js_files_are_found():
    names = {p.name for p in JS_FILES}
    assert {
        "app.js",
        "render.js",
        "export.js",
        "http.js",
        "mock.js",
        "errors.js",
        "index.js",
    } <= names


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.relative_to(WEB).as_posix())
def test_ac60_js_never_parses_html_or_evaluates_strings(path):
    text = path.read_text(encoding="utf-8")
    hits = [name for name, pattern in BANNED_JS.items() if re.search(pattern, text)]
    assert not hits, f"{path.name} uses {hits}"


def test_ban_list_would_catch_each_pattern():
    samples = {
        "innerHTML": "node.innerHTML = x;",
        "outerHTML": "n.outerHTML = x",
        "insertAdjacentHTML": "n.insertAdjacentHTML('beforeend', x)",
        "document.write": "document.write(x)",
        "eval(": "eval (code)",
        "new Function": "new Function('return 1')",
    }
    for name, sample in samples.items():
        assert re.search(BANNED_JS[name], sample), name
    assert not re.search(BANNED_JS["eval("], "retrieval(x)")


# A URL the browser would load: a URL attribute, or CSS url() / @import.
_HTML_REF = re.compile(
    r"""\b(?:href|src|srcset|action|formaction|poster|data|ping|manifest)\s*=\s*["']?\s*(?:https?:)?//""",
    re.I,
)
_CSS_REF = re.compile(r"""(?:url\(\s*["']?|@import\s+["']?)\s*(?:https?:)?//""", re.I)


def test_ac60_index_html_loads_nothing_from_another_origin():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert not _HTML_REF.search(html)
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert not re.search(r"<link[^>]+rel=[\"']?preconnect", html, re.I)
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html, re.I), (
        "no inline script (CSP script-src 'self')"
    )
    assert not re.search(r"<style\b", html, re.I), "no inline style element (CSP style-src 'self')"


@pytest.mark.parametrize("path", CSS_FILES, ids=lambda p: p.name)
def test_css_references_no_http_or_https_url(path):
    css = path.read_text(encoding="utf-8")
    assert not re.search(r"https?:", css, re.I)
    assert not _CSS_REF.search(css)


def test_reference_patterns_would_catch_remote_urls():
    assert _HTML_REF.search('<link rel="stylesheet" href="https://fonts.googleapis.com/css2">')
    assert _HTML_REF.search('<script src="//cdn.example/x.js"></script>')
    assert not _HTML_REF.search('<input placeholder="https://github.com/owner/repo">')
    assert _CSS_REF.search('src: url("https://example.com/f.woff2")')
    assert not _CSS_REF.search('src: url("../fonts/Geist-Regular.woff2")')


def test_fonts_are_self_hosted_with_their_licence():
    fonts = WEB / "fonts"
    for name in FONTS:
        data = (fonts / name).read_bytes()
        assert data[:4] == b"wOF2", f"{name} is not a woff2 file"
        assert len(data) > 10_000
    licence = (fonts / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in licence
    assert sorted(p.name for p in fonts.iterdir()) == sorted([*FONTS, "OFL.txt"])


def test_tokens_css_declares_each_font_file_with_font_display_swap():
    css = (WEB / "css" / "tokens.css").read_text(encoding="utf-8")
    faces = re.findall(r"@font-face\s*\{[^}]*\}", css)
    assert len(faces) == len(FONTS)
    for name in FONTS:
        assert f'url("../fonts/{name}") format("woff2")' in css
    assert all("font-display: swap" in face for face in faces)


@pytest.mark.parametrize("path", TEXT_FILES, ids=lambda p: p.relative_to(WEB).as_posix())
def test_web_text_files_use_lf_and_have_no_nul(path):
    if not path.is_file():
        pytest.skip("directory")
    data = path.read_bytes()
    assert b"\x00" not in data
    assert b"\r" not in data


def test_stage_tools_come_from_the_api_not_a_client_table():
    """STAGES has no tool column; 'Sandbox' is only the display name of the `sandbox` tool."""
    render = (WEB / "js" / "render.js").read_text(encoding="utf-8")
    stages = re.search(r"export const STAGES = \[(.*?)\];", render, re.S)
    assert stages
    rows = re.findall(r"\[(.*?)\],?\n", stages.group(1))
    assert rows and all(row.count('"') == 6 for row in rows), (
        "each stage is [key, label, short label]"
    )
    assert "Sandbox" not in (WEB / "index.html").read_text(encoding="utf-8")


def test_ac86_native_svg_callgraph_in_render_js():
    """AC-86 (ADR-0044): Native inline SVG call-path diagram, accessible fallback,
    and remediation."""
    render = (WEB / "js" / "render.js").read_text(encoding="utf-8")
    assert "renderCallPathSvg" in render
    assert "renderCallPath" in render
    assert "renderRemediation" in render
    assert 'svgEl("svg"' in render
    assert 'class: "callgraph-svg"' in render
    assert "viewBox" in render
    # No external charting libraries
    assert not re.search(r"\b(d3|mermaid|chart\.js|cytoscape)\b", render, re.I)
    # Accessible text fallback is preserved
    assert 'class: "callpath"' in render
    # Copy guidance button interacts with clipboard
    assert "navigator.clipboard.writeText" in render
    assert "Copy guidance" in render


def test_ac86_callgraph_and_remediation_css():
    """AC-86 (ADR-0044): Call graph responsive styling, dark mode support, and remediation."""
    app_css = (WEB / "css" / "app.css").read_text(encoding="utf-8")
    tokens_css = (WEB / "css" / "tokens.css").read_text(encoding="utf-8")

    assert ".callgraph-svg" in app_css
    assert ".callgraph-node" in app_css
    assert ".callgraph-edge" in app_css
    assert ".ev-remediation" in app_css
    assert ".remediation__badge" in app_css
    assert ".btn--copy" in app_css

    # Zero hard-coded hex colors in app.css (token discipline)
    filtered_css = app_css.replace("#banner", "").replace("#explain", "")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", filtered_css)

    # Dark mode tokens support
    assert "@media (prefers-color-scheme: dark)" in tokens_css
