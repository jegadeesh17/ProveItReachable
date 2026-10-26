"""The prototype's mock data follows the frozen contract exactly (ARCHITECTURE section 17).

Every `web/fixtures` report, event, config and demo file validates against the Pydantic models; the
Markdown fixtures are what `render_markdown` writes; `errors.json` carries the exact API messages
(AC-43). Fast: no server, no browser.
"""

import json
import re
import string
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from pir import messages
from pir.markdown import render_markdown
from pir.models.api import AppConfigResponse, DemoSummary, ProgressEvent
from pir.models.recording import RecordedEvent
from pir.models.report import ScanReport
from pir.replay import ReplayStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "web" / "fixtures"
SCENARIOS = ["reachable", "clean", "incomplete", "partial-failure"]
TERMINAL = {"done", "failed", "skipped"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_report(name: str) -> ScanReport:
    return ScanReport.model_validate_json(
        (FIXTURES / "reports" / f"{name}.json").read_text("utf-8")
    )


# ---- reports --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", SCENARIOS)
def test_ac55_report_fixture_validates_against_scan_report(name):
    report = load_report(name)
    assert [s.name for s in report.steps] == [
        "resolve",
        "osv",
        "research",
        "analysis",
        "reasoning",
        "report",
    ]


@pytest.mark.parametrize("name", SCENARIOS)
def test_report_steps_list_tools_per_the_step_tools_rule(name):
    report = load_report(name)
    allowed = {
        "resolve": [["sandbox", "lockfile"], ["sandbox", "uv"], ["sandbox"], []],
        "osv": [["osv"], []],
        "research": [["tavily", "nemotron"], []],
        "analysis": [["sandbox", "ast"], ["sandbox"], []],
        "reasoning": [["nemotron", "ast"], []],
        "report": [["verdict_gate"], []],
    }
    for step in report.steps:
        assert list(step.tools) in allowed[step.name], (name, step.name, step.tools)
        if step.state == "skipped":
            assert step.tools == []


def test_sandbox_is_listed_only_where_the_step_used_one():
    """The UI claims Sandbox only when `sandbox` is in `tools`; skipped steps never list it."""
    clean = load_report("clean")
    assert all("sandbox" not in s.tools for s in clean.steps if s.state == "skipped")
    assert any("sandbox" in s.tools for s in load_report("reachable").steps)


@pytest.mark.parametrize("name", SCENARIOS)
def test_markdown_fixture_is_what_the_renderer_writes(name):
    """The .md fixture is the real export of the .json fixture (AC-62)."""
    expected = render_markdown(load_report(name))
    actual = (FIXTURES / "reports" / f"{name}.md").read_text(encoding="utf-8")
    assert actual == expected


def test_fixtures_cover_each_report_outcome():
    outcomes = {name: load_report(name).outcome for name in SCENARIOS}
    assert outcomes == {
        "reachable": "findings",
        "clean": "clean",
        "incomplete": "incomplete",
        "partial-failure": "findings",
    }


def test_proof_report_fixture_validates():
    report = load_report("proof")
    assert report.outcome == "findings"
    assert any(f.proof is not None for f in report.findings)


# ---- events ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", SCENARIOS)
def test_ac41_event_fixture_validates_against_progress_event(name):
    items = read_json(FIXTURES / "events" / f"{name}.json")
    events = [RecordedEvent.model_validate_json(json.dumps(item)) for item in items]
    assert [e.event.seq for e in events] == list(range(1, len(events) + 1))
    offsets = [e.offset_ms for e in events]
    assert offsets == sorted(offsets)
    assert events[-1].event.type == "done"
    assert events[-1].event.outcome == load_report(name).outcome
    assert all(isinstance(e.event, ProgressEvent) for e in events)


@pytest.mark.parametrize("name", SCENARIOS)
def test_event_tools_match_the_report_steps(name):
    report = load_report(name)
    by_step = {s.name: list(s.tools) for s in report.steps}
    items = read_json(FIXTURES / "events" / f"{name}.json")
    seen_terminal = set()
    for item in items:
        event = ProgressEvent.model_validate_json(json.dumps(item["event"]))
        assert event.scan_id == report.scan_id
        if event.type == "step" and event.state in TERMINAL:
            assert list(event.tools) == by_step[event.step], (name, event.step)
            seen_terminal.add(event.step)
        else:
            assert event.tools == []
    assert seen_terminal == set(by_step)


def test_invalid_event_is_rejected():
    """An event without `tools`, or with an unknown tool, does not validate (strict models)."""
    item = read_json(FIXTURES / "events" / "reachable.json")[0]["event"]
    without_tools = {k: v for k, v in item.items() if k != "tools"}
    with pytest.raises(ValueError):
        ProgressEvent.model_validate_json(json.dumps(without_tools))
    with pytest.raises(ValueError):
        ProgressEvent.model_validate_json(json.dumps({**item, "tools": ["curl"]}))


# ---- config and demos -----------------------------------------------------------------------


def test_ac64_config_fixture_validates_with_all_limits_and_flags():
    config = AppConfigResponse.model_validate_json((FIXTURES / "config.json").read_text("utf-8"))
    assert set(config.limits) == {
        "max_repo_mb",
        "max_repo_files",
        "live_scans_per_client_hour",
        "live_scans_per_client_day",
    }
    assert set(config.features) == {"progress_feed", "markdown_export", "proof"}
    assert config.eval is None  # the UI shows no accuracy figure without a scored eval run


def test_ac46_demos_fixture_is_the_real_recordings():
    adapter = TypeAdapter(list[DemoSummary])
    demos = adapter.validate_json((FIXTURES / "demos.json").read_text("utf-8"))
    recorded = ReplayStore(ROOT / "demo" / "replays").list()
    assert [d.model_dump(mode="json") for d in demos] == [
        d.model_dump(mode="json") for d in recorded
    ]
    assert {d.slug for d in demos} == {"planesweeppose", "pyrl", "co2tools"}
    assert not any("placeholder" in d.slug or "placeholder" in d.title for d in demos)


# ---- errors ---------------------------------------------------------------------------------


def _pattern(template: str) -> re.Pattern[str]:
    """The template as a regex: literal text escaped, each `{field}` any text."""
    out = ""
    for literal, field, _spec, _conv in string.Formatter().parse(template):
        out += re.escape(literal)
        if field is not None:
            out += "(?P<%s>.+?)" % field
    return re.compile(out)


def test_ac43_errors_fixture_has_every_code_with_the_exact_message():
    table = read_json(FIXTURES / "errors.json")
    assert set(table) == set(messages.ERRORS)
    assert {"demo_not_found", "too_many_requests"} <= set(table)
    for code, spec in messages.ERRORS.items():
        entry = table[code]
        assert entry["status"] == spec.status, code
        error = entry["error"]
        assert error["code"] == code
        match = _pattern(spec.template).fullmatch(error["message"])
        assert match, f"{code}: {error['message']!r} does not fit {spec.template!r}"
        # Fields the API also returns as `details` must agree with the text.
        for field, value in error["details"].items():
            if field in match.groupdict():
                assert match.group(field) == str(value), (code, field)


def test_errors_fixture_texts_are_the_literal_templates_when_there_are_no_fields():
    table = read_json(FIXTURES / "errors.json")
    for code, spec in messages.ERRORS.items():
        if "{" not in spec.template:
            assert table[code]["error"]["message"] == spec.template


def test_wrong_message_would_be_caught():
    template = messages.ERRORS["demo_not_found"].template
    assert _pattern(template).fullmatch("That demo does not exist.")
    assert not _pattern(template).fullmatch("That demo does not exist")


@pytest.mark.parametrize("code", ["rate_limited", "too_many_requests", "server_busy"])
def test_429_fixtures_carry_a_wait_time(code):
    error = read_json(FIXTURES / "errors.json")[code]["error"]
    assert isinstance(error["retry_after_s"], int) and error["retry_after_s"] > 0
