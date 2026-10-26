"""Workflow checks: triggers, fast commands, no secrets, pinned actions, README badge, fingerprint.

AC-71, AC-72, NFR-11, ADR-0033 (amended 2026-10-25). The workflow files are parsed
with PyYAML; nothing runs them.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOW_DIR / "ci.yml"
FINGERPRINT_WORKFLOW = WORKFLOW_DIR / "fingerprint.yml"
README = ROOT / "README.md"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))
FAST_PYTEST = 'python -m pytest -q -m "not slow"'
REQUIRED_TRIGGERS = {"push", "pull_request"}
CI_URL = "https://github.com/jegadeesh17/ProveItReachable/actions/workflows/ci.yml"
BADGE_URL = f"{CI_URL}/badge.svg"
SHA_PINNED_USES = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}")
TAG_COMMENT = re.compile(r"#\s*v\d+(\.\d+)*\s*$")
SANDBOX_RUN_FLAGS = ("--platform linux/amd64", "--network none", "--user 65534:65534", ":ro")


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


def dispatch_is_bare(doc: dict[str, Any]) -> bool:
    # ADR-0033: the only trigger is workflow_dispatch, and it declares no inputs.
    on = doc.get("on", doc.get(True))
    if not isinstance(on, dict) or set(on) != {"workflow_dispatch"}:
        return False
    return not (on["workflow_dispatch"] or {})


def permissions_are_read_only(doc: dict[str, Any]) -> bool:
    # ADR-0033: top-level contents: read, and no job may override it.
    if doc.get("permissions") != {"contents": "read"}:
        return False
    return all("permissions" not in job for job in jobs_of(doc))


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


def commits_or_pushes(text: str) -> bool:
    lowered = text.lower()
    return "git commit" in lowered or "git push" in lowered


def docker_run_lines(doc: dict[str, Any]) -> list[str]:
    return [line for line in run_lines(doc) if line.startswith("docker run ")]


def inspect_precedes_run(doc: dict[str, Any]) -> bool:
    # ADR-0033: the digest check (imagetools inspect) must come before any docker run.
    lines = run_lines(doc)
    inspects = [i for i, line in enumerate(lines) if "docker buildx imagetools inspect" in line]
    runs = [i for i, line in enumerate(lines) if line.startswith("docker run ")]
    return bool(inspects and runs) and inspects[0] < runs[0]


def missing_sandbox_flags(line: str) -> list[str]:
    return [flag for flag in SANDBOX_RUN_FLAGS if flag not in line]


def upload_steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for step in steps_of(doc)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]


def artifact_config_ok(step: dict[str, Any]) -> bool:
    config = step.get("with") or {}
    return (
        config.get("name") == "base-image-fingerprint"
        and config.get("if-no-files-found") == "error"
        and config.get("retention-days") == 7
    )


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


# ADR-0033 (amended 2026-10-25): fingerprint.yml measures the base-image fingerprint on a runner.


def test_adr0033_fingerprint_workflow_file_exists():
    assert FINGERPRINT_WORKFLOW.is_file()


def test_adr0033_fingerprint_runs_on_workflow_dispatch_without_inputs():
    doc = parse(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8"))
    assert triggers(doc) == {"workflow_dispatch"}
    assert dispatch_is_bare(doc)


def test_adr0033_checker_flags_dispatch_with_inputs():
    doc = parse(
        "on:\n  workflow_dispatch:\n    inputs:\n      image:\n        required: true\njobs: {}\n"
    )
    assert not dispatch_is_bare(doc)


def test_adr0033_checker_flags_extra_trigger_next_to_dispatch():
    doc = parse("on:\n  workflow_dispatch:\n  push:\njobs: {}\n")
    assert not dispatch_is_bare(doc)


def test_adr0033_fingerprint_permissions_are_contents_read_only():
    doc = parse(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8"))
    assert doc["permissions"] == {"contents": "read"}
    assert permissions_are_read_only(doc)


def test_adr0033_checker_flags_write_permissions_at_either_level():
    top = parse("on:\n  workflow_dispatch:\npermissions: write-all\njobs: {}\n")
    job = parse(
        "on:\n  workflow_dispatch:\npermissions:\n  contents: read\n"
        "jobs:\n  measure:\n    runs-on: ubuntu-latest\n"
        "    permissions:\n      contents: write\n    steps: []\n"
    )
    assert not permissions_are_read_only(top)
    assert not permissions_are_read_only(job)


def test_adr0033_fingerprint_is_one_ubuntu_job():
    doc = parse(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8"))
    assert len(jobs_of(doc)) == 1
    assert jobs_of(doc)[0]["runs-on"] == "ubuntu-latest"


def test_adr0033_fingerprint_references_no_secrets():
    text = FINGERPRINT_WORKFLOW.read_text(encoding="utf-8")
    assert secret_lines(text) == []


def test_nfr11_fingerprint_pins_carry_tag_comment():
    lines = [
        line
        for line in FINGERPRINT_WORKFLOW.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("- uses:") or line.strip().startswith("uses:")
    ]
    assert len(lines) == 3, lines
    assert all(TAG_COMMENT.search(line) for line in lines), lines


def test_nfr11_fingerprint_reuses_ci_checkout_and_setup_python_pins():
    ci_pins = {
        ref.split("@")[0]: ref for ref in uses_refs(parse(CI_WORKFLOW.read_text(encoding="utf-8")))
    }
    fingerprint_refs = uses_refs(parse(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8")))
    assert ci_pins["actions/checkout"] in fingerprint_refs
    assert ci_pins["actions/setup-python"] in fingerprint_refs


def test_adr0033_fingerprint_checkout_does_not_persist_credentials():
    doc = parse(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8"))
    checkouts = [
        step for step in steps_of(doc) if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["persist-credentials"] is False


def test_adr0033_fingerprint_never_commits_or_pushes():
    assert not commits_or_pushes(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8"))


def test_adr0033_checker_flags_commit_or_push_text():
    assert commits_or_pushes("run: git commit -m fingerprint")
    assert commits_or_pushes("run: git push origin HEAD:main")


def test_adr0033_fingerprint_reads_image_from_settings_default_only():
    text = FINGERPRINT_WORKFLOW.read_text(encoding="utf-8")
    assert 'Settings.model_fields["sandbox_base_image"].default' in text
    assert "github.event.inputs" not in text
    assert "inputs." not in text


def test_adr0033_fingerprint_uses_project_agent_builder_and_decoder():
    text = FINGERPRINT_WORKFLOW.read_text(encoding="utf-8")
    assert "build_agent_pyz" in text
    assert "from pir.sandbox_agent.protocol import" in text
    assert "decode_result" in text


def test_adr0033_digest_check_runs_before_the_docker_run():
    doc = parse(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8"))
    assert inspect_precedes_run(doc)


def test_adr0033_checker_flags_docker_run_before_digest_check():
    doc = parse(
        "on:\n  workflow_dispatch:\n"
        "jobs:\n  measure:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: docker run --rm alpine true\n"
        "      - run: docker buildx imagetools inspect alpine\n"
    )
    assert not inspect_precedes_run(doc)


def test_adr0033_fingerprint_docker_run_is_sandboxed_selfcheck():
    doc = parse(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8"))
    lines = docker_run_lines(doc)
    assert len(lines) == 1, lines
    assert missing_sandbox_flags(lines[0]) == []
    assert "/opt/pir/agent.pyz selfcheck --args-b64 e30=" in lines[0]


def test_adr0033_checker_flags_docker_run_without_sandbox_flags():
    line = "docker run --rm -v /host/agent:/opt/pir image selfcheck"
    assert missing_sandbox_flags(line) == list(SANDBOX_RUN_FLAGS)


def test_adr0033_fingerprint_uploads_base_image_fingerprint_last():
    doc = parse(FINGERPRINT_WORKFLOW.read_text(encoding="utf-8"))
    uploads = upload_steps(doc)
    assert len(uploads) == 1
    assert artifact_config_ok(uploads[0])
    assert steps_of(doc)[-1] is uploads[0]


def test_adr0033_checker_flags_artifact_that_warns_or_keeps_too_long():
    warn = {
        "uses": f"actions/upload-artifact@{'0' * 40}",
        "with": {
            "name": "base-image-fingerprint",
            "if-no-files-found": "warn",
            "retention-days": 7,
        },
    }
    long_lived = {
        "uses": f"actions/upload-artifact@{'0' * 40}",
        "with": {
            "name": "base-image-fingerprint",
            "if-no-files-found": "error",
            "retention-days": 30,
        },
    }
    assert not artifact_config_ok(warn)
    assert not artifact_config_ok(long_lived)


# NFR-13 and AC-77 (ARCHITECTURE 19.3): keepalive.yml runs the spend check on a schedule.

KEEPALIVE_WORKFLOW = WORKFLOW_DIR / "keepalive.yml"
KEEPALIVE_CRON = "17 */6 * * *"
KEEPALIVE_RUN = 'python -I src/pir/keepalive.py "$PIR_HEALTH_URL"'


def test_nfr13_keepalive_workflow_file_exists():
    assert KEEPALIVE_WORKFLOW.is_file()


def test_nfr13_keepalive_runs_on_schedule_and_workflow_dispatch_only():
    doc = parse(KEEPALIVE_WORKFLOW.read_text(encoding="utf-8"))
    assert triggers(doc) == {"schedule", "workflow_dispatch"}
    on = doc.get("on", doc.get(True))
    assert on["schedule"] == [{"cron": KEEPALIVE_CRON}]


def test_nfr13_keepalive_is_one_ubuntu_job_with_read_only_token():
    doc = parse(KEEPALIVE_WORKFLOW.read_text(encoding="utf-8"))
    assert len(jobs_of(doc)) == 1
    assert jobs_of(doc)[0]["runs-on"] == "ubuntu-latest"
    assert permissions_are_read_only(doc)


def test_nfr13_keepalive_runs_check_with_health_url_from_repository_variable():
    doc = parse(KEEPALIVE_WORKFLOW.read_text(encoding="utf-8"))
    assert KEEPALIVE_RUN in run_lines(doc)
    env_steps = [step for step in steps_of(doc) if KEEPALIVE_RUN in str(step.get("run", ""))]
    assert env_steps[0]["env"] == {"PIR_HEALTH_URL": "${{ vars.PIR_HEALTH_URL }}"}


def test_nfr13_keepalive_failure_is_not_ignored():
    text = KEEPALIVE_WORKFLOW.read_text(encoding="utf-8")
    assert "continue-on-error" not in text
    assert "|| true" not in text


def test_nfr13_keepalive_uses_python_313_and_no_secrets():
    doc = parse(KEEPALIVE_WORKFLOW.read_text(encoding="utf-8"))
    setups = [
        s for s in steps_of(doc) if str(s.get("uses", "")).startswith("actions/setup-python@")
    ]
    assert len(setups) == 1
    assert setups[0]["with"]["python-version"] == "3.13"
    assert secret_lines(KEEPALIVE_WORKFLOW.read_text(encoding="utf-8")) == []


def test_nfr13_keepalive_reuses_ci_checkout_and_setup_python_pins():
    ci_pins = {
        ref.split("@")[0]: ref for ref in uses_refs(parse(CI_WORKFLOW.read_text(encoding="utf-8")))
    }
    keepalive_refs = uses_refs(parse(KEEPALIVE_WORKFLOW.read_text(encoding="utf-8")))
    assert ci_pins["actions/checkout"] in keepalive_refs
    assert ci_pins["actions/setup-python"] in keepalive_refs


def test_nfr13_checker_flags_expression_in_keepalive_run():
    doc = parse(
        "on:\n  schedule:\n    - cron: '17 */6 * * *'\njobs:\n  k:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: python x.py ${{ vars.PIR_HEALTH_URL }}\n"
    )
    assert runs_with_expressions(doc) == ["python x.py ${{ vars.PIR_HEALTH_URL }}"]
