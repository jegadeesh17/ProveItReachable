"""Live end-to-end scan (M1-TASK-11, milestone verification): run with PIR_LIVE_TESTS=1.

Scans one small public Python repository through the real Sandbox, OSV, Tavily and Nemotron and
validates the report. Spend is printed. Run only after the M1-TASK-08 live gates have passed
(`test_uv_repo_interpreter_never_runs` and `test_base_image_fingerprint_matches`).
"""

import uuid

import pytest

from pir.config import load_settings
from pir.deps import build_deps
from pir.github import parse_repo_url
from pir.models.report import ScanReport
from pir.pipeline import run_scan
from pir.spend import Refused, print_spend_summary

pytestmark = [pytest.mark.slow, pytest.mark.live]

# A small, public, permissively licensed Python repository.
REPO = "https://github.com/psf/requests-html"


async def test_live_scan_end_to_end(capsys):
    settings = load_settings().model_copy(update={"executor": "sandbox"})
    deps = build_deps(settings)
    try:
        coords = parse_repo_url(REPO)
        target = await deps.validate(coords, None)
        scan_id = uuid.uuid4().hex
        budget = await deps.meter.reserve(scan_id)
        assert not isinstance(budget, Refused), budget
        try:
            report = await run_scan(deps, target, budget, scan_id=scan_id)
        finally:
            await deps.meter.settle(budget)
            print_spend_summary(deps.meter)
    finally:
        await deps.aclose()

    assert ScanReport.model_validate_json(report.model_dump_json()) == report
    assert [s.name for s in report.steps] == [
        "resolve",
        "osv",
        "research",
        "analysis",
        "reasoning",
        "report",
    ]
    assert report.steps[0].state == "done" and "sandbox" in report.steps[0].tools
    assert report.repo.commit_sha == target.commit_sha
    assert "spend nebius: used $" in capsys.readouterr().err
