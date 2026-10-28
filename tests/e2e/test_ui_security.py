"""Untrusted text and the page's security posture, in a real browser (slow).

AC-57 (links), AC-60 (text only, CSP 'self'), security review findings 7 and 9: a `javascript:` or
`data:` advisory link is never a link, advisory and model text with markup renders as text and
does not run, the Content Security Policy is enforced and nothing is loaded from another origin,
and `?scenario=` accepts allowlisted names only.
"""

# ruff: noqa: E501 (the embedded browser scripts read better on one line)

import json
import re
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.slow

REPO_URL = "https://github.com/octo/{name}"
PAYLOAD = '<script>window.__pwned = "script"</script><img src=x onerror="window.__pwned = \'img\'"><svg onload="window.__pwned = \'svg\'">'
BAD_LINKS = [
    "javascript:window.__pwned='js'",
    "JaVaScRiPt:window.__pwned='mixed-case'",
    "  javascript:window.__pwned='leading-space'",
    "data:text/html,<script>window.__pwned='data'</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "//evil.example/protocol-relative",
    "not a url at all",
]
GOOD_LINK = "https://osv.dev/vulnerability/GHSA-8q59-q68h-6hv4"


def scan_demo_repo(server, ui_factory, mutate=None, **kw):
    ui = ui_factory(server.url, **kw).goto()
    dialogs = []
    ui.page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    ui.dialogs = dialogs
    if mutate:
        ui.patch_report(mutate)
    ui.scan(REPO_URL.format(name="demo"))
    ui.wait_report()
    ui.page.evaluate(
        "() => document.querySelectorAll('.finding__toggle[aria-expanded=false]').forEach((b) => b.click())"
    )
    return ui


def pwned(ui):
    return ui.page.evaluate("window.__pwned === undefined ? null : window.__pwned")


# ---- security review finding 9 / AC-57: link schemes ----------------------------------------


def test_ac57_javascript_and_data_advisory_links_are_never_rendered_as_links(server, ui_factory):
    def add_bad_links(report):
        links = report["findings"][0]["advisory_links"]
        links[:] = [
            {"url": GOOD_LINK, "title": "OSV record", "source": "osv"},
            *(
                {"url": url, "title": f"bad link {i}", "source": "tavily"}
                for i, url in enumerate(BAD_LINKS)
            ),
        ]

    ui = scan_demo_repo(server, ui_factory, add_bad_links)
    hrefs = ui.page.locator("#report a[href]").evaluate_all(
        "els => els.map(e => e.getAttribute('href'))"
    )
    assert GOOD_LINK in hrefs
    for href in hrefs:
        assert href.startswith(("https://", "http://", "#")), f"unexpected link target: {href!r}"
    body = ui.page.locator(".ev-links").first
    assert body.get_by_text("not linked: not an http or https address").count() == len(BAD_LINKS)
    for i in range(len(BAD_LINKS)):
        label = body.get_by_text(f"bad link {i}", exact=False)
        assert label.locator("xpath=ancestor-or-self::a").count() == 0  # the title is plain text
        label.first.click()  # clicking the text does nothing
    assert pwned(ui) is None and ui.dialogs == []
    assert ui.problems() == []


# ---- AC-60: markup in report text is text ----------------------------------------------------


def hostile_report(report):
    report["repo"]["name"] = "name" + PAYLOAD
    for dep in report["dependencies"]:
        dep["name"] = dep["name"] + PAYLOAD
    for f in report["findings"]:
        f["package"] = "pkg" + PAYLOAD
        f["explanation"] = PAYLOAD
        f["limitations"] = [PAYLOAD]
        f["reason"] = PAYLOAD
        for step in f["call_path"] or []:
            step["symbol"] = PAYLOAD
            step["file"] = PAYLOAD
        for loc in f["source_locations"] or []:
            loc["snippet"] = PAYLOAD
        for link in f["advisory_links"]:
            link["title"] = PAYLOAD
        if f["advisory"]:
            f["advisory"]["summary"] = PAYLOAD
            f["advisory"]["severity"] = PAYLOAD
        if f["vulnerable_symbol"]:
            f["vulnerable_symbol"]["symbols"] = [PAYLOAD]
    for step in report["steps"]:
        step["message"] = PAYLOAD
    report["limitations"] = [PAYLOAD]


def test_ac60_script_and_img_onerror_in_report_text_render_as_text_and_do_not_run(
    server, ui_factory
):
    ui = scan_demo_repo(server, ui_factory, hostile_report)
    assert pwned(ui) is None and ui.dialogs == []
    assert (
        ui.page.locator("#report script, #report img, #report svg[onload], #scan-head img").count()
        == 0
    )
    assert ui.page.locator("#report [onerror], #report [onload]").count() == 0
    text = ui.page.locator("#report").inner_text()
    assert "<script>" in text and "onerror=" in text  # visible as characters
    assert "<script>" in ui.page.locator("#scan-head").inner_text()
    ui.page.wait_for_timeout(300)  # an image error would fire by now
    assert pwned(ui) is None
    assert ui.problems() == []


def test_ac60_markup_in_progress_messages_and_demo_titles_renders_as_text(server, ui_factory):
    ui = ui_factory(server.url)
    dialogs = []
    ui.page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))

    def hostile_demos(route):
        response = route.fetch()
        demos = response.json()
        demos[0]["title"] = "Demo" + PAYLOAD
        demos[0]["description"] = PAYLOAD
        route.fulfill(
            response=response, body=json.dumps(demos), headers={"Content-Type": "application/json"}
        )

    def hostile_status(route):
        response = route.fetch()
        data = response.json()
        for step in data["steps"]:
            step["message"] = PAYLOAD
        route.fulfill(
            response=response, body=json.dumps(data), headers={"Content-Type": "application/json"}
        )

    ui.page.route("**/api/demos", hostile_demos)
    # A refused stream makes the page poll the status route, which then carries the hostile messages.
    ui.fail(
        re.compile(r"/api/scans/[^/]+/events$"),
        429,
        "too_many_requests",
        "Too many requests right now; try again in 1 min.",
    )
    ui.page.route(re.compile(r"/api/scans/[^/]+$"), hostile_status)
    ui.patch_report(hostile_report)  # the finished track shows the report's step messages
    ui.page.goto(server.url + "/")
    first = ui.page.locator("button[data-demo]").first
    first.wait_for()
    assert "<script>" in first.inner_text()
    first.click()
    ui.wait_report(timeout=40_000)
    assert "<script>" in ui.page.locator("#track .stage__msg").first.inner_text()
    assert pwned(ui) is None and dialogs == []
    assert (
        ui.page.locator("#demo-list script, #track script, #demo-list img, #track img").count() == 0
    )


def test_ac60_markup_in_an_error_message_renders_as_text(server, ui_factory):
    ui = ui_factory(server.url).goto()
    ui.fail(re.compile(r"/api/scans$"), 400, "invalid_url", "Bad address " + PAYLOAD)
    ui.scan(REPO_URL.format(name="demo"))
    notice = ui.page.locator(".notice--error")
    notice.first.wait_for()
    assert "<script>" in notice.first.inner_text()
    assert notice.locator("script, img").count() == 0
    assert pwned(ui) is None
    assert ui.problems(allow_resource_errors=True) == []


# ---- CSP 'self' only (ADR-0026) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/js/app.js", "/css/app.css", "/api/config", "/no-such-page"]
)
def test_csp_header_is_self_only_on_pages_assets_api_and_404s(server, path):
    csp = server.api(path).headers["content-security-policy"]
    assert (
        "script-src 'self'" in csp and "default-src 'self'" in csp and "connect-src 'self'" in csp
    )
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert not re.search(
        r"https?://(?!huggingface\.co)", csp
    )  # only the Space frame ancestor is named


def test_csp_is_enforced_and_a_full_visit_makes_no_violation_and_no_foreign_request(
    server, ui_factory
):
    ui = ui_factory(server.url).goto()
    ui.run_demo("co2tools")
    ui.wait_report()
    ui.page.get_by_role("button", name="New scan").click()
    ui.scan(REPO_URL.format(name="partial"))
    ui.wait_report()
    with ui.page.expect_download():
        ui.page.get_by_role("link", name="Download Markdown").click()

    assert ui.csp_violations() == []
    assert not [
        text for _, text in ui.console if "Content Security Policy" in text or "Refused to" in text
    ]
    own = urlparse(server.url).netloc
    foreign = [
        u
        for u in ui.requests
        if urlparse(u).scheme in ("http", "https") and urlparse(u).netloc != own
    ]
    assert foreign == []
    fonts = [u for u in ui.requests if u.endswith(".woff2")]
    assert fonts and all(
        urlparse(u).netloc == own for u in fonts
    )  # the self-hosted Geist files load

    # The policy really blocks: an inline script the page did not ship does not run.
    ui.page.evaluate(
        "() => { const s = document.createElement('script'); s.textContent = 'window.__injected = 1'; document.head.append(s); }"
    )
    assert ui.page.evaluate("window.__injected === undefined")
    assert ui.csp_violations() != []


# ---- security review finding 7: ?scenario= is an allowlist -----------------------------------


@pytest.mark.parametrize(
    "scenario",
    [
        "../../../pyproject.toml",
        "reachable/../clean",
        "%2e%2e%2f%2e%2e%2fREADME",
        "..\\..\\README",
        "constructor",
        "__proto__",
        "reachable%00.json",
        "https://evil.example/x",
    ],
)
def test_scenario_names_off_the_allowlist_fall_back_and_never_reach_a_file_path(
    server, ui_factory, scenario
):
    ui = ui_factory(server.url)
    ui.page.goto(f"{server.url}/?api=mock&scenario={scenario}&jump=report")
    ui.wait_report()
    assert "Scenario: reachable." in ui.page.locator("#mock-notice-text").inner_text()
    fixtures = [u for u in ui.requests if "/fixtures/" in u]
    allowed = re.compile(
        r"/fixtures/(config|demos|errors)\.json$|/fixtures/(reports|events)/(reachable|clean|incomplete|partial-failure)\.(json|md)$"
    )
    assert fixtures and all(allowed.search(u) for u in fixtures), fixtures
    page_url = f"{server.url}/?"
    assert not [
        u
        for u in ui.requests
        if not u.startswith(page_url) and re.search(r"\.\.|%2e|pyproject|README|evil", u, re.I)
    ]
    assert ui.problems() == []


def test_a_listed_scenario_is_honoured(server, ui_factory):
    ui = ui_factory(server.url)
    ui.page.goto(f"{server.url}/?api=mock&scenario=clean&jump=report")
    ui.wait_report()
    assert "Scenario: clean." in ui.page.locator("#mock-notice-text").inner_text()
    assert ui.page.locator(".funnel--clean").count() == 1
