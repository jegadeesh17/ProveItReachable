"""Browser flows against the real API (slow).

AC-40 to AC-46, AC-49, AC-54 to AC-59, AC-61 to AC-64, NFR-10, plus the polish checks: widths of
390 and 1280 px, long text, keyboard use, visible focus and reduced motion. Test names carry the
criterion they cover.
"""

# ruff: noqa: E501 (the embedded browser scripts read better on one line)

import json
import re
import time
from pathlib import Path

import pytest

from pir import messages

pytestmark = pytest.mark.slow

STEPS = ["resolve", "osv", "research", "analysis", "reasoning", "report"]
RANK = {"likely reachable": 0, "uncertain": 1, "no reachable path detected": 2}
REPO_URL = "https://github.com/octo/{name}"
EVAL_MODEL = "nvidia/nemotron-3-super-120b-a12b"

OVERFLOW_JS = """
() => {
  const vw = document.documentElement.clientWidth;
  const out = [];
  if (document.documentElement.scrollWidth > vw) out.push('page scrolls sideways: ' + document.documentElement.scrollWidth + ' > ' + vw);
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.right <= vw + 1) continue;
    let p = el.parentElement, clipped = false;
    while (p && p !== document.body) {
      if (getComputedStyle(p).overflowX !== 'visible') { clipped = true; break; }
      p = p.parentElement;
    }
    if (!clipped) out.push(el.tagName + '.' + el.className + ' right=' + Math.round(r.right));
  }
  return out.slice(0, 8);
}
"""
FOCUS_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const cs = getComputedStyle(el);
  const outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
  const ring = cs.boxShadow !== 'none';
  const r = el.getBoundingClientRect();
  const inline = el.tagName === 'A' && !!el.closest('p, li > span, dd');
  return { inline, id: el.tagName + (el.id ? '#' + el.id : '') + ' ' + (el.textContent || '').trim().slice(0, 30), visible: outline || ring, w: r.width, h: r.height };
}
"""
OPEN_ALL_JS = "() => document.querySelectorAll('.finding__toggle[aria-expanded=false]').forEach((b) => b.click())"


def expand_all(page) -> None:
    page.evaluate(OPEN_ALL_JS)


def overflow(page) -> list[str]:
    return page.evaluate(OVERFLOW_JS)


def report_of(server, ui) -> dict:
    return server.api(f"/api/scans/{ui.scan_id()}/report").json()


def live_scan(server, ui_factory, name: str, **kw):
    ui = ui_factory(server.url, **kw).goto()
    ui.scan(REPO_URL.format(name=name))
    ui.wait_report()
    return ui


# ---- AC-46: the demo list, its loading and failure states ------------------------------------


def test_ac46_demo_list_comes_from_the_api(server, ui_factory):
    ui = ui_factory(server.url).goto()
    demos = server.api("/api/demos").json()
    buttons = ui.page.locator("button[data-demo]")
    assert buttons.count() == len(demos) == 3
    for demo, button in zip(demos, buttons.all(), strict=True):
        assert button.get_attribute("data-demo") == demo["slug"]
        assert demo["title"] in button.inner_text()
        assert "Recorded" in button.inner_text()
    assert ui.problems() == []


def test_ac46_demo_list_shows_a_skeleton_then_the_rows(server, ui_factory):
    ui = ui_factory(server.url)

    held = []
    ui.page.route("**/api/demos", lambda route: held.append(route))  # hold the answer back
    ui.page.goto(server.url + "/")
    assert ui.page.locator(".demo--skel").count() == 3
    assert ui.page.locator("#demo-list").get_attribute("aria-busy") == "true"
    held[0].continue_()
    ui.page.locator("button[data-demo]").first.wait_for()
    assert ui.page.locator(".demo--skel").count() == 0
    assert ui.page.locator("#demo-list").get_attribute("aria-busy") == "false"


def test_ac46_demo_list_failure_says_what_happened_and_can_retry(server, ui_factory):
    ui = ui_factory(server.url)
    calls = {"n": 0}

    def flaky(route):
        calls["n"] += 1
        route.abort() if calls["n"] == 1 else route.continue_()

    ui.page.route("**/api/demos", flaky)
    ui.page.goto(server.url + "/")
    notice = ui.page.locator("#demo-list .notice--error")
    notice.wait_for()
    assert "The demo list did not load" in notice.inner_text()
    assert ui.page.locator("#scan-submit").is_disabled()  # no half-working form
    ui.page.get_by_role("button", name="Try again").click()
    ui.page.locator("button[data-demo]").first.wait_for()
    assert ui.page.locator("#scan-submit").is_enabled()


# ---- AC-40, AC-41, AC-54 to AC-57, AC-59: a live scan from the form -------------------------


def test_ac40_ac41_a_live_scan_runs_from_the_form_to_the_report(server, ui_factory):
    ui = ui_factory(server.url).goto()
    ui.scan(REPO_URL.format(name="demo"))
    ui.page.locator("#run").wait_for()
    assert ui.page.locator("#track .stage").count() == 6
    ui.wait_report()

    # AC-41: every step is pending, then running, then done, in pipeline order.
    log = ui.stage_log()
    assert [s for s, st in log[:6]] == STEPS and {st for _, st in log[:6]} == {"pending"}
    first = {entry: log.index(entry) for entry in set(log)}
    for step in STEPS:
        assert first[(step, "running")] < first[(step, "done")]
    running = [first[(step, "running")] for step in STEPS]
    assert running == sorted(running)

    # AC-40: the report is on the same screen, with the repo and the verdict.
    assert ui.page.locator("#scan-head h1").inner_text().replace("\n", "") == "octo/demo"
    assert ui.page.locator("#report [data-verdict='likely reachable']").count() == 1
    assert ui.page.locator(".track .stage[data-state=done]").count() == 6
    assert ui.problems() == []


def test_ac54_ranking_follows_the_report_and_likely_reachable_comes_first(server, ui_factory):
    ui = live_scan(server, ui_factory, "partial")
    shown = ui.page.locator("#report article.finding").evaluate_all(
        "els => els.map(e => e.dataset.verdict)"
    )
    expected = [f["verdict"] for f in report_of(server, ui)["findings"]]
    assert shown == expected and shown[0] == "likely reachable"
    assert [RANK[v] for v in shown] == sorted(RANK[v] for v in shown)


def test_ac55_evidence_comes_before_confidence_limitations_and_explanation(server, ui_factory):
    ui = live_scan(server, ui_factory, "demo")
    order = ui.page.evaluate(
        """() => {
          const body = document.querySelector('.finding__body');
          const q = (s) => body.querySelector(s);
          const seq = ['.ev-fn', '.ev-path', '.ev-links', '.conf', '.assessment .bullets', '.explanation'].map(q);
          return seq.map((n, i) => i === 0 || !!(seq[i - 1].compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING));
        }"""
    )
    assert order == [True] * 6
    path = ui.page.locator(".ev-path .where").all_inner_texts()
    assert path == ["app.py:5"]  # the call path carries file:line


def test_ac56_ac44_an_uncertain_finding_is_labelled_and_gives_its_reason(server, ui_factory):
    ui = live_scan(server, ui_factory, "partial")
    card = ui.page.locator("article[data-verdict='uncertain']")
    assert card.count() == 1
    assert "finding--uncertain" in card.get_attribute("class")
    assert "uncertain" in card.locator(".flag").inner_text()  # the word, not colour alone
    assert "Reason: Nebius request failed" in card.inner_text()
    card.locator(".finding__toggle").click()
    assert "Why this is uncertain" in card.inner_text()
    assert card.locator(".reason-box p").first.inner_text().strip() != ""
    assert ui.problems() == []


def test_ac44_a_failed_osv_step_makes_a_not_checked_finding_and_the_page_survives(
    server, ui_factory
):
    ui = live_scan(server, ui_factory, "osvdown")
    card = ui.page.locator("article[data-verdict='uncertain']")
    assert "Reason: OSV did not answer" in card.inner_text()
    assert "not checked" in card.inner_text()
    assert ui.page.locator(".track .stage[data-state=failed]").count() >= 1
    assert ui.problems() == []


def test_ac57_advisory_links_are_exact_new_tab_and_http_only(server, ui_factory):
    ui = live_scan(server, ui_factory, "partial")
    expand_all(ui.page)
    report = report_of(server, ui)
    wanted = {link["url"] for f in report["findings"] for link in f["advisory_links"]}
    links = ui.page.locator("#report .finding__body a")
    assert links.count() >= len(wanted) > 0
    found = set()
    for link in links.all():
        href = link.get_attribute("href")
        assert re.match(r"https?://", href)
        assert link.get_attribute("target") == "_blank"
        assert link.get_attribute("rel") == "noopener noreferrer"
        found.add(href)
    assert found == wanted  # the exact strings from the report


def test_ac59_one_screen_from_scan_to_export_without_navigating(server, ui_factory):
    ui = live_scan(server, ui_factory, "demo")
    with ui.page.expect_download():
        ui.page.get_by_role("link", name="Download JSON").click()
    ui.page.get_by_role("button", name="New scan").click()
    ui.run_demo("co2tools")
    ui.wait_report()
    assert ui.navigations == 1  # only the first load
    assert ui.page.url == server.url + "/"


# ---- AC-43: each error code, shown verbatim --------------------------------------------------

TOO_LARGE = messages.repo_too_large(max_mb=50, max_files=5000, mb=120.5, files=9000)
ERRORS = [
    ("not a url", messages.message("invalid_url")),
    ("https://example.com/octo/demo", messages.message("invalid_url")),
    (REPO_URL.format(name="missing"), messages.message("repo_not_found")),
    (REPO_URL.format(name="huge"), TOO_LARGE),
    (REPO_URL.format(name="notpython"), messages.not_python_project("no .py files")),
    (REPO_URL.format(name="ghdown"), messages.message("github_unavailable")),
]


@pytest.mark.parametrize(("url", "message"), ERRORS, ids=lambda v: str(v)[:24])
def test_ac43_an_intake_error_shows_the_api_message_verbatim(server, ui_factory, url, message):
    ui = ui_factory(server.url).goto()
    ui.scan(url)
    notice = ui.page.locator(".notice--error")
    notice.first.wait_for()
    assert notice.first.locator(".notice__lead").inner_text() == message
    body = ui.page.inner_text("body")
    assert "Traceback" not in body and 'File "' not in body
    assert ui.page.locator("#report").is_hidden()  # nothing half-drawn
    assert ui.page.locator("#repo-url").input_value() == url  # the user's input is kept
    assert ui.page.locator("#scan-submit").is_enabled() or "ghdown" in url
    assert ui.problems(allow_resource_errors=True) == []


def test_ac43_too_large_also_shows_the_cap_values_as_facts(server, ui_factory):
    ui = ui_factory(server.url).goto()
    ui.scan(REPO_URL.format(name="huge"))
    facts = ui.page.locator(".notice__facts")
    facts.wait_for()
    text = facts.inner_text()
    text = text.replace(" ", " ")
    assert "50 MB or 5000 files" in text and "120.5 MB in 9000 files" in text


def test_ac43_an_invalid_address_is_flagged_on_the_field(server, ui_factory):
    ui = ui_factory(server.url).goto()
    field = ui.page.get_by_label("GitHub repository URL")
    field.fill("github.com/octo")
    field.blur()
    ui.page.locator("#live-notice .notice--error").wait_for()
    assert field.get_attribute("aria-invalid") == "true"
    assert "live-notice" in field.get_attribute("aria-describedby")
    field.fill("")
    ui.page.get_by_role("button", name="Scan repo").click()
    assert ui.page.locator("#live-notice .notice__lead").inner_text() == messages.message(
        "invalid_url"
    )


def test_ac43_rate_limited_locks_the_form_but_demos_still_run(limited_server, ui_factory):
    ui = live_scan(limited_server, ui_factory, "demo")
    ui.page.get_by_role("button", name="New scan").click()
    ui.scan(REPO_URL.format(name="demo"))
    lead = ui.page.locator("#live-notice .notice__lead")
    lead.wait_for()
    assert re.fullmatch(
        r"Rate limited: you can start another live scan in (?:1 h|\d+ min)\. Demo replays still work\.",
        lead.inner_text(),
    )
    assert ui.page.locator("#scan-submit").is_disabled()
    ui.run_demo("co2tools")
    ui.wait_report()
    assert "co2tools" in ui.page.locator("#scan-head h1").inner_text()


@pytest.mark.parametrize(
    ("code", "retry_after_s", "message_minutes", "expected_duration"),
    [
        ("rate_limited", 40860, 681, "11 h 21 min"),
        ("daily_limit", 2400, None, "40 min"),
    ],
)
def test_ac43_daily_limit_explains_budget_used_up_with_duration(
    server, ui_factory, code, retry_after_s, message_minutes, expected_duration
):
    ui = ui_factory(server.url).goto()
    err_body = {
        "code": code,
        "message": (
            f"Rate limited: you can start another live scan in {message_minutes} min. Demo replays still work."
            if message_minutes
            else "Daily limit reached"
        ),
        "details": {"minutes": message_minutes} if message_minutes else {},
        "retry_after_s": retry_after_s,
    }
    ui.page.route(
        re.compile(r"/api/scans$"),
        lambda route: route.fulfill(
            status=429,
            headers={"Content-Type": "application/json", "Retry-After": str(retry_after_s)},
            body=json.dumps({"error": err_body}),
        ),
    )
    ui.scan(REPO_URL.format(name="demo"))
    lead = ui.page.locator("#live-notice .notice__lead")
    lead.wait_for()
    assert lead.inner_text() == (
        f"Today's live-scan budget is used up. It resets in {expected_duration} (00:00 UTC). "
        "Demo replays still work."
    )
    assert ui.page.locator("#scan-submit").is_disabled()
    ui.run_demo("co2tools")
    ui.wait_report()
    assert "co2tools" in ui.page.locator("#scan-head h1").inner_text()


def test_ac43_live_scans_paused_keeps_replays_and_says_so(paused_server, ui_factory):
    ui = ui_factory(paused_server.url).goto()
    assert "Live scans" in ui.page.locator("#live-notice").inner_text()
    assert (
        ui.page.locator("#repo-url").is_disabled() and ui.page.locator("#scan-submit").is_disabled()
    )
    assert ui.page.locator(".eval-figure").count() == 0  # no eval file, no figure
    ui.run_demo("pyrl")
    ui.wait_report()
    assert ui.problems() == []


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (429, "server_busy", messages.message("server_busy")),
        (503, "live_scans_paused", messages.message("live_scans_paused")),
        (500, "internal_error", messages.internal_error("abc123")),
    ],
)
def test_ac43_other_start_errors_show_their_message(server, ui_factory, status, code, message):
    ui = ui_factory(server.url).goto()
    ui.fail(re.compile(r"/api/scans$"), status, code, message)
    ui.scan(REPO_URL.format(name="demo"))
    ui.page.locator(".notice--error, .notice--info").nth(0).wait_for()
    assert message in ui.page.inner_text("body")
    assert "abc123" in message or "Traceback" not in ui.page.inner_text("body")


def test_ac43_a_report_that_fails_to_load_is_an_error_not_a_blank_screen(server, ui_factory):
    ui = ui_factory(server.url).goto()
    message = messages.internal_error("e1f2a3")
    ui.fail(re.compile(r"/api/scans/[^/]+/report$"), 500, "internal_error", message)
    ui.run_demo("co2tools")
    ui.page.locator("#banner .notice--error").wait_for()
    assert message in ui.page.locator("#banner").inner_text()
    assert ui.page.locator("#report").is_hidden()
    assert ui.page.locator("#repo-url").is_enabled()  # the user can go on


# ---- AC-42, AC-64: switches off --------------------------------------------------------------


def test_ac42_ac64_with_the_feed_and_markdown_off_the_ui_shows_one_busy_state(
    flags_off_server, ui_factory
):
    ui = ui_factory(flags_off_server.url).goto()
    ui.run_demo("co2tools")
    assert ui.page.locator("#busy-box").is_visible()
    assert ui.page.locator("#track").is_hidden()
    ui.wait_report()
    assert ui.page.locator("#busy-box").is_hidden()
    assert not any(url.endswith("/events") for url in ui.requests)  # no stream was opened
    labels = ui.page.locator("#export-slot a").all_inner_texts()
    assert [t.strip() for t in labels] == ["Download JSON"]
    assert flags_off_server.api(f"/api/scans/{ui.scan_id()}/report.md").status_code == 404
    assert ui.problems(allow_resource_errors=True) == []


def test_ac42_a_live_scan_with_the_feed_off_ends_in_the_same_report(flags_off_server, ui_factory):
    ui = live_scan(flags_off_server, ui_factory, "demo")
    assert ui.page.locator("#report [data-verdict='likely reachable']").count() == 1


# ---- the stream falls back to polling --------------------------------------------------------


def test_ac41_a_refused_stream_falls_back_to_polling_and_still_ends_in_the_report(
    server, ui_factory
):
    ui = ui_factory(server.url).goto()
    message = messages.too_many_requests(1)
    ui.fail(
        re.compile(r"/api/scans/[^/]+/events$"),
        429,
        "too_many_requests",
        message,
        **{"Retry-After": "60"},
    )
    ui.run_demo("co2tools")
    ui.wait_report(timeout=40_000)
    assert ui.page.locator(".track .stage[data-state=done]").count() >= 5
    assert ui.problems(allow_resource_errors=True) == []


def test_a_scan_that_expired_during_polling_says_so(server, ui_factory):
    ui = ui_factory(server.url).goto()
    gone = messages.message("scan_not_found")
    ui.fail(re.compile(r"/api/scans/[^/]+/events$"), 404, "scan_not_found", gone)
    ui.fail(re.compile(r"/api/scans/[^/]+$"), 404, "scan_not_found", gone)
    ui.run_demo("co2tools")
    ui.page.locator("#banner .notice--error").wait_for()
    assert gone in ui.page.locator("#banner").inner_text()
    assert ui.page.locator("#report").is_hidden()


# ---- AC-45, AC-58: clean and unresolved ------------------------------------------------------


def test_ac58_a_clean_scan_says_so_and_how_many_dependencies_were_checked(server, ui_factory):
    ui = live_scan(server, ui_factory, "clean")
    report = report_of(server, ui)
    assert report["outcome"] == "clean"
    funnel = ui.page.locator(".funnel--clean")
    text = funnel.inner_text()
    assert "No known-vulnerable dependencies were found" in text
    checked = report["summary"]["dependencies_checked"]
    assert f"All {checked} dependencies were checked" in text
    assert ui.page.locator("#report article.finding").count() == 0
    assert ui.problems() == []


def test_ac45_unresolved_dependencies_are_listed_with_their_reasons(server, ui_factory):
    ui = live_scan(server, ui_factory, "incomplete")
    report = report_of(server, ui)
    assert report["outcome"] == "incomplete"
    assert "Not a clean result" in ui.page.locator(".funnel--incomplete").inner_text()
    section = ui.page.locator("#not-examined")
    unresolved = [d for d in report["dependencies"] if not d["resolved"]]
    assert unresolved
    for dep in unresolved:
        row = section.locator("tbody tr", has_text=dep["name"])
        assert "unresolved" in row.inner_text()
        assert dep["unresolved_reason"] in row.inner_text()


# ---- AC-61 to AC-63: exports -----------------------------------------------------------------


def test_ac61_the_downloaded_json_is_identical_to_get_report(server, ui_factory):
    ui = live_scan(server, ui_factory, "partial")
    with ui.page.expect_download() as info:
        ui.page.get_by_role("link", name="Download JSON").click()
    download = info.value
    assert re.fullmatch(r"proveitreachable-partial-ddddddd\.json", download.suggested_filename)
    served = server.api(f"/api/scans/{ui.scan_id()}/report").content
    assert Path(download.path()).read_bytes() == served


def test_ac62_the_markdown_has_every_finding_in_the_order_of_the_page(server, ui_factory):
    ui = live_scan(server, ui_factory, "partial")
    with ui.page.expect_download() as info:
        ui.page.get_by_role("link", name="Download Markdown").click()
    md = Path(info.value.path()).read_text(encoding="utf-8")
    assert info.value.suggested_filename.endswith(".md")
    headings = re.findall(r"^### \d+\. (.+?): (\S+) (\S+) \(", md, re.M)
    dom = ui.page.locator("#report article.finding").evaluate_all(
        "els => els.map(e => e.getAttribute('aria-label'))"
    )
    assert [f"{v}: {p} {ver}" for v, p, ver in headings] == dom
    report = report_of(server, ui)
    for finding in report["findings"]:
        assert finding["confidence"] in md
        for link in finding["advisory_links"]:
            assert link["url"] in md
    lr = next(f for f in report["findings"] if f["verdict"] == "likely reachable")
    assert f"`{lr['call_path'][0]['file']}:{lr['call_path'][0]['line']}`" in md  # the call path
    assert "Limitations" in md


def test_ac63_a_replay_exports_both_formats_with_no_live_calls(server, ui_factory):
    h = server.harness
    before = (len(h.osv_transport.requests), len(h.tavily_transport.requests), len(h.llm.calls))
    ui = ui_factory(server.url).goto()
    ui.run_demo("co2tools")
    ui.wait_report()
    for name in ("Download JSON", "Download Markdown"):
        with ui.page.expect_download() as info:
            ui.page.get_by_role("link", name=name).click()
        assert Path(info.value.path()).stat().st_size > 500
    after = (len(h.osv_transport.requests), len(h.tavily_transport.requests), len(h.llm.calls))
    assert after == before


# ---- AC-49 / NFR-10: a judge reaches the report from a demo ---------------------------------


def test_ac49_nfr10_a_replay_at_its_real_pace_reaches_the_report_in_under_180_seconds(
    paced_server, ui_factory
):
    ui = ui_factory(paced_server.url).goto()
    started = time.monotonic()
    ui.run_demo("planesweeppose")  # one click, no instructions beyond the screen
    ui.wait_report(timeout=170_000)
    elapsed = time.monotonic() - started
    assert elapsed < 180
    assert ui.page.locator("#report article.finding").count() == 30
    assert ui.problems() == []


# ---- the eval figure's wording ---------------------------------------------------------------


def test_the_eval_figure_says_what_the_precision_is_over(server, ui_factory):
    ui = ui_factory(server.url).goto()
    text = ui.page.locator(".eval-figure").inner_text()
    assert text.startswith("Likely-reachable precision 100%")
    assert "called likely reachable" in text  # precision is over those predictions
    assert "14 labelled cases" in text and "25 Oct 2026" in text
    assert EVAL_MODEL in text
    assert "not overall accuracy" in text
    assert "measured on" not in text


# ---- the usage line and the evaluation line in "How this report was made" (ADR-0045) ---------

TRACE = "https://smith.langchain.com/public/6f1c2a9e-4b7d-4e0a-9c35-8d2b1f7a3e64/r"


def plain(text: str) -> str:
    """Collapse the no-break spaces the page uses, so wording can be compared."""
    return re.sub(r"\s+", " ", text).strip()


def usage_report(server, ui_factory, mutate=None, *, width: int = 1280, eval_run=None):
    """The partial-failure mock report, which carries `usage`, optionally changed on the way in.

    `eval_run` is served as the API config's scored evaluation run (`config.eval`).
    """
    ui = ui_factory(server.url, width=width)

    def config(route) -> None:
        response = route.fetch()
        data = response.json()
        data["eval"] = eval_run
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        route.fulfill(response=response, headers=headers, body=json.dumps(data))

    if eval_run is not None:
        ui.page.route(re.compile(r"/fixtures/config\.json$"), config)

    def handle(route) -> None:
        response = route.fetch()
        data = response.json()
        if mutate:
            mutate(data)
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        route.fulfill(response=response, headers=headers, body=json.dumps(data))

    ui.page.route(re.compile(r"/fixtures/reports/partial-failure\.json$"), handle)
    ui.page.goto(f"{server.url}/?api=mock&scenario=partial-failure&jump=report")
    ui.wait_report()
    return ui


def test_adr0045_the_usage_line_shows_what_the_scan_used_and_links_the_trace(server, ui_factory):
    ui = usage_report(server, ui_factory)
    line = ui.page.locator(".pipeline__usage")
    assert plain(line.inner_text()) == (
        "Nemotron 14 calls · 41k tokens · Tavily 9 searches · Sandbox 4 runs"
        " · OSV 12 queries · $0.10 metered model + search spend"
        " · View trace in LangSmith (opens in a new tab)"
    )
    link = line.get_by_role("link", name=re.compile("View trace in LangSmith"))
    assert link.get_attribute("href") == TRACE
    assert link.get_attribute("target") == "_blank"
    assert link.get_attribute("rel") == "noopener noreferrer"
    assert ui.problems() == []


def test_adr0045_a_zero_count_is_left_out_of_the_usage_line(server, ui_factory):
    def mutate(data):
        data["usage"]["sandbox"]["operations"] = 0
        data["usage"]["tavily"] = {"searches": 1, "usd": 0}
        data["usage"]["trace_url"] = None

    ui = usage_report(server, ui_factory, mutate)
    text = plain(ui.page.locator(".pipeline__usage").inner_text())
    assert "Sandbox" not in text and "Tavily 1 search " in text
    assert "$0.07 metered model + search spend" in text
    assert ui.page.locator(".pipeline__usage a").count() == 0


@pytest.mark.parametrize("scenario", ["reachable", "clean"])
def test_adr0045_a_report_without_usage_shows_no_usage_line(server, ui_factory, scenario):
    ui = ui_factory(server.url)
    ui.page.goto(f"{server.url}/?api=mock&scenario={scenario}&jump=report")
    ui.wait_report()
    assert ui.page.locator(".pipeline__usage").count() == 0
    assert ui.page.locator(".pipeline__eval").count() == 0  # no eval run loaded, no eval line


def test_adr0045_a_recorded_replay_shows_no_usage_line(server, ui_factory):
    ui = ui_factory(server.url).goto()
    ui.run_demo("pyrl")
    ui.wait_report()
    assert ui.page.locator(".pipeline__usage").count() == 0


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:window.__pwned=1",
        "http://smith.langchain.com/public/abc/r",
        "https://evil.example/public/abc/r",
        "https://smith.langchain.com.evil.example/public/abc/r",
        "https://evilsmith.langchain.com/public/abc/r",
        "https://user:pw@smith.langchain.com/public/abc/r",
        "https://smith.langchain.com/o/abc/projects",
        "https://smith.langchain.com:8443/public/abc/r",
        "https://smith.langchain.com/x/public/abc/r",
        "https://a.b.smith.langchain.com/public/abc/r",
        "https://smith.langchain.com/public/abc/r?token=1",
        "https://smith.langchain.com/public/abc/r#frag",
        "not a url",
        "",
    ],
)
def test_adr0045_an_unsafe_trace_url_renders_no_link(server, ui_factory, bad):
    ui = usage_report(server, ui_factory, lambda d: d["usage"].update(trace_url=bad))
    assert plain(ui.page.locator(".pipeline__usage").inner_text()).startswith("Nemotron 14 calls")
    assert ui.page.locator(".pipeline__usage a").count() == 0
    assert "View trace" not in ui.page.locator("#report").inner_text()


def test_adr0045_a_subdomain_of_the_trace_host_is_accepted(server, ui_factory):
    url = "https://eu.smith.langchain.com/public/abc/r"
    ui = usage_report(server, ui_factory, lambda d: d["usage"].update(trace_url=url))
    assert ui.page.locator(".pipeline__usage a").get_attribute("href") == url


EVAL_RUN = {
    "scored": True,
    "run_date": "2026-10-29",
    "model": "nvidia/nemotron-3-super-120b-a12b",
    "n": 30,
    "precision": 0.9091,
}


def test_adr0045_the_evaluation_line_says_what_it_was_measured_on(server, ui_factory):
    ui = usage_report(server, ui_factory, eval_run=EVAL_RUN)
    text = plain(ui.page.locator(".pipeline__eval").inner_text())
    # Rounded down, like the front door: 0.9091 is 90%, never 91%.
    assert text == "Evaluated on 30 labelled cases · likely-reachable precision 90%"
    assert "LangSmith" not in text
    front_door = plain(ui.page.locator(".eval-figure").inner_text())
    assert "precision 90%" in front_door


def test_adr0045_no_eval_run_means_no_evaluation_line(server, ui_factory):
    ui = usage_report(server, ui_factory)
    assert ui.page.locator(".pipeline__usage").count() == 1
    assert ui.page.locator(".pipeline__eval").count() == 0
    assert "Evaluated on" not in ui.page.locator("#report").inner_text()


@pytest.mark.parametrize("bad", [{"n": "30", "precision": 0.9}, {"n": 30, "precision": 1.5}])
def test_adr0045_an_unusable_eval_run_renders_no_evaluation_line(server, ui_factory, bad):
    ui = usage_report(server, ui_factory, eval_run={**EVAL_RUN, **bad})
    assert ui.page.locator(".pipeline__eval").count() == 0


def test_adr0045_the_evaluation_line_agrees_with_the_front_door(server, ui_factory):
    ui = ui_factory(server.url).goto()
    ui.run_demo("co2tools")
    ui.wait_report()
    line = plain(ui.page.locator(".pipeline__eval").inner_text())
    assert line == "Evaluated on 14 labelled cases · likely-reachable precision 100%"
    assert "precision 100%" in plain(ui.page.locator(".eval-figure").inner_text())


@pytest.mark.parametrize("width", [375, 390])
def test_adr0045_likely_reachable_precision_never_breaks_across_lines(server, ui_factory, width):
    ui = usage_report(server, ui_factory, width=width, eval_run=EVAL_RUN)
    figure = ui.page.locator(".pipeline__eval-figure")
    assert figure.evaluate("e => e.getClientRects().length") == 1


def test_adr0045_both_powered_by_lines_name_sandboxes_and_langsmith(server, ui_factory):
    ui = ui_factory(server.url).goto()
    for selector in (".powered--head", ".powered--foot"):
        text = plain(ui.page.locator(selector).text_content())
        assert text == "Powered by Nebius Token Factory · Nemotron · Sandboxes · Tavily · LangSmith"


@pytest.mark.parametrize("width", [375, 1280])
def test_adr0045_the_usage_and_evaluation_lines_fit_without_sideways_scroll(
    server, ui_factory, width
):
    ui = usage_report(server, ui_factory, width=width)
    assert ui.page.locator(".pipeline__usage").is_visible()
    assert overflow(ui.page) == []


# ---- proof (optional feature) renders read-only ----------------------------------------------


@pytest.mark.parametrize("status", ["proven", "not_proven"])
def test_a_proof_on_a_finding_renders_as_read_only_text(server, ui_factory, status):
    def add_proof(report):
        lr = next(f for f in report["findings"] if f["verdict"] == "likely reachable")
        lr["proof"] = {
            "status": status,
            "reason": "The entry point reached the vulnerable call when run.",
            "ran_at": "2026-10-25T10:00:00Z",
            "entry": "app:load_config",
            "target": "yaml.full_load",
            "recorded_call": "yaml.full_load('a: 1')",
        }

    ui = ui_factory(server.url).goto()
    ui.patch_report(add_proof)
    ui.scan(REPO_URL.format(name="demo"))
    ui.wait_report()
    proof = ui.page.locator(".ev-proof")
    assert proof.get_attribute("data-proof") == status
    assert status.replace("_", " ") in proof.locator(".proof-status").inner_text()
    assert "app:load_config" in proof.inner_text() and "yaml.full_load" in proof.inner_text()
    assert proof.locator("button, a, input, select, textarea").count() == 0  # read-only
    assert (
        ui.page.locator("#report").get_by_role("button", name=re.compile("prove", re.I)).count()
        == 0
    )
    assert ui.problems() == []


# ---- polish: widths, long text, keyboard, focus, motion --------------------------------------

LIVE_VIEWS = ["demo", "partial", "osvdown", "clean", "incomplete"]
MOCK_VIEWS = [
    "error-invalid-url",
    "error-not-found",
    "error-too-large",
    "error-not-python",
    "rate-limited",
    "paused",
    "demo-not-found",
    "too-many-requests",
    "partial-failure",
    "incomplete",
]


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("name", LIVE_VIEWS)
def test_polish_live_reports_do_not_break_the_layout(server, ui_factory, name, width):
    ui = live_scan(server, ui_factory, name, width=width)
    expand_all(ui.page)
    assert overflow(ui.page) == []


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("slug", ["planesweeppose", "pyrl", "co2tools"])
def test_polish_committed_replays_do_not_break_the_layout(server, ui_factory, slug, width):
    ui = ui_factory(server.url, width=width).goto()
    ui.run_demo(slug)
    ui.wait_report()
    expand_all(ui.page)
    assert overflow(ui.page) == []
    assert ui.problems() == []


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("scenario", MOCK_VIEWS)
def test_polish_every_mock_scenario_fits(server, ui_factory, scenario, width):
    ui = ui_factory(server.url, width=width)
    ui.page.goto(f"{server.url}/?api=mock&scenario={scenario}&jump=report")
    ui.page.locator(".notice, #report .funnel").first.wait_for()
    expand_all(ui.page)
    assert overflow(ui.page) == []


@pytest.mark.parametrize("width", [390, 1280])
def test_polish_the_idle_screen_and_an_over_long_address_fit(server, ui_factory, width):
    ui = ui_factory(server.url, width=width).goto()
    assert overflow(ui.page) == []
    long_url = "https://github.com/octo/" + "a" * 400
    ui.scan(long_url)
    ui.page.locator(".notice--error").first.wait_for()
    assert ui.page.locator("#repo-url").input_value() == long_url
    assert overflow(ui.page) == []


def long_text(report: dict) -> None:
    unbroken = "x" * 160
    report["repo"]["name"] = "repository-" + "n" * 80
    for dep in report["dependencies"]:
        dep["name"] = "dependency-" + "d" * 90
    for f in report["findings"]:
        f["package"] = "package-" + "p" * 90
        f["version"] = "1.0.0+" + "v" * 70
        f["explanation"] = unbroken * 8
        f["limitations"] = [unbroken * 3, "short"]
        for step in f["call_path"] or []:
            step["file"] = "src/" + "dir/" * 30 + "file.py"
            step["symbol"] = "module." + "s" * 120
        for loc in f["source_locations"] or []:
            loc["file"] = "src/" + "dir/" * 30 + "file.py"
            loc["snippet"] = "call(" + "a" * 300 + ")"
        for link in f["advisory_links"]:
            link["url"] = "https://example.org/" + "u" * 500
            link["title"] = "T" * 200
        if f["advisory"]:
            f["advisory"]["summary"] = unbroken * 10
            f["advisory"]["id"] = "GHSA-" + "i" * 100
            f["advisory"]["fixed_versions"] = ["9.9.9+" + "f" * 90]


@pytest.mark.parametrize("width", [390, 1280])
def test_polish_very_long_names_urls_and_text_do_not_break_the_layout(server, ui_factory, width):
    ui = ui_factory(server.url, width=width).goto()
    ui.patch_report(long_text)
    ui.scan(REPO_URL.format(name="partial"))
    ui.wait_report()
    expand_all(ui.page)
    assert overflow(ui.page) == []
    # Nothing is clipped either: every opened finding fits its own box.
    clipped = ui.page.locator(".finding__body").evaluate_all(
        "els => els.filter(e => e.scrollWidth > e.clientWidth + 1).length"
    )
    assert clipped == 0
    assert ui.problems() == []


def test_polish_a_whole_replay_can_be_run_and_read_with_the_keyboard_alone(server, ui_factory):
    ui = ui_factory(server.url).goto()
    page = ui.page
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip"
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "main"

    page.locator("button[data-demo=pyrl]").focus()
    page.keyboard.press("Enter")
    ui.wait_report()
    assert page.evaluate("document.activeElement.id") == "scan-h"  # focus moved to the run

    toggle = page.locator(".finding__toggle").nth(1)
    toggle.focus()
    assert toggle.get_attribute("aria-expanded") == "false"
    page.keyboard.press("Enter")
    assert toggle.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Space")
    assert toggle.get_attribute("aria-expanded") == "false"

    page.get_by_role("button", name="New scan").focus()
    page.keyboard.press("Enter")
    field = page.get_by_label("GitHub repository URL")
    field.focus()
    page.keyboard.type(REPO_URL.format(name="clean"))
    page.keyboard.press("Enter")
    page.locator(".funnel--clean").wait_for()
    assert ui.problems() == []


@pytest.mark.parametrize("width", [390, 1280])
def test_polish_every_control_shows_a_visible_focus_ring(server, ui_factory, width):
    ui = ui_factory(server.url, width=width).goto()
    page = ui.page
    seen: list[str] = []

    def walk(limit: int) -> None:
        for _ in range(limit):
            page.keyboard.press("Tab")
            info = page.evaluate(FOCUS_JS)
            if info is None:
                return
            assert info["visible"], f"no focus ring on {info['id']}"
            assert info["inline"] or (info["h"] >= 24 and info["w"] >= 24), (
                f"target too small: {info['id']}"
            )
            seen.append(info["id"])

    walk(40)  # idle: skip link, field, button, demos, new-scan
    ui.run_demo("co2tools")
    ui.wait_report()
    page.locator("#scan-h").focus()
    walk(60)  # report: exports, findings, details
    assert len(set(seen)) >= 12
    assert any("Download JSON" in s for s in seen) and any("PlaneSweepPose" in s for s in seen)


def test_polish_reduced_motion_removes_every_animation_and_transition(server, ui_factory):
    probe = """() => {
      const spin = document.querySelector('.stage[data-state=running] .stage__node svg');
      const row = document.querySelector('.stage');
      return { spin: spin && getComputedStyle(spin).animationName, transition: getComputedStyle(row).transitionDuration };
    }"""
    results = {}
    for reduced in (False, True):
        ui = ui_factory(server.url, reduced_motion=reduced)
        ui.page.goto(f"{server.url}/?api=mock&scenario=reachable&speed=3")
        ui.page.locator("button[data-demo]").first.wait_for()
        ui.run_demo("planesweeppose")
        ui.page.locator(".stage[data-state=running]").first.wait_for()
        results[reduced] = ui.page.evaluate(probe)
        ui.wait_report(timeout=60_000)
        strip = ui.page.evaluate(
            "getComputedStyle(document.querySelector('.strip__item')).animationName"
        )
        results[reduced]["strip"] = strip
    assert (
        results[False]["spin"] != "none" and results[False]["strip"] != "none"
    )  # the probe can fail
    assert results[True]["spin"] == "none" and results[True]["strip"] == "none"
    assert results[True]["transition"] == "0s"


def test_polish_tool_chips_arriving_do_not_shift_the_pipeline_rows(server, ui_factory):
    for width in (390, 1280):
        ui = ui_factory(server.url, width=width)
        ui.page.goto(f"{server.url}/?api=mock&scenario=reachable&speed=0.25")
        ui.page.locator("button[data-demo]").first.wait_for()
        ui.run_demo("planesweeppose")
        ui.page.locator(".stage[data-step=report][data-state=pending]").wait_for()
        shift = ui.page.evaluate(
            """() => {
              const row = document.querySelector('.stage[data-step=report]');
              const slot = row.querySelector('.stage__tool');
              const before = row.getBoundingClientRect().height;
              const chip = document.createElement('span');
              chip.className = 'tool';
              chip.textContent = 'Sandbox \\u00b7 Static analysis';
              slot.append(chip);
              return row.getBoundingClientRect().height - before;
            }"""
        )
        assert abs(shift) < 0.5, f"a tool chip moved the row by {shift}px at {width}px"


# ---- the report lands well after the scan's `done` event --------------------------------------

NOT_READY = {
    "error": {
        "code": "report_not_ready",
        "message": "The report is not ready yet; it appears when the scan finishes.",
        "details": {},
        "retry_after_s": None,
    }
}
# The waits between retries are seconds long. Run every timer of 1 s or more 100 times faster, so the
# whole schedule (about 90 s) takes about a second; the budget is counted from the planned gaps.
FAST_TIMERS_JS = """
(() => {
  const real = window.setTimeout.bind(window);
  window.setTimeout = (fn, ms, ...args) => real(fn, ms >= 1000 ? ms / 100 : ms, ...args);
})();
"""
WATCH_JS = """
(() => {
  window.__seen = { waitText: null, live: null, error: false };
  new MutationObserver(() => {
    const wait = document.querySelector('.report-wait');
    if (wait) {
      window.__seen.waitText = wait.innerText;
      window.__seen.live = wait.getAttribute('aria-live');
    }
    if (document.querySelector('.notice--error')) window.__seen.error = true;
  }).observe(document, { subtree: true, childList: true, characterData: true });
})();
"""
WAIT_TEXT = "Finishing the report \u2014 recording spend and saving the trace\u2026"


def scan_with_late_report(server, ui_factory, answer):
    """Start a live scan whose report requests are answered by `answer(call_number)`.

    `answer` returns None to let the real report through, or (status, error body).
    """
    ui = ui_factory(server.url)
    ui.page.add_init_script(FAST_TIMERS_JS)
    ui.page.add_init_script(WATCH_JS)
    calls: list[int] = []

    def handle(route) -> None:
        calls.append(1)
        reply = answer(len(calls))
        if reply is None:
            route.continue_()
        else:
            status, body = reply
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    ui.page.route(re.compile(r"/api/scans/[^/]+/report$"), handle)
    ui.goto()
    ui.scan(REPO_URL.format(name="demo"))
    return ui, calls


def test_report_not_ready_eight_times_then_the_report_renders_with_a_calm_status(
    server, ui_factory
):
    ui, calls = scan_with_late_report(
        server, ui_factory, lambda n: (409, NOT_READY) if n <= 8 else None
    )
    ui.wait_report()
    assert len(calls) == 9
    assert ui.page.locator("#report [data-verdict='likely reachable']").count() == 1
    seen = ui.page.evaluate("window.__seen")
    assert seen["waitText"] is not None and WAIT_TEXT in seen["waitText"]
    assert seen["live"] == "polite"
    assert seen["error"] is False
    assert ui.page.locator(".report-wait").count() == 0  # the status goes when the report arrives
    assert ui.page.locator("#banner .notice").count() == 0
    assert ui.problems(allow_resource_errors=True) == []


def test_report_not_ready_for_the_whole_budget_ends_in_the_error(server, ui_factory):
    ui, calls = scan_with_late_report(server, ui_factory, lambda n: (409, NOT_READY))
    ui.page.locator("#banner .notice--error").wait_for(timeout=30_000)
    assert NOT_READY["error"]["message"] in ui.page.locator("#banner").inner_text()
    assert ui.page.locator(".report-wait").count() == 0
    assert ui.page.locator("#report").is_hidden()
    assert ui.page.locator("#repo-url").is_enabled()  # the user can go on
    assert 15 <= len(calls) <= 30  # it kept trying for the whole budget, and then stopped
    settled = len(calls)
    ui.page.wait_for_timeout(500)
    assert len(calls) == settled  # no timer is left retrying


def test_any_other_report_error_fails_fast_without_the_waiting_status(server, ui_factory):
    gone = {"error": {"code": "internal_error", "message": "Something broke.", "details": {}}}
    ui, calls = scan_with_late_report(server, ui_factory, lambda n: (500, gone))
    ui.page.locator("#banner .notice--error").wait_for()
    assert "Something broke." in ui.page.locator("#banner").inner_text()
    assert len(calls) == 1
    assert ui.page.evaluate("window.__seen.waitText") is None
