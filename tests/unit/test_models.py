"""Strict models: NFR-05, AC-18, AC-19, report rules and the model-boundary security checks."""

import copy
import json

import pytest
from pydantic import ValidationError

from pir.models.api import (
    AppConfigResponse,
    ErrorResponse,
    HealthResponse,
    ProgressEvent,
    ScanRequest,
)
from pir.models.eval import EvalRepo
from pir.models.ledger import LedgerState
from pir.models.report import (
    Dependency,
    Finding,
    ReportSummary,
    ScanReport,
    StepStatus,
    compute_finding_id,
)

SHA = "a" * 40
NOW = "2026-10-25T12:00:00Z"
STEP_NAMES = ["resolve", "osv", "research", "analysis", "reasoning", "report"]
SCOPE = "Scope: 1 of 1 Python files parsed; examined imports of `yaml`. Not examined: reflection."


def load(model, data):
    """Validate like the API does: from JSON text (strict mode, ISO strings for datetimes)."""
    return model.model_validate_json(json.dumps(data))


def dep(name="pyyaml", version="5.3.1", **over):
    data = {
        "name": name,
        "source": "uv.lock",
        "groups": ["main"],
        "version": version,
        "resolved": True,
        "unresolved_reason": None,
        "direct": True,
        "osv_status": "checked",
        "osv_error": None,
    }
    return {**data, **over}


def finding(verdict="uncertain", **over):
    data = {
        "finding_id": compute_finding_id("pyyaml", "5.3.1", "GHSA-8q59-q68h-6hv4"),
        "kind": "advisory",
        "verdict": verdict,
        "confidence": "low",
        "package": "pyyaml",
        "version": "5.3.1",
        "advisory": {
            "id": "GHSA-8q59-q68h-6hv4",
            "aliases": ["CVE-2020-14343"],
            "summary": "Arbitrary code execution",
            "severity": "critical",
            "fixed_versions": ["5.4"],
            "published": None,
            "modified": None,
        },
        "vulnerable_symbol": {
            "identified": True,
            "symbols": ["yaml.load"],
            "not_identified_reason": None,
            "evidence_urls": ["https://osv.dev/GHSA-8q59-q68h-6hv4"],
        },
        "advisory_links": [
            {"url": "https://osv.dev/GHSA-8q59-q68h-6hv4", "source": "osv", "title": None}
        ],
        "source_locations": [],
        "call_path": None,
        "explanation": "text",
        "limitations": ["limit"],
        "reason_code": "unsupported_reachability_claim",
        "reason": "because",
    }
    if verdict == "likely reachable":
        data.update(
            confidence="high",
            reason_code=None,
            reason=None,
            source_locations=[{"file": "app.py", "line": 3, "kind": "call", "snippet": None}],
            call_path=[
                {"file": "app.py", "line": 3, "edge": "vulnerable_call", "symbol": "yaml.load"}
            ],
        )
    if verdict == "no reachable path detected":
        data.update(confidence="medium", reason_code=None, reason=None, limitations=[SCOPE])
    return {**data, **over}


def not_checked(**over):
    data = finding(
        finding_id=compute_finding_id("pyyaml", "5.3.1", None),
        kind="not_checked",
        advisory=None,
        vulnerable_symbol=None,
        advisory_links=[],
        reason_code="osv_failed",
        reason="OSV unreachable",
    )
    return {**data, **over}


def steps(state="done"):
    return [
        {
            "name": n,
            "state": state,
            "tools": [],
            "started_at": None,
            "finished_at": None,
            "message": None,
        }
        for n in STEP_NAMES
    ]


def report(findings=None, deps=None, **over):
    findings = [] if findings is None else findings
    deps = [dep()] if deps is None else deps
    by = {"likely reachable": 0, "no reachable path detected": 0, "uncertain": 0}
    for f in findings:
        by[f["verdict"]] += 1
    data = {
        "schema_version": "1.0",
        "scan_id": "0123456789abcdef0123456789abcdef",
        "tool_version": "0.1.0",
        "model": "nemotron",
        "generated_at": NOW,
        "repo": {
            "url": "https://github.com/octo/demo",
            "owner": "octo",
            "name": "demo",
            "ref_requested": None,
            "default_branch": "main",
            "commit_sha": SHA,
        },
        "outcome": "findings" if findings else "clean",
        "resolution": {
            "method": "lockfile",
            "python_version": None,
            "lockfile": "uv.lock",
            "compiled_from": None,
            "notes": [],
        },
        "steps": steps(),
        "dependencies": deps,
        "findings": findings,
        "summary": {
            "dependencies_total": len(deps),
            "dependencies_checked": sum(d["osv_status"] == "checked" for d in deps),
            "dependencies_unresolved": sum(not d["resolved"] for d in deps),
            "dependencies_not_checked": sum(d["osv_status"] == "not_checked" for d in deps),
            "findings_total": len(findings),
            "by_verdict": by,
        },
        "limitations": ["scope"],
    }
    return {**data, **over}


# NFR-05 ---------------------------------------------------------------------------------------


def test_valid_report_round_trips():  # NFR-05
    rep = load(ScanReport, report([finding()]))
    assert load(ScanReport, json.loads(rep.model_dump_json())) == rep


def test_int_as_string_is_rejected_not_coerced():  # NFR-05
    summary = report()["summary"] | {"dependencies_total": "1"}
    with pytest.raises(ValidationError):
        load(ReportSummary, summary)


def test_extra_field_is_rejected():  # NFR-05
    with pytest.raises(ValidationError):
        load(ScanReport, report(surprise=1))


def test_wrong_literal_is_rejected():  # NFR-05
    with pytest.raises(ValidationError):
        load(ScanReport, report(outcome="done"))


def test_python_objects_are_not_coerced_either():  # NFR-05
    with pytest.raises(ValidationError):
        ReportSummary(
            dependencies_total="1",
            dependencies_checked=0,
            dependencies_unresolved=0,
            dependencies_not_checked=0,
            findings_total=0,
            by_verdict={"likely reachable": 0, "no reachable path detected": 0, "uncertain": 0},
        )


def test_models_are_frozen():  # NFR-05
    rep = load(ScanReport, report())
    with pytest.raises(ValidationError):
        rep.model = "other"


# AC-18 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["Likely reachable", "reachable", "unreachable", "", "UNCERTAIN"])
def test_ac18_verdict_outside_the_three_labels_fails(label):
    with pytest.raises(ValidationError):
        load(Finding, finding(verdict=label))


@pytest.mark.parametrize("label", ["likely reachable", "no reachable path detected", "uncertain"])
def test_ac18_the_three_labels_validate(label):
    assert load(Finding, finding(verdict=label)).verdict == label


# AC-19 ----------------------------------------------------------------------------------------


def test_ac19_advisory_finding_without_links_fails():
    with pytest.raises(ValidationError, match="advisory link"):
        load(
            Finding,
            finding(
                advisory_links=[],
                vulnerable_symbol=finding()["vulnerable_symbol"] | {"evidence_urls": []},
            ),
        )


def test_ac19_advisory_finding_without_limitations_fails():
    with pytest.raises(ValidationError):
        load(Finding, finding(limitations=[]))


def test_ac19_advisory_finding_without_advisory_or_symbol_fails():
    with pytest.raises(ValidationError):
        load(Finding, finding(advisory=None))
    with pytest.raises(ValidationError):
        load(Finding, finding(vulnerable_symbol=None))


def test_ac19_missing_package_or_version_fails():
    bad = finding()
    del bad["version"]
    with pytest.raises(ValidationError):
        load(Finding, bad)
    with pytest.raises(ValidationError):
        load(Finding, finding(version="not a version"))


def test_ac19_not_checked_has_no_advisory_and_no_links():
    ok = load(Finding, not_checked())
    assert ok.kind == "not_checked" and ok.advisory is None and ok.advisory_links == []
    link = [{"url": "https://x.example/a", "source": "osv", "title": None}]
    with pytest.raises(ValidationError):
        load(Finding, not_checked(advisory_links=link))
    with pytest.raises(ValidationError):
        load(Finding, not_checked(advisory=finding()["advisory"]))


def test_evidence_urls_must_be_among_the_links():
    sym = finding()["vulnerable_symbol"] | {"evidence_urls": ["https://elsewhere.example/"]}
    with pytest.raises(ValidationError, match="subset"):
        load(Finding, finding(vulnerable_symbol=sym))


def test_vulnerable_symbol_rules():
    sym = finding()["vulnerable_symbol"]
    with pytest.raises(ValidationError):
        load(Finding, finding(vulnerable_symbol=sym | {"symbols": []}))
    unidentified = {
        "identified": False,
        "symbols": [],
        "not_identified_reason": "vulnerable function not identified: advisory is vague",
        "evidence_urls": [],
    }
    assert (
        load(Finding, finding(vulnerable_symbol=unidentified)).vulnerable_symbol.identified is False
    )
    with pytest.raises(ValidationError):
        load(Finding, finding(vulnerable_symbol=unidentified | {"not_identified_reason": "vague"}))


# Finding iff rules ----------------------------------------------------------------------------


def test_finding_id_must_match_its_inputs():
    with pytest.raises(ValidationError, match="finding_id"):
        load(Finding, finding(finding_id="0" * 16))


def test_likely_reachable_needs_call_path_and_locations():
    with pytest.raises(ValidationError):
        load(Finding, finding("likely reachable", call_path=None))
    with pytest.raises(ValidationError):
        load(Finding, finding("likely reachable", call_path=[]))
    with pytest.raises(ValidationError):
        load(Finding, finding("likely reachable", source_locations=[]))


def test_call_path_only_for_likely_reachable():
    path = finding("likely reachable")["call_path"]
    with pytest.raises(ValidationError):
        load(Finding, finding("uncertain", call_path=path))


def test_uncertain_needs_reason_and_low_confidence():
    with pytest.raises(ValidationError):
        load(Finding, finding(reason_code=None))
    with pytest.raises(ValidationError):
        load(Finding, finding(reason=None))
    with pytest.raises(ValidationError, match="low confidence"):
        load(Finding, finding(confidence="medium"))


def test_reason_only_for_uncertain():
    with pytest.raises(ValidationError):
        load(Finding, finding("likely reachable", reason_code="osv_failed", reason="x"))


def test_no_path_needs_the_scope_statement():  # AC-21
    with pytest.raises(ValidationError, match="scope"):
        load(Finding, finding("no reachable path detected", limitations=["something else"]))


def test_explanation_length_cap_and_path_traversal_in_locations():
    with pytest.raises(ValidationError):
        load(Finding, finding(explanation="x" * 2001))
    bad = [{"file": "../etc/passwd", "line": 1, "kind": "call", "snippet": None}]
    with pytest.raises(ValidationError):
        load(Finding, finding(source_locations=bad))
    with pytest.raises(ValidationError):
        load(
            Finding,
            finding(
                source_locations=[{"file": "/abs.py", "line": 1, "kind": "call", "snippet": None}]
            ),
        )
    with pytest.raises(ValidationError):
        load(
            Finding,
            finding(
                source_locations=[{"file": "a.py", "line": 0, "kind": "call", "snippet": None}]
            ),
        )


def test_advisory_aliases_sorted_and_id_charset():
    adv = finding()["advisory"]
    with pytest.raises(ValidationError):
        load(Finding, finding(advisory=adv | {"aliases": ["CVE-2", "CVE-1"]}))
    with pytest.raises(ValidationError):
        load(Finding, finding(advisory=adv | {"id": "../../x"}))


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x/y", "https://", "x" * 10])
def test_advisory_link_url_must_be_http(url):
    bad = [{"url": url, "source": "osv", "title": None}]
    with pytest.raises(ValidationError):
        load(
            Finding,
            finding(
                advisory_links=bad,
                vulnerable_symbol=finding()["vulnerable_symbol"] | {"evidence_urls": []},
            ),
        )


# Report-level rules ---------------------------------------------------------------------------


def test_steps_must_be_the_six_in_order():
    with pytest.raises(ValidationError, match="steps"):
        load(ScanReport, report(steps=steps()[:5]))
    swapped = steps()
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(ValidationError, match="steps"):
        load(ScanReport, report(steps=swapped))


def test_final_report_steps_cannot_be_running():
    with pytest.raises(ValidationError, match="final report"):
        load(ScanReport, report(steps=steps("running")))


def test_by_verdict_needs_all_three_keys():
    data = report()
    data["summary"]["by_verdict"].pop("uncertain")
    with pytest.raises(ValidationError, match="three verdict keys"):
        load(ScanReport, data)


def test_summary_must_match_content():
    data = report([finding()])
    data["summary"]["findings_total"] = 5
    with pytest.raises(ValidationError, match="findings_total"):
        load(ScanReport, data)


def test_finding_ids_must_be_unique():
    with pytest.raises(ValidationError, match="unique"):
        load(ScanReport, report([finding(), finding()]))


def test_dependencies_must_be_sorted_by_name():
    deps = [dep("zlib", "1.0"), dep("aiohttp", "3.0")]
    with pytest.raises(ValidationError, match="sorted"):
        load(ScanReport, report(deps=deps))


def test_findings_must_be_ranked():  # AC-23
    lr = finding("likely reachable")
    other = finding(
        "no reachable path detected",
        package="aaa",
        finding_id=compute_finding_id("aaa", "5.3.1", "GHSA-8q59-q68h-6hv4"),
    )
    deps = [dep("aaa"), dep("pyyaml")]
    assert (
        load(ScanReport, report([lr, other], deps=deps)).findings[0].verdict == "likely reachable"
    )
    with pytest.raises(ValidationError, match="ranked"):
        load(ScanReport, report([other, lr], deps=deps))


def test_generated_at_must_be_utc():
    with pytest.raises(ValidationError):
        load(ScanReport, report(generated_at="2026-10-25T12:00:00+02:00"))
    with pytest.raises(ValidationError):
        load(ScanReport, report(generated_at="2026-10-25T12:00:00"))


def test_scan_id_and_repo_rules():
    with pytest.raises(ValidationError):
        load(ScanReport, report(scan_id="not-a-uuid"))
    repo = report()["repo"]
    for bad in (
        repo | {"owner": "-bad"},
        repo | {"name": ".."},
        repo | {"commit_sha": "A" * 40},
        repo | {"url": "https://evil.example/octo/demo"},
    ):
        with pytest.raises(ValidationError):
            load(ScanReport, report(repo=bad))


# Outcome (AC-11) ------------------------------------------------------------------------------


def test_outcome_clean():
    assert load(ScanReport, report()).outcome == "clean"
    with pytest.raises(ValidationError, match="outcome"):
        load(ScanReport, report(outcome="incomplete"))


def test_outcome_incomplete_for_unresolved_dependency():
    unresolved = dep(
        "flask", None, resolved=False, unresolved_reason="dynamic", osv_status="not_queried"
    )
    data = report(deps=[unresolved, dep()], outcome="incomplete")
    assert load(ScanReport, data).outcome == "incomplete"
    with pytest.raises(ValidationError, match="outcome"):
        load(ScanReport, report(deps=[unresolved, dep()], outcome="clean"))


def test_outcome_incomplete_when_resolve_failed():
    broken = steps()
    broken[0]["state"] = "failed"
    assert load(ScanReport, report(steps=broken, outcome="incomplete")).outcome == "incomplete"
    with pytest.raises(ValidationError, match="outcome"):
        load(ScanReport, report(steps=broken, outcome="clean"))


def test_outcome_findings_whenever_findings_exist():
    data = report([finding()], deps=[dep()])
    assert load(ScanReport, data).outcome == "findings"
    data["outcome"] = "clean"
    with pytest.raises(ValidationError, match="outcome"):
        load(ScanReport, data)


def test_not_checked_dependency_with_finding_gives_findings():
    deps = [dep(osv_status="not_checked", osv_error="timeout")]
    data = report([not_checked()], deps=deps)
    assert load(ScanReport, data).outcome == "findings"


# Step tools rule ------------------------------------------------------------------------------


def step(name, tools, state="done"):
    return {
        "name": name,
        "state": state,
        "tools": tools,
        "started_at": None,
        "finished_at": None,
        "message": None,
    }


@pytest.mark.parametrize(
    ("name", "tools"),
    [
        ("resolve", ["sandbox", "lockfile"]),
        ("resolve", ["sandbox", "uv"]),
        ("resolve", []),
        ("osv", ["osv"]),
        ("research", ["tavily", "nemotron"]),
        ("analysis", ["sandbox", "ast"]),
        ("analysis", ["ast"]),
        ("reasoning", ["nemotron", "ast"]),
        ("report", ["verdict_gate"]),
    ],
)
def test_step_tools_valid(name, tools):
    assert load(StepStatus, step(name, tools)).tools == tools


@pytest.mark.parametrize(
    ("name", "tools"),
    [
        ("osv", ["tavily"]),
        ("research", ["nemotron", "tavily"]),
        ("resolve", ["lockfile", "sandbox"]),
        ("resolve", ["lockfile", "uv"]),
        ("analysis", ["ast", "ast"]),
        ("report", ["ast"]),
    ],
)
def test_step_tools_invalid(name, tools):
    with pytest.raises(ValidationError):
        load(StepStatus, step(name, tools))


def test_skipped_step_has_no_tools_and_message_cap():
    with pytest.raises(ValidationError):
        load(StepStatus, step("osv", ["osv"], "skipped"))
    with pytest.raises(ValidationError):
        load(StepStatus, step("osv", []) | {"message": "x" * 301})


# Security carry-over: Dependency names and versions (findings 6) -------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "--index-url=x",
        "pkg @ https://x",
        "Pkg",
        "a_b",
        "-lead",
        "trail-",
        "a..b",
        "",
        "a b",
        "x" * 101,
    ],
)
def test_dependency_rejects_bad_names(name):
    with pytest.raises(ValidationError):
        load(Dependency, dep(name))


@pytest.mark.parametrize(
    "version", ["1.0 @ https://x", "--pre", "latest", "1.0;rm", "", "1..0", "x" * 70]
)
def test_dependency_rejects_non_pep440_versions(version):
    with pytest.raises(ValidationError):
        load(Dependency, dep(version=version))


@pytest.mark.parametrize(
    "version", ["5.3.1", "1!2.0", "2.0rc1", "1.0.post2", "1.0.dev3", "1.0+local.1", "v1.2"]
)
def test_dependency_accepts_pep440_versions(version):
    assert load(Dependency, dep(version=version)).version == version


def test_dependency_resolution_rules():
    with pytest.raises(ValidationError):
        load(Dependency, dep(version=None))
    with pytest.raises(ValidationError):
        load(
            Dependency,
            dep(resolved=False, unresolved_reason=None, version=None, osv_status="not_queried"),
        )
    with pytest.raises(ValidationError):
        load(Dependency, dep(osv_status="not_checked"))
    with pytest.raises(ValidationError):
        load(Dependency, dep(osv_error="boom"))
    ok = dep("flask", None, resolved=False, unresolved_reason="x", osv_status="not_queried")
    assert load(Dependency, ok).resolved is False


# Security carry-over: ScanRequest.ref (finding 11) --------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        ".",
        "..",
        "a/./b",
        "a/../b",
        "%2e%2e",
        "feature/",
        "a//b",
        "/lead",
        ".hidden",
        "f" * 41,
        "a" * 101,
        "a b",
        "",
    ],
)
def test_scan_request_rejects_bad_refs(ref):
    with pytest.raises(ValidationError):
        load(ScanRequest, {"repo_url": "https://github.com/o/r", "ref": ref})


@pytest.mark.parametrize(
    "ref", ["main", "v1.0.0", "feature/x-1", "release_2", "a" * 40, "0" * 40, "f" * 39]
)
def test_scan_request_accepts_normal_refs(ref):
    assert load(ScanRequest, {"repo_url": "https://github.com/o/r", "ref": ref}).ref == ref


def test_scan_request_url_cap_and_optional_ref():
    assert load(ScanRequest, {"repo_url": "https://github.com/o/r"}).ref is None
    with pytest.raises(ValidationError):
        load(ScanRequest, {"repo_url": "https://github.com/" + "a" * 300})
    with pytest.raises(ValidationError):
        load(ScanRequest, {"repo_url": ""})


# Other models ---------------------------------------------------------------------------------


def test_error_response_shape():
    ok = load(ErrorResponse, {"error": {"code": "scan_not_found", "message": "m"}})
    assert ok.error.details == {} and ok.error.retry_after_s is None
    with pytest.raises(ValidationError):
        load(ErrorResponse, {"error": {"code": "nope", "message": "m"}})


def test_progress_event_rules():
    base = {
        "seq": 1,
        "scan_id": "x",
        "type": "step",
        "step": "osv",
        "state": "running",
        "message": None,
        "at": NOW,
        "outcome": None,
        "tools": [],
    }
    assert load(ProgressEvent, base).seq == 1
    with pytest.raises(ValidationError):
        load(ProgressEvent, base | {"seq": 0})
    with pytest.raises(ValidationError):
        load(ProgressEvent, base | {"type": "done"})
    with pytest.raises(ValidationError):
        load(ProgressEvent, base | {"step": None})
    done = base | {"type": "done", "step": None, "state": None, "outcome": "clean"}
    assert load(ProgressEvent, done).outcome == "clean"


def test_config_and_health_rules():
    limits = {
        "max_repo_mb": 50,
        "max_repo_files": 5000,
        "live_scans_per_client_hour": 3,
        "live_scans_per_client_day": 10,
    }
    cfg = {
        "version": "0.1.0",
        "live_scans_enabled": True,
        "live_scans_paused_reason": None,
        "features": {"progress_feed": True},
        "limits": limits,
        "eval": None,
    }
    assert load(AppConfigResponse, cfg).limits["max_repo_mb"] == 50
    with pytest.raises(ValidationError):
        load(AppConfigResponse, cfg | {"limits": {"max_repo_mb": 50}})
    health = {"status": "ok", "version": "1", "live_scans": "enabled", "budget_remaining": None}
    assert load(HealthResponse, health).budget_remaining is None
    with pytest.raises(ValidationError):
        load(HealthResponse, health | {"budget_remaining": {"nebius": 1.5}})
    with pytest.raises(ValidationError):
        load(HealthResponse, health | {"live_scans": "maybe"})


def test_ledger_allowances_sum_to_caps():
    pool = {"spent_usd": 0.0, "reserved_usd": 0.0}
    state = {
        "schema_version": 2,
        "caps_usd": {"nebius": 10.0, "tavily": 5.0},
        "spenders": {
            "local": {
                "allowance_usd": {"nebius": 10.0, "tavily": 5.0},
                "pools": {"nebius": pool, "tavily": pool},
                "day": None,
            }
        },
        "reservations": [],
        "cas_verified": None,
        "updated_at": NOW,
    }
    assert load(LedgerState, state).schema_version == 2
    bad = copy.deepcopy(state)
    bad["spenders"]["local"]["allowance_usd"]["nebius"] = 9.0
    with pytest.raises(ValidationError, match="sum"):
        load(LedgerState, bad)


@pytest.mark.parametrize("source", ["local:eval/fixtures/demo", "github:octo/demo@" + SHA])
def test_eval_repo_source_accepted(source):
    assert load(EvalRepo, {"id": "r", "source": source}).source == source


@pytest.mark.parametrize(
    "source", ["github:octo/demo@main", "local:../etc", "http://x/y", "local:eval/fixtures/../x"]
)
def test_eval_repo_source_rejected(source):
    with pytest.raises(ValidationError):
        load(EvalRepo, {"id": "r", "source": source})
