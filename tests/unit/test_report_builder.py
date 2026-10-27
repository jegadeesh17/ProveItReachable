"""Verdict gate, confidence caps, ranking, step tools and report building (AC-18 to AC-23)."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from pir.models.index import AnalysisIndex, DefRecord, Edge, IndexedFile, TargetSite
from pir.models.reasoning import ModelVerdict, ReasoningOutcome
from pir.models.report import (
    Advisory,
    AdvisoryLink,
    CallPathStep,
    Dependency,
    Finding,
    Remediation,
    RepoRef,
    Resolution,
    SourceLocation,
    StepStatus,
    compute_finding_id,
)
from pir.models.research import AdvisoryResearch
from pir.reasoning.ast_tool import AstTool
from pir.report_builder import (
    Candidate,
    ScanContext,
    build_finding,
    build_report,
    compute_remediation,
    rank,
    step_tools,
)
from pir.sandbox_agent import index as agent_index

SHA = "b" * 40
OSV_URL = "https://osv.dev/vulnerability/GHSA-8q59-q68h-6hv4"
SEEN = {("app.py", 3), ("app.py", 10)}
ENTRY = CallPathStep(file="app.py", line=3, edge="entry", symbol="app:main")
HIT = CallPathStep(file="app.py", line=10, edge="vulnerable_call", symbol="yaml.load")


def advisory(adv_id="GHSA-8q59-q68h-6hv4", severity="critical"):
    return Advisory(
        id=adv_id,
        aliases=["CVE-2020-14343"],
        summary="Arbitrary code execution",
        severity=severity,
        fixed_versions=["5.4"],
        published=None,
        modified=None,
    )


def candidate(direct=True, with_advisory=True, package="pyyaml"):
    link = AdvisoryLink(url=OSV_URL, source="osv", title=None)
    return Candidate(
        package=package,
        version="5.3.1",
        advisory=advisory() if with_advisory else None,
        advisory_links=[link] if with_advisory else [],
        direct=direct,
        required_by=None if direct else "requests",
    )


def research(identified=True, failure=None):
    link = AdvisoryLink(url=OSV_URL, source="osv", title=None)
    return AdvisoryResearch(
        advisory_id="GHSA-8q59-q68h-6hv4",
        identified=identified,
        not_identified_reason=None if identified else "vulnerable function not identified: vague",
        from_cache=False,
        symbols=["yaml.load"] if identified else [],
        links=[link],
        evidence_urls=[OSV_URL] if identified else [],
        failure=failure,
    )


def index(truncated=False, unparsed=False, extra_sites=(), sites=None, edges=None):
    files = [
        IndexedFile(path="app.py", lines=20, is_test=False, parsed=True, skip_reason=None),
        IndexedFile(
            path="tests/test_app.py", lines=10, is_test=True, parsed=True, skip_reason=None
        ),
    ]
    if unparsed:
        files.append(
            IndexedFile(
                path="broken.py", lines=2, is_test=False, parsed=False, skip_reason="syntax error"
            )
        )
    if sites is None:
        sites = [
            TargetSite(
                file="app.py", line=10, col=4, caller="app:main", kind="call",
                snippet="yaml.load(x)", target="yaml.load",
            ),
            TargetSite(
                file="app.py", line=10, col=4, caller="app:helper", kind="call",
                snippet="yaml.load(x)", target="yaml.load",
            ),
            TargetSite(
                file="app.py", line=10, col=4, caller="app:x", kind="call",
                snippet="yaml.load(x)", target="yaml.load",
            ),
            TargetSite(
                file="app.py", line=10, col=4, caller="app:<module>", kind="call",
                snippet="yaml.load(x)", target="yaml.load",
            ),
        ]  # fmt: skip
    if edges is None:
        edges = [
            Edge(caller="app:main", callee="app:x", file="app.py", line=3, heuristic=True),
            Edge(caller="app:main", callee="app:helper", file="app.py", line=3, heuristic=False),
        ]
    return AnalysisIndex(
        agent_protocol=1,
        commit_sha=SHA,
        target_modules=["yaml"],
        files=files,
        imports=[],
        defs=[
            DefRecord(
                id="app:main",
                file="app.py",
                line=2,
                end_line=5,
                decorators=[],
                entry_kind="main_block",
            ),
            DefRecord(
                id="app:helper", file="app.py", line=8, end_line=12, decorators=[], entry_kind=None
            ),
        ],
        target_sites=[*sites, *extra_sites],
        edges=edges,
        truncated=truncated,
        stats={},
    )


def outcome(verdict="likely reachable", confidence="high", path=(ENTRY, HIT), locations=None):
    locs = (
        [SourceLocation(file="app.py", line=10, kind="call", snippet=None)]
        if locations is None
        else locations
    )
    model = ModelVerdict(
        verdict=verdict,
        confidence=confidence,
        call_path=list(path) if path is not None else None,
        source_locations=locs,
        explanation="model says so",
        limitations=["model limitation"],
    )
    return ReasoningOutcome(
        verdict=model, tool_calls=[], attempts=1, failure=None, failure_detail=None
    )


def no_path(confidence="high"):
    return outcome("no reachable path detected", confidence, path=None, locations=[])


def run(out, idx=None, cand=None, res="default", **kw):
    return build_finding(
        cand or candidate(),
        research() if res == "default" else res,
        out,
        idx or index(),
        seen_locations=kw.pop("seen", SEEN),
        **kw,
    )


# AC-20 / AC-22: likely reachable needs a validated path --------------------------------------


def test_ac20_valid_path_stays_likely_reachable():
    f = run(outcome())
    assert f.verdict == "likely reachable"
    assert f.confidence == "high"
    assert [s.line for s in f.call_path] == [3, 10]
    assert f.reason_code is None


@pytest.mark.parametrize(
    ("path", "seen", "fragment"),
    [
        (
            (
                ENTRY,
                CallPathStep(file="ghost.py", line=1, edge="vulnerable_call", symbol="yaml.load"),
            ),
            SEEN | {("ghost.py", 1)},
            "not a file in the index",
        ),
        (
            (
                ENTRY,
                CallPathStep(file="app.py", line=99, edge="vulnerable_call", symbol="yaml.load"),
            ),
            SEEN | {("app.py", 99)},
            "line range",
        ),
        ((ENTRY, HIT), {("app.py", 3)}, "never returned by the ast tool"),
        (
            (
                ENTRY,
                CallPathStep(file="app.py", line=4, edge="vulnerable_call", symbol="yaml.load"),
            ),
            SEEN | {("app.py", 4)},
            "not a use of the vulnerable symbol",
        ),
        ((ENTRY,), SEEN, "not a use of the vulnerable symbol"),
        ((), SEEN, "no call path"),
        (None, SEEN, "no call path"),
        (
            (
                ENTRY,
                CallPathStep(file="app.py", line=3, edge="call", symbol="app:ghost"),
                HIT,
            ),
            SEEN | {("app.py", 3)},
            "step 0 does not call step 1",
        ),
        (
            (
                ENTRY,
                CallPathStep(file="app.py", line=3, edge="call", symbol="app:x"),
                HIT,
            ),
            SEEN | {("app.py", 3)},
            "step 0 does not call step 1",
        ),
        (
            (
                CallPathStep(file="app.py", line=3, edge="entry", symbol="app:unmatched"),
                HIT,
            ),
            SEEN | {("app.py", 3)},
            "does not call the target site",
        ),
    ],
    ids=[
        "file-outside-index",
        "line-out-of-range",
        "not-seen",
        "last-not-target",
        "single-step",
        "empty",
        "none",
        "broken-edge",
        "wrong-heuristic-flag",
        "wrong-site-caller",
    ],
)
def test_ac20_ac22_invalid_path_is_downgraded(path, seen, fragment):
    f = run(outcome(path=path), seen=seen)
    assert f.verdict == "uncertain"
    assert f.reason_code == "unsupported_reachability_claim"
    assert fragment in f.reason and f.reason.startswith("rule 2")
    assert f.confidence == "low"
    assert f.call_path is None
    assert "Downgraded from 'likely reachable'" in f.explanation


def test_ac22_last_step_must_be_a_site_of_the_vulnerable_symbol():
    other = TargetSite(
        file="app.py", line=4, col=0, caller="app:main", kind="call", snippet="json.load(x)",
        target="json.load",
    )  # fmt: skip
    step = CallPathStep(file="app.py", line=4, edge="vulnerable_call", symbol="json.load")
    f = run(
        outcome(path=(ENTRY, step)), idx=index(extra_sites=[other]), seen=SEEN | {("app.py", 4)}
    )
    assert f.reason_code == "unsupported_reachability_claim"


def test_ac20_ac22_wrong_site_caller_is_downgraded():
    other_site = TargetSite(
        file="app.py",
        line=10,
        col=4,
        caller="app:other",
        kind="call",
        snippet="yaml.load(x)",
        target="yaml.load",
    )
    f = run(outcome(path=(ENTRY, HIT)), idx=index(sites=[other_site]))
    assert f.verdict == "uncertain"
    assert f.reason_code == "unsupported_reachability_claim"
    assert "step 0 does not call the target site (caller is app:other)" in f.reason


def test_valid_path_without_model_locations_uses_the_target_site():
    f = run(outcome(locations=[]))
    assert [(s.file, s.line, s.kind) for s in f.source_locations] == [("app.py", 10, "call")]


def test_model_locations_outside_the_index_are_dropped_with_a_limitation():
    ghost = SourceLocation(file="ghost.py", line=3, kind="call", snippet=None)
    real = SourceLocation(file="app.py", line=10, kind="call", snippet=None)
    f = run(outcome(locations=[ghost, real]))
    assert [s.file for s in f.source_locations] == ["app.py"]
    assert any("dropped" in text for text in f.limitations)


# Gate rule 3 ----------------------------------------------------------------------------------


def test_rule3_no_path_with_a_non_test_site_is_contradicted():
    f = run(no_path())
    assert f.verdict == "uncertain"
    assert f.reason_code == "contradicted_by_index"
    assert "app.py:10" in f.reason
    assert f.confidence == "low"


def test_rule3_test_only_site_does_not_contradict():
    test_site = TargetSite(
        file="tests/test_app.py", line=2, col=0, caller="tests.test_app:<module>", kind="call",
        snippet="yaml.load(x)", target="yaml.load",
    )  # fmt: skip
    f = run(no_path(), idx=index(sites=[test_site]))
    assert f.verdict == "no reachable path detected"


def test_rule3_other_symbol_of_the_package_does_not_contradict():
    safe = TargetSite(
        file="app.py", line=10, col=0, caller="app:main", kind="call", snippet="yaml.safe_load(x)",
        target="yaml.safe_load",
    )  # fmt: skip
    assert run(no_path(), idx=index(sites=[safe])).verdict == "no reachable path detected"


# AC-21 ----------------------------------------------------------------------------------------


def test_ac21_no_path_gets_the_scope_statement():
    f = run(no_path(), idx=index(sites=[]))
    scope = [t for t in f.limitations if t.startswith("Scope:")]
    assert len(scope) == 1
    assert "2 of 2 Python files parsed" in scope[0]
    assert "`yaml`" in scope[0]
    assert "Not examined: dynamic imports, getattr or reflection" in scope[0]
    assert "model limitation" in f.limitations
    assert "No import or call site found." in f.limitations


def test_ac21_scope_names_skips_and_transitive_parent():
    f = run(
        no_path(), idx=index(sites=[], unparsed=True, truncated=True), cand=candidate(direct=False)
    )
    scope = next(t for t in f.limitations if t.startswith("Scope:"))
    assert "2 of 3 Python files parsed (1 skipped: syntax error; index truncated" in scope
    assert "transitive dependency of `requests`" in scope


# Confidence caps (ADR-0010, rule 5), table-driven --------------------------------------------

HEURISTIC_PATH = (
    ENTRY,
    CallPathStep(file="app.py", line=3, edge="heuristic_call", symbol="app:x"),
    HIT,
)
NON_ENTRY_PATH = (CallPathStep(file="app.py", line=3, edge="call", symbol="app:helper"), HIT)
MODULE_ENTRY_PATH = (CallPathStep(file="app.py", line=3, edge="entry", symbol="app:<module>"), HIT)
NON_ENTRY_DEF = (CallPathStep(file="app.py", line=3, edge="entry", symbol="app:helper"), HIT)

CAPS = [
    # (model verdict, model confidence, path, index kwargs, direct, expected confidence)
    ("likely reachable", "high", (ENTRY, HIT), {}, True, "high"),
    ("likely reachable", "medium", (ENTRY, HIT), {}, True, "medium"),
    ("likely reachable", "low", (ENTRY, HIT), {}, True, "low"),
    ("likely reachable", "high", MODULE_ENTRY_PATH, {}, True, "high"),
    ("likely reachable", "high", HEURISTIC_PATH, {}, True, "medium"),
    ("likely reachable", "high", NON_ENTRY_PATH, {}, True, "medium"),
    ("likely reachable", "high", NON_ENTRY_DEF, {}, True, "medium"),
    ("likely reachable", "low", HEURISTIC_PATH, {}, True, "low"),
    ("no reachable path detected", "high", None, {"sites": []}, True, "high"),
    ("no reachable path detected", "medium", None, {"sites": []}, True, "medium"),
    ("no reachable path detected", "high", None, {"sites": [], "unparsed": True}, True, "medium"),
    ("no reachable path detected", "high", None, {"sites": [], "truncated": True}, True, "medium"),
    ("no reachable path detected", "low", None, {"sites": [], "truncated": True}, True, "low"),
    ("no reachable path detected", "high", None, {"sites": []}, False, "low"),
    ("no reachable path detected", "high", None, {"sites": [], "unparsed": True}, False, "low"),
    ("no reachable path detected", "high", None, {"sites": []}, None, "high"),
    ("uncertain", "high", None, {}, True, "low"),
    ("uncertain", "medium", None, {}, True, "low"),
]


@pytest.mark.parametrize(("verdict", "model_conf", "path", "idx_kw", "direct", "expected"), CAPS)
def test_confidence_caps(verdict, model_conf, path, idx_kw, direct, expected):
    out = outcome(verdict, model_conf, path=path, locations=[])
    f = run(out, idx=index(**idx_kw), cand=candidate(direct=direct), seen=SEEN | {("app.py", 4)})
    assert f.confidence == expected
    if verdict == "uncertain":
        assert f.verdict == "uncertain"


def test_heuristic_and_incomplete_index_add_limitations():
    f = run(outcome(path=HEURISTIC_PATH), idx=index(unparsed=True))
    assert any("heuristic" in t for t in f.limitations)
    assert any("skipped or the index was truncated" in t for t in f.limitations)


# AC-18 / forced and failure paths -------------------------------------------------------------


def test_model_uncertain_stays_uncertain_with_a_reason():
    f = run(outcome("uncertain", "high", path=None))
    assert (f.verdict, f.confidence) == ("uncertain", "low")
    assert f.reason_code and f.reason
    # M1-FIX-02: nothing was downgraded, so the explanation must not claim it was
    assert "Downgraded" not in f.explanation
    assert f.explanation.startswith("The model answered 'uncertain'")


@pytest.mark.parametrize("code", ["spend_cap_reached", "scan_timeout", "sandbox_timeout"])
def test_forced_reason_wins(code):
    f = run(outcome(), forced=(code, "stopped early"))
    assert (f.verdict, f.reason_code, f.reason) == ("uncertain", code, "stopped early")
    assert f.call_path is None and f.limitations


def test_symbol_not_identified_is_uncertain_not_guessed():
    f = run(outcome(), res=research(identified=False))
    assert f.reason_code == "vulnerable_function_not_identified"
    assert f.vulnerable_symbol.identified is False
    assert f.vulnerable_symbol.not_identified_reason.startswith(
        "vulnerable function not identified"
    )


def test_research_failure_code_is_carried():
    f = run(outcome(), res=research(identified=False, failure="tavily_failed"))
    assert f.reason_code == "tavily_failed"


def test_missing_research_is_not_identified():
    f = run(outcome(), res=None)
    assert f.reason_code == "vulnerable_function_not_identified"
    assert f.advisory_links[0].url == OSV_URL


@pytest.mark.parametrize(
    ("out", "code"),
    [
        (None, "model_output_invalid"),
        (
            ReasoningOutcome(
                verdict=None,
                tool_calls=[],
                attempts=3,
                failure="model_output_invalid",
                failure_detail="3 invalid answers",
            ),
            "model_output_invalid",
        ),
        (
            ReasoningOutcome(
                verdict=None,
                tool_calls=[],
                attempts=1,
                failure="nebius_failed",
                failure_detail="HTTP 500",
            ),
            "nebius_failed",
        ),
    ],
)
def test_ac28_missing_model_verdict_is_uncertain(out, code):
    f = run(out)
    assert (f.verdict, f.reason_code) == ("uncertain", code)


def test_missing_index_cannot_back_a_verdict():
    f = build_finding(candidate(), research(), outcome(), None, seen_locations=SEEN)
    assert (f.verdict, f.reason_code) == ("uncertain", "sandbox_failed")


def test_not_checked_candidate_becomes_a_not_checked_finding():  # AC-26
    f = build_finding(candidate(with_advisory=False), None, None, None)
    assert f.kind == "not_checked"
    assert f.advisory is None and f.advisory_links == [] and f.vulnerable_symbol is None
    assert (f.verdict, f.reason_code) == ("uncertain", "osv_failed")
    forced = build_finding(
        candidate(with_advisory=False), None, None, None, forced=("osv_failed", "OSV timed out")
    )
    assert forced.reason == "OSV timed out"


def test_explanation_is_truncated_to_the_model_limit():
    out = outcome()
    long_text = ModelVerdict(
        **{**out.verdict.model_dump(), "explanation": "x" * 2000}  # at the limit already
    )
    f = run(out.model_copy(update={"verdict": long_text}), forced=("scan_timeout", "late"))
    assert len(f.explanation) <= 2000
    downgraded = run(out.model_copy(update={"verdict": long_text}), seen=set())
    assert len(downgraded.explanation) <= 2000


def test_finding_id_is_stable_for_package_version_and_advisory():
    a = run(outcome())
    b = run(no_path(), idx=index(sites=[]))
    assert a.finding_id == b.finding_id and len(a.finding_id) == 16


# AC-23 ranking --------------------------------------------------------------------------------


def make(verdict, confidence, severity, package, adv_id):
    base = run(outcome())
    return base.model_copy(
        update={
            "verdict": verdict,
            "confidence": confidence,
            "package": package,
            "advisory": advisory(adv_id, severity),
        }
    )


def test_ac23_rank_order():
    lr_hi = make("likely reachable", "high", "low", "zzz", "GHSA-2")
    lr_med = make("likely reachable", "medium", "critical", "aaa", "GHSA-1")
    unc = make("uncertain", "low", "critical", "aaa", "GHSA-1")
    unc_hi_sev = make("uncertain", "low", "critical", "bbb", "GHSA-1")
    unc_lo_sev = make("uncertain", "low", "unknown", "aaa", "GHSA-1")
    np_hi = make("no reachable path detected", "high", "critical", "aaa", "GHSA-1")
    np_lo = make("no reachable path detected", "low", "critical", "aaa", "GHSA-1")
    expected = [lr_hi, lr_med, unc, unc_hi_sev, unc_lo_sev, np_hi, np_lo]
    shuffled = [np_lo, unc_lo_sev, lr_med, np_hi, unc_hi_sev, lr_hi, unc]
    assert rank(shuffled) == expected
    assert rank(list(reversed(shuffled))) == expected


def test_ac23_rank_breaks_ties_by_severity_package_then_advisory_id():
    a = make("uncertain", "low", "high", "pkg", "GHSA-B")
    b = make("uncertain", "low", "high", "pkg", "GHSA-A")
    c = make("uncertain", "low", "moderate", "aaa", "GHSA-Z")
    d = make("uncertain", "low", "high", "abc", "GHSA-Z")
    assert rank([c, a, b, d]) == [d, b, a, c]


def test_rank_does_not_mutate_its_input():
    items = [
        make("uncertain", "low", "low", "b", "G"),
        make("likely reachable", "high", "low", "a", "G"),
    ]
    before = list(items)
    rank(items)
    assert items == before


# Step tools rule ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "used", "expected"),
    [
        ("resolve", {"uv", "sandbox"}, ["sandbox", "uv"]),
        ("resolve", {"lockfile"}, ["lockfile"]),
        ("research", ["nemotron", "tavily"], ["tavily", "nemotron"]),
        ("analysis", ["ast"], ["ast"]),
        ("reasoning", ["ast", "nemotron"], ["nemotron", "ast"]),
        ("report", ["verdict_gate"], ["verdict_gate"]),
        ("osv", [], []),
    ],
)
def test_step_tools_canonical_order(step, used, expected):
    assert step_tools(step, used) == expected


def test_step_tools_rejects_a_tool_the_step_cannot_use():
    with pytest.raises(ValueError, match="not allowed"):
        step_tools("osv", ["tavily"])


# build_report ---------------------------------------------------------------------------------

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def dependency(name, resolved=True, status="checked", error=None):
    return Dependency(
        name=name,
        source="uv.lock",
        groups=["main"],
        version="1.0" if resolved else None,
        resolved=resolved,
        unresolved_reason=None if resolved else "dynamic metadata needs a build",
        direct=True,
        osv_status=status if resolved else "not_queried",
        osv_error=error,
    )


def context(deps, resolve_state="done", step_state="done"):
    steps = [
        StepStatus(
            name=n,
            state=resolve_state if n == "resolve" else step_state,
            tools=[],
            started_at=None,
            finished_at=None,
            message=None,
        )
        for n in ("resolve", "osv", "research", "analysis", "reasoning", "report")
    ]
    return ScanContext(
        scan_id="0123456789abcdef0123456789abcdef",
        tool_version="0.1.0",
        model="nemotron",
        generated_at=T0,
        repo=RepoRef(
            url="https://github.com/octo/demo",
            owner="octo",
            name="demo",
            ref_requested=None,
            default_branch="main",
            commit_sha=SHA,
        ),  # fmt: skip
        resolution=Resolution(
            method="lockfile", python_version=None, lockfile="uv.lock", compiled_from=None, notes=[]
        ),
        steps=steps,
        dependencies=deps,
        limitations=["scope"],
    )


def test_build_report_clean():
    rep = build_report(context([dependency("a")]), [])
    assert rep.outcome == "clean"
    assert rep.summary.dependencies_total == 1 and rep.summary.findings_total == 0
    assert rep.summary.by_verdict == {
        "likely reachable": 0,
        "no reachable path detected": 0,
        "uncertain": 0,
    }


def test_build_report_incomplete_for_unresolved_or_failed_resolve():
    unresolved = build_report(context([dependency("a"), dependency("b", resolved=False)]), [])
    assert unresolved.outcome == "incomplete"
    assert unresolved.summary.dependencies_unresolved == 1
    failed = build_report(context([dependency("a")], resolve_state="failed"), [])
    assert failed.outcome == "incomplete"


def test_build_report_findings_are_ranked_and_summarised():
    deps = [dependency("zeta"), dependency("pyyaml")]
    unc = run(outcome(), cand=candidate(package="zeta"), forced=("scan_timeout", "late"))
    lr = run(outcome())
    other = build_finding(
        candidate(package="pyyaml2"), research(), no_path(), index(sites=[]), seen_locations=SEEN
    )
    rep = build_report(context(deps), [other, unc, lr])
    assert rep.outcome == "findings"
    assert [f.verdict for f in rep.findings][0] == "likely reachable"
    assert rep.findings[-1].verdict == "no reachable path detected"
    assert rep.summary.findings_total == 3
    assert [d.name for d in rep.dependencies] == ["pyyaml", "zeta"]


def test_build_report_not_checked_dependency_counts():
    deps = [dependency("pyyaml", status="not_checked", error="OSV down")]
    nc = build_finding(candidate(with_advisory=False), None, None, None)
    rep = build_report(context(deps), [nc])
    assert rep.outcome == "findings"
    assert rep.summary.dependencies_not_checked == 1
    assert rep.findings[0].kind == "not_checked"


def test_build_report_validates_strictly():
    with pytest.raises(ValidationError):
        build_report(context([dependency("a")], step_state="running"), [])
    dup = run(outcome())
    with pytest.raises(ValidationError, match="unique"):
        build_report(context([dependency("pyyaml")]), [dup, dup])
    with pytest.raises(ValidationError, match="sorted|steps"):
        bad = context([dependency("a")])
        build_report(ScanContext(**{**bad.__dict__, "steps": list(reversed(bad.steps))}), [])


# --- default-argument equivalences through the gate (ADR-0037, AC-80) ---

DEFAULT_LOADER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "pyyaml_default_loader"
)


def _default_loader_index() -> AnalysisIndex:
    raw = agent_index.build_index(str(DEFAULT_LOADER), SHA, ["yaml"])
    return AnalysisIndex.model_validate(raw)


def _full_load_research() -> AdvisoryResearch:
    return research().model_copy(update={"symbols": ["yaml.full_load", "yaml.FullLoader"]})


def _cand(version="5.3"):
    return replace(candidate(), version=version)


def _tool_path(idx, version, line):
    """The path AstTool returns to the call at main.py:`line`, as the model would submit it."""
    tool = AstTool(idx, package="PyYAML", version=version)
    paths = json.loads(tool.call("find_paths", {"symbol": "yaml.full_load"}).content)["paths"]
    chosen = [p for p in paths if p["steps"][-1]["line"] == line]
    steps = [CallPathStep(**s) for s in chosen[0]["steps"]] if chosen else None
    return steps, tool.seen_locations


@pytest.mark.parametrize("line", [13, 19])
def test_ac80_likely_reachable_path_to_an_implied_call_passes_the_gate(line):
    idx = _default_loader_index()
    steps, seen = _tool_path(idx, "5.3", line)
    f = build_finding(
        _cand(),
        _full_load_research(),
        outcome(path=steps, locations=[]),
        idx,
        seen_locations=seen,
    )
    assert f.verdict == "likely reachable"
    assert f.reason_code is None
    assert f.call_path[-1].line == line and f.call_path[-1].symbol == "yaml.load"
    assert f.confidence == "high"


def test_ac80_the_same_path_fails_the_gate_on_pyyaml_6():
    idx = _default_loader_index()
    steps, seen = _tool_path(idx, "5.3", 19)
    f = build_finding(
        _cand("6.0"),
        _full_load_research(),
        outcome(path=steps, locations=[]),
        idx,
        seen_locations=seen,
    )
    assert f.verdict == "uncertain"
    assert f.reason_code == "unsupported_reachability_claim"
    assert "not a use of the vulnerable symbol" in f.reason


def test_ac80_a_path_to_the_loader_call_is_never_accepted():
    idx = _default_loader_index()
    steps, seen = _tool_path(idx, "5.3", 19)
    safe = steps[:-1] + [
        CallPathStep(file="settings.py", line=8, edge="vulnerable_call", symbol="yaml.load")
    ]
    f = build_finding(
        _cand(),
        _full_load_research(),
        outcome(path=safe, locations=[]),
        idx,
        seen_locations=seen | {("settings.py", 8)},
    )
    assert f.verdict == "uncertain"
    assert f.reason_code == "unsupported_reachability_claim"


def test_ac80_no_path_answer_is_downgraded_when_an_implying_call_exists():
    idx = _default_loader_index()
    f = build_finding(_cand(), _full_load_research(), no_path(), idx, seen_locations=set())
    assert f.verdict == "uncertain"
    assert f.reason_code == "contradicted_by_index"
    assert "main.py:" in f.reason and "yaml.load" in f.reason


def test_ac80_no_path_answer_stands_on_pyyaml_6():
    idx = _default_loader_index()
    f = build_finding(_cand("6.0"), _full_load_research(), no_path(), idx, seen_locations=set())
    assert f.verdict == "no reachable path detected"


def test_ac80_no_path_answer_stands_when_only_loader_calls_exist():
    idx = _default_loader_index()
    only_safe = idx.model_copy(
        update={"target_sites": [s for s in idx.target_sites if s.file == "settings.py"]}
    )
    f = build_finding(_cand(), _full_load_research(), no_path(), only_safe, seen_locations=set())
    assert f.verdict == "no reachable path detected"


# ---- AC-85: deterministic finding remediation (ADR-0044) -----------------------------------


def test_ac85_remediation_selects_lowest_fixed_version_greater_than_current():
    adv = advisory()
    adv_multi = adv.model_copy(update={"fixed_versions": ["5.4.1", "5.3.2", "6.0"]})
    cand = candidate(with_advisory=True)
    cand_multi = replace(cand, version="5.3.1", advisory=adv_multi)

    rem = compute_remediation(cand_multi, cand.advisory_links)
    assert rem is not None
    assert rem.package == "pyyaml"
    assert rem.target_version == "5.3.2"
    assert rem.guidance == "Upgrade pyyaml to version 5.3.2 or newer."


def test_ac85_remediation_falls_back_to_first_fixed_version_when_none_greater():
    adv = advisory()
    adv_older = adv.model_copy(update={"fixed_versions": ["5.2.0", "5.3.0"]})
    cand = candidate(with_advisory=True)
    cand_older = replace(cand, version="5.3.1", advisory=adv_older)

    rem = compute_remediation(cand_older, cand.advisory_links)
    assert rem is not None
    assert rem.package == "pyyaml"
    assert rem.target_version == "5.2.0"
    assert rem.guidance == "Upgrade pyyaml to version 5.2.0 or newer."


def test_ac85_remediation_with_osv_fix_commit_link_and_no_fixed_versions():
    adv = advisory()
    adv_unversioned = adv.model_copy(update={"fixed_versions": []})
    fix_commit_url = (
        "https://github.com/yaml/pyyaml/commit/abcdef1234567890abcdef1234567890abcdef12"
    )
    links = [
        AdvisoryLink(url=fix_commit_url, source="osv", title="Fix commit"),
        AdvisoryLink(url="https://example.com/docs", source="tavily", title="Docs"),
    ]
    cand = replace(candidate(with_advisory=True), advisory=adv_unversioned, advisory_links=links)

    rem = compute_remediation(cand, links)
    assert rem is not None
    assert rem.package == "pyyaml"
    assert rem.target_version is None
    assert "Inspect the fix commit" in rem.guidance
    assert fix_commit_url in rem.guidance


def test_ac85_remediation_with_no_fixed_version_and_no_fix_commit():
    adv = advisory()
    adv_unfixed = adv.model_copy(update={"fixed_versions": []})
    links = [
        AdvisoryLink(url="https://osv.dev/vulnerability/GHSA-1234", source="osv", title="Advisory")
    ]
    cand = replace(candidate(with_advisory=True), advisory=adv_unfixed, advisory_links=links)

    rem = compute_remediation(cand, links)
    assert rem is not None
    assert rem.package == "pyyaml"
    assert rem.target_version is None
    assert "No fixed version is published" in rem.guidance


def test_ac85_remediation_attached_to_built_finding():
    f = build_finding(
        candidate(with_advisory=True), research(), outcome(), index(), seen_locations=SEEN
    )
    assert f.remediation is not None
    assert f.remediation.package == "pyyaml"
    assert f.remediation.target_version == "5.4"
    assert f.remediation.guidance == "Upgrade pyyaml to version 5.4 or newer."


def test_ac85_not_checked_finding_has_no_remediation():
    f = build_finding(candidate(with_advisory=False), None, None, None)
    assert f.remediation is None


def test_ac85_remediation_validation_mismatched_package_raises():
    rem = Remediation(package="requests", target_version="2.32.0", guidance="Upgrade requests")
    adv = advisory()
    fid = compute_finding_id("pyyaml", "5.3.1", adv.id)
    with pytest.raises(ValidationError, match="remediation package does not match finding package"):
        Finding.model_validate(
            {
                "finding_id": fid,
                "kind": "advisory",
                "verdict": "likely reachable",
                "confidence": "high",
                "package": "pyyaml",
                "version": "5.3.1",
                "advisory": adv.model_dump(),
                "vulnerable_symbol": {
                    "identified": True,
                    "symbols": ["yaml.load"],
                    "not_identified_reason": None,
                    "evidence_urls": [OSV_URL],
                },
                "advisory_links": [{"url": OSV_URL, "source": "osv", "title": None}],
                "source_locations": [
                    {"file": "app.py", "line": 10, "kind": "call", "snippet": "yaml.load(x)"}
                ],
                "call_path": [
                    {"file": "app.py", "line": 3, "edge": "entry", "symbol": "app:main"},
                    {
                        "file": "app.py",
                        "line": 10,
                        "edge": "vulnerable_call",
                        "symbol": "yaml.load",
                    },
                ],
                "explanation": "test explanation",
                "limitations": ["test limitation"],
                "reason_code": None,
                "reason": None,
                "remediation": rem.model_dump(),
            }
        )
