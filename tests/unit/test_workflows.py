"""CI workflow checks: triggers, fast commands, no secrets, pinned actions, README badge.

AC-71, AC-72, NFR-11. The workflow files are parsed with PyYAML; nothing runs them.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOW_DIR / "ci.yml"
README = ROOT / "README.md"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))
FAST_PYTEST = 'python -m pytest -q -m "not slow"'
REQUIRED_TRIGGERS = {"push", "pull_request"}
CI_URL = "https://github.com/jegadeesh17/ProveItReachable/actions/workflows/ci.yml"
BADGE_URL = f"{CI_URL}/badge.svg"
SHA_PINNED_USES = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}")
TAG_COMMENT = re.compile(r"#\s*v\d+(\.\d+)*\s*$")


def parse(text: str) -> dict[str, Any]:
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict), "a workflow must be a YAML mapping"
    return doc


def triggers(doc: dict[str, Any]) -> set[str]:
    # PyYAML (YAML 1.1) reads a bare `on:` key as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    if isinstance(on, dict):
        return set(on)
    return set()


def missing_triggers(doc: dict[str, Any]) -> set[str]:
    return REQUIRED_TRIGGERS - triggers(doc)


def jobs_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return list((doc.get("jobs") or {}).values())


def steps_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in jobs_of(doc) for step in job.get("steps") or []]


def run_lines(doc: dict[str, Any]) -> list[str]:
    lines = (str(step.get("run", "")).splitlines() for step in steps_of(doc))
    return [line.strip() for block in lines for line in block if line.strip()]


def runs_with_expressions(doc: dict[str, Any]) -> list[str]:
    return [step["run"] for step in steps_of(doc) if "${{" in str(step.get("run", ""))]


def uses_refs(doc: dict[str, Any]) -> list[str]:
    job_refs = [job["uses"] for job in jobs_of(doc) if "uses" in job]
    step_refs = [step["uses"] for step in steps_of(doc) if "uses" in step]
    return job_refs + step_refs


def unpinned_refs(doc: dict[str, Any]) -> list[str]:
    return [ref for ref in uses_refs(doc) if not SHA_PINNED_USES.fullmatch(ref)]


def secret_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if "secrets." in line]


def has_ci_badge(text: str) -> bool:
    badge_link = f"[![CI]({BADGE_URL})]({CI_URL})"
    return badge_link in text


def test_ac71_ci_workflow_file_exists():
    assert CI_WORKFLOW.is_file()


def test_ac71_ci_runs_on_push_and_pull_request():
    doc = parse(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert missing_triggers(doc) == set()
    assert triggers(doc) == REQUIRED_TRIGGERS


def test_ac71_ci_runs_ruff_and_fast_pytest():
    doc = parse(CI_WORKFLOW.read_text(encoding="utf-8"))
    commands = run_lines(doc)
    for expected in (
        'pip install -e ".[dev]"',
        "ruff check .",
        "ruff format --check .",
        FAST_PYTEST,
    ):
        assert any(expected in command for command in commands), expected


def test_ac71_ci_is_one_ubuntu_job_with_read_only_token():
    doc = parse(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert len(jobs_of(doc)) == 1
    assert jobs_of(doc)[0]["runs-on"] == "ubuntu-latest"
    assert doc["permissions"] == {"contents": "read"}


def test_ac71_ci_references_no_secrets_or_live_tests():
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert secret_lines(text) == []
    assert "PIR_LIVE_TESTS" not in text


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_ac71_no_workflow_references_secrets(path: Path):
    assert secret_lines(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_nfr11_no_expression_inside_run(path: Path):
    doc = parse(path.read_text(encoding="utf-8"))
    assert runs_with_expressions(doc) == []


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_nfr11_every_action_is_pinned_to_commit_sha(path: Path):
    doc = parse(path.read_text(encoding="utf-8"))
    assert unpinned_refs(doc) == []


def test_nfr11_ci_pins_carry_tag_comment():
    lines = [
        line
        for line in CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("- uses:") or line.strip().startswith("uses:")
    ]
    assert lines
    assert all(TAG_COMMENT.search(line) for line in lines), lines


def test_ac72_readme_has_ci_badge_linking_to_workflow():
    assert has_ci_badge(README.read_text(encoding="utf-8"))


def test_ac71_checker_flags_push_only_workflow():
    doc = parse("on: [push]\njobs: {}\n")
    assert missing_triggers(doc) == {"pull_request"}


def test_nfr11_checker_flags_tag_pinned_action():
    doc = parse(
        "on: [push, pull_request]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v7\n"
    )
    assert unpinned_refs(doc) == ["actions/checkout@v7"]


def test_nfr11_checker_flags_expression_inside_run():
    doc = parse(
        "on: [push, pull_request]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        '      - run: echo "${{ github.event.pull_request.title }}"\n'
    )
    assert runs_with_expressions(doc) == ['echo "${{ github.event.pull_request.title }}"']


def test_ac71_checker_flags_secret_reference():
    text = "env:\n  TOKEN: ${{ secrets.TOKEN }}\n"
    assert secret_lines(text) == ["TOKEN: ${{ secrets.TOKEN }}"]


def test_ac72_checker_rejects_link_without_badge_or_wrong_workflow():
    assert not has_ci_badge(f"[CI]({CI_URL})")
    wrong = "https://github.com/jegadeesh17/ProveItReachable/actions/workflows/other.yml"
    assert not has_ci_badge(f"[![CI]({wrong}/badge.svg)]({wrong})")
