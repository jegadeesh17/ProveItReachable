"""AstTool: the seven section 9.1 tools over an index (M1-TASK-10; AC-17)."""

import json
import shutil
from pathlib import Path

import pytest

from pir.models.index import AnalysisIndex, IndexedFile, TargetSite
from pir.models.reasoning import ModelVerdict, ReasoningOutcome
from pir.models.report import Advisory, AdvisoryLink
from pir.models.research import AdvisoryResearch
from pir.reasoning.ast_tool import MAX_RESULT_BYTES, TOOL_NAMES, AstTool
from pir.report_builder import Candidate, build_finding
from pir.sandbox_agent import index as agent_index

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "repos"
SHA = "c" * 40


@pytest.fixture(scope="module")
def index() -> AnalysisIndex:
    raw = agent_index.build_index(str(FIXTURES / "entrypoints"), SHA, ["yaml"])
    return AnalysisIndex.model_validate(raw)


@pytest.fixture
def tool(index) -> AstTool:
    return AstTool(index)


def _ok(tool: AstTool, name: str, arguments: dict) -> dict:
    result = tool.call(name, arguments)
    assert result.ok, result.content
    assert result.result_bytes <= MAX_RESULT_BYTES
    return json.loads(result.content)


def _big_index(count: int = 120) -> AnalysisIndex:
    sites = [
        TargetSite(
            file="big.py",
            line=line,
            col=4,
            caller="big:<module>",
            kind="call",
            snippet="x" * 200,
            target="yaml.load",
        )
        for line in range(1, count + 1)
    ]
    return AnalysisIndex(
        agent_protocol=1,
        commit_sha=SHA,
        target_modules=["yaml"],
        files=[
            IndexedFile(path="big.py", lines=count, is_test=False, parsed=True, skip_reason=None)
        ],
        imports=[],
        defs=[],
        target_sites=sites,
        edges=[],
        truncated=False,
        stats={},
    )


# --- specs (AC-17) ---


def test_specs_are_exactly_the_section_9_1_tools(tool):  # AC-17
    names = [spec.name for spec in tool.specs()]
    assert names == [
        "find_imports",
        "find_call_sites",
        "find_callers",
        "get_definition",
        "list_entry_points",
        "find_paths",
        "submit_verdict",
    ]
    assert tuple(names) == TOOL_NAMES
    assert not any("read" in n or "file" in n or "open" in n for n in names)


def test_spec_parameters_match_section_9_1(tool):  # AC-17
    props = {s.name: s.parameters for s in tool.specs()}
    assert props["find_imports"]["required"] == ["module"]
    assert {"symbol", "limit", "offset"} <= set(props["find_call_sites"]["properties"])
    assert props["find_call_sites"]["required"] == ["symbol"]
    assert props["find_callers"]["required"] == ["function_id"]
    assert props["get_definition"]["required"] == ["function_id"]
    assert props["list_entry_points"]["required"] == []
    assert props["find_paths"]["properties"]["max_depth"]["maximum"] == 8
    assert set(props["submit_verdict"]["properties"]) == set(ModelVerdict.model_fields)
    for spec in tool.specs():
        assert spec.parameters["type"] == "object"
        assert spec.parameters["additionalProperties"] is False


# --- queries ---


def test_find_call_sites_matches_symbol_and_prefix(tool):
    exact = _ok(tool, "find_call_sites", {"symbol": "yaml.load"})
    places = [(s["file"], s["line"]) for s in exact["sites"]]
    assert places == [("app.py", 29), ("src/lib/core.py", 14), ("tool/runner.py", 5)]
    assert exact["total"] == 3 and exact["next_offset"] is None
    assert all(s["target"] == "yaml.load" and s["in_test"] is False for s in exact["sites"])
    prefix = _ok(tool, "find_call_sites", {"symbol": "yaml"})
    assert prefix["total"] == 4  # yaml.safe_load too
    assert _ok(tool, "find_call_sites", {"symbol": "yaml.loa"})["total"] == 0  # not a prefix match


def test_find_call_sites_paginates(tool):
    seen = []
    offset = 0
    while offset is not None:
        page = _ok(tool, "find_call_sites", {"symbol": "yaml", "limit": 1, "offset": offset})
        assert len(page["sites"]) == 1 and page["total"] == 4
        seen.append((page["sites"][0]["file"], page["sites"][0]["line"]))
        offset = page["next_offset"]
    assert len(seen) == 4 and seen == sorted(seen)


def test_find_imports_includes_submodules_and_bound_as(tool):
    result = _ok(tool, "find_imports", {"module": "yaml"})
    assert {(r["file"], r["line"]) for r in result["imports"]} >= {("app.py", 2), ("plain.py", 1)}
    assert all(r["bound_as"] and r["module"] == "yaml" for r in result["imports"])
    assert _ok(tool, "find_imports", {"module": "ya"})["total"] == 0


def test_find_callers_reports_the_heuristic_flag(tool):
    callers = _ok(tool, "find_callers", {"function_id": "app:parse_body"})["callers"]
    assert [(c["caller"], c["line"], c["heuristic"]) for c in callers] == [
        ("app:load_view", 10, False),
        ("app:sync", 25, False),
    ]
    heuristic = _ok(tool, "find_callers", {"function_id": "lib.core:Service.helper"})["callers"]
    assert heuristic[0]["heuristic"] is True
    assert _ok(tool, "find_callers", {"function_id": "nope:nothing"})["callers"] == []


def test_get_definition_and_unknown_id(tool):
    definition = _ok(tool, "get_definition", {"function_id": "app:load_view"})
    assert definition["file"] == "app.py" and definition["line"] == 9
    assert definition["decorators"] == ["app.route"] and definition["entry_kind"] == "route"
    assert ("app.py", 9) in tool.seen_locations
    missing = tool.call("get_definition", {"function_id": "app:missing"})
    assert missing.ok is False and "error" in json.loads(missing.content)


def test_list_entry_points_only_lists_entries(tool):
    result = _ok(tool, "list_entry_points", {})
    kinds = {e["id"]: e["entry_kind"] for e in result["entry_points"]}
    assert kinds["app:load_view"] == "route" and kinds["app:<module>"] == "main_block"
    assert kinds["worker:<module>"] == "module_level"
    assert "app:parse_body" not in kinds and None not in kinds.values()


# --- find_paths ---


def test_find_paths_returns_shortest_entry_to_call_site_paths(tool):
    result = _ok(tool, "find_paths", {"symbol": "yaml.load"})
    paths = result["paths"]
    # core.py's call is only reachable from tests (not entry points), so it has no path.
    assert [p["steps"][-1]["file"] for p in paths] == ["app.py", "app.py", "tool/runner.py"]
    first = paths[0]
    assert first["depth"] == 1 and first["heuristic"] is False
    assert first["steps"] == [
        {"file": "app.py", "line": 9, "edge": "entry", "symbol": "app:load_view"},
        {"file": "app.py", "line": 10, "edge": "call", "symbol": "app:parse_body"},
        {"file": "app.py", "line": 29, "edge": "vulnerable_call", "symbol": "yaml.load"},
    ]
    assert paths[1]["steps"][0]["symbol"] == "app:sync"
    assert paths[2]["steps"][0]["symbol"] == "tool.__main__:<module>"
    assert len(paths) <= 5


def test_find_paths_every_step_is_recorded_as_seen(tool):
    paths = _ok(tool, "find_paths", {"symbol": "yaml.load"})["paths"]
    for path in paths:
        for step in path["steps"]:
            assert (step["file"], step["line"]) in tool.seen_locations


def test_find_paths_respects_max_depth_and_rejects_more_than_8(tool):
    shallow = _ok(tool, "find_paths", {"symbol": "yaml.load", "max_depth": 0})
    assert shallow["paths"] == [] and "note" in shallow
    assert tool.call("find_paths", {"symbol": "yaml.load", "max_depth": 9}).ok is False
    assert tool.call("find_paths", {"symbol": "yaml.load", "max_depth": -1}).ok is False


def test_find_paths_prefers_non_heuristic_edges_and_marks_heuristic(index):
    # Make Service.run an entry point: its only route to yaml.load is the heuristic self.* edge.
    defs = [
        d.model_copy(update={"entry_kind": "route"}) if d.id == "lib.core:Service.run" else d
        for d in index.defs
    ]
    tool = AstTool(index.model_copy(update={"defs": defs}))
    paths = _ok(tool, "find_paths", {"symbol": "yaml.load"})["paths"]
    core = next(p for p in paths if p["steps"][-1]["file"] == "src/lib/core.py")
    assert core["heuristic"] is True
    assert [s["edge"] for s in core["steps"]] == [
        "entry",
        "heuristic_call",
        "call",
        "vulnerable_call",
    ]
    assert core["depth"] == 2


def test_find_paths_caps_at_five_paths(index):
    entries = [
        d.model_copy(update={"entry_kind": "route"}) if d.id.startswith("lib.core:") else d
        for d in index.defs
    ]
    many = index.model_copy(update={"defs": entries})
    assert len(_ok(AstTool(many), "find_paths", {"symbol": "yaml"})["paths"]) <= 5


def test_gate_accepts_a_find_paths_path(index, tool):  # AC-17 with the gate's rule 2
    path = _ok(tool, "find_paths", {"symbol": "yaml.load"})["paths"][0]["steps"]
    verdict = ModelVerdict.model_validate(
        {
            "verdict": "likely reachable",
            "confidence": "high",
            "call_path": path,
            "source_locations": [],
            "explanation": "Reached from a route.",
            "limitations": ["Static analysis only."],
        }
    )
    advisory = Advisory(
        id="GHSA-8q59-q68h-6hv4",
        aliases=[],
        summary="yaml.load",
        severity="high",
        fixed_versions=[],
        published=None,
        modified=None,
    )
    link = AdvisoryLink(
        url="https://osv.dev/vulnerability/GHSA-8q59-q68h-6hv4", source="osv", title=None
    )
    research = AdvisoryResearch(
        advisory_id=advisory.id,
        identified=True,
        not_identified_reason=None,
        from_cache=False,
        symbols=["yaml.load"],
        links=[link],
        evidence_urls=[],
        failure=None,
    )
    outcome = ReasoningOutcome(
        verdict=verdict, tool_calls=[], attempts=1, failure=None, failure_detail=None
    )
    candidate = Candidate(package="pyyaml", version="5.3", advisory=advisory, direct=True)
    finding = build_finding(candidate, research, outcome, index, seen_locations=tool.seen_locations)
    assert finding.verdict == "likely reachable" and finding.confidence == "high"


# --- determinism, size cap, seen_locations ---


def test_results_are_deterministic_and_instances_do_not_share_state(index):
    calls = [
        ("find_call_sites", {"symbol": "yaml"}),
        ("find_paths", {"symbol": "yaml.load"}),
        ("find_imports", {"module": "yaml"}),
        ("list_entry_points", {}),
    ]
    first, second = AstTool(index), AstTool(index)
    one = [first.call(n, a).content for n, a in calls]
    two = [second.call(n, a).content for n, a in calls]
    assert one == two
    assert [first.call(n, a).content for n, a in calls] == one
    assert first.seen_locations == second.seen_locations


def test_result_is_capped_at_8kb_and_pagination_covers_everything():
    tool = AstTool(_big_index())
    page = _ok(tool, "find_call_sites", {"symbol": "yaml.load", "limit": 50})
    assert len(page["sites"]) < 50 and page["next_offset"] == len(page["sites"])
    assert page["total"] == 120
    seen_lines = [s["line"] for s in page["sites"]]
    assert tool.seen_locations == {("big.py", n) for n in seen_lines}  # only what was returned
    collected = list(seen_lines)
    offset = page["next_offset"]
    while offset is not None:
        nxt = _ok(tool, "find_call_sites", {"symbol": "yaml.load", "limit": 50, "offset": offset})
        collected += [s["line"] for s in nxt["sites"]]
        offset = nxt["next_offset"]
    assert collected == list(range(1, 121))
    assert tool.seen_locations == {("big.py", n) for n in range(1, 121)}


def test_seen_locations_start_empty_and_collect_every_returned_location(tool):
    assert tool.seen_locations == set()
    _ok(tool, "find_call_sites", {"symbol": "yaml.load"})
    _ok(tool, "find_imports", {"module": "yaml"})
    _ok(tool, "find_callers", {"function_id": "app:parse_body"})
    _ok(tool, "list_entry_points", {})
    assert {("app.py", 29), ("src/lib/core.py", 14), ("tool/runner.py", 5)} <= tool.seen_locations
    assert ("app.py", 2) in tool.seen_locations  # an import
    assert ("app.py", 10) in tool.seen_locations  # a caller edge
    assert ("worker.py", 1) in tool.seen_locations  # an entry point


# --- invalid input ---


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("read_file", {"path": "app.py"}),
        ("submit_verdict", {"verdict": "uncertain"}),
        ("find_call_sites", {}),
        ("find_call_sites", {"symbol": ""}),
        ("find_call_sites", {"symbol": 5}),
        ("find_call_sites", {"symbol": "x" * 1000}),
        ("find_call_sites", {"symbol": "yaml", "limit": 0}),
        ("find_call_sites", {"symbol": "yaml", "limit": 51}),
        ("find_call_sites", {"symbol": "yaml", "limit": True}),
        ("find_call_sites", {"symbol": "yaml", "offset": -1}),
        ("find_call_sites", {"symbol": "yaml", "offset": "1"}),
        ("find_call_sites", {"symbol": "yaml", "path": "/etc/passwd"}),
        ("list_entry_points", {"limit": 1.5}),
        ("find_imports", ["yaml"]),
    ],
)
def test_bad_calls_return_an_error_result_and_never_raise(tool, name, arguments):
    result = tool.call(name, arguments)  # type: ignore[arg-type]
    assert result.ok is False
    assert "error" in json.loads(result.content)
    assert result.result_bytes <= MAX_RESULT_BYTES
    assert tool.seen_locations == set()


# --- default-argument equivalences (ADR-0037, AC-80) ---


@pytest.fixture(scope="module")
def default_loader_index() -> AnalysisIndex:
    raw = agent_index.build_index(str(FIXTURES / "pyyaml_default_loader"), SHA, ["yaml"])
    return AnalysisIndex.model_validate(raw)


def _full_load_paths(index: AnalysisIndex, version: str | None, package: str | None = "PyYAML"):
    tool = AstTool(index, package=package, version=version)
    return _ok(tool, "find_paths", {"symbol": "yaml.full_load"})["paths"]


def test_find_paths_reaches_both_default_loader_calls_from_the_main_block(  # AC-80
    default_loader_index,
):
    paths = _full_load_paths(default_loader_index, "5.3")
    ends = {(p["steps"][-1]["file"], p["steps"][-1]["line"]) for p in paths}
    assert ends == {("main.py", 13), ("main.py", 19)}
    for path in paths:
        assert path["steps"][0]["edge"] == "entry"
        assert path["steps"][0]["symbol"] == "main:<module>"
        assert path["steps"][-1]["edge"] == "vulnerable_call"
        assert path["steps"][-1]["symbol"] == "yaml.load"  # the name the repo calls
    helper = next(p for p in paths if p["steps"][-1]["line"] == 13)
    assert [s["symbol"] for s in helper["steps"][1:-1]] == ["main:_get_config"]


def test_find_paths_never_reaches_the_loader_or_unknown_argument_calls(  # AC-80
    default_loader_index,
):
    for symbol in ("yaml.full_load", "yaml.FullLoader"):
        tool = AstTool(default_loader_index, package="PyYAML", version="5.3")
        paths = _ok(tool, "find_paths", {"symbol": symbol})["paths"]
        ends = {(p["steps"][-1]["file"], p["steps"][-1]["line"]) for p in paths}
        assert not {e for e in ends if e[0] in ("settings.py", "dynamic.py")}
        assert ("main.py", 19) in ends


def test_find_paths_returns_nothing_on_pyyaml_6(default_loader_index):  # AC-80
    assert _full_load_paths(default_loader_index, "6.0") == []


@pytest.mark.parametrize(("package", "version"), [(None, None), (None, "5.3"), ("PyYAML", None)])
def test_no_package_or_version_applies_no_equivalence(  # AC-80
    default_loader_index, package, version
):
    assert _full_load_paths(default_loader_index, version, package) == []


def test_unparsable_version_finds_no_path(default_loader_index):  # AC-80
    assert _full_load_paths(default_loader_index, "not-a-version") == []


def test_find_call_sites_carries_implied_by_only_for_implied_sites(default_loader_index):  # AC-80
    tool = AstTool(default_loader_index, package="PyYAML", version="5.3")
    sites = _ok(tool, "find_call_sites", {"symbol": "yaml.full_load"})["sites"]
    assert [(s["file"], s["line"]) for s in sites] == [("main.py", 13), ("main.py", 19)]
    assert all(s["implied_by"] == "yaml.load" for s in sites)
    plain = _ok(tool, "find_call_sites", {"symbol": "yaml.load"})["sites"]
    assert len(plain) == 6 and all("implied_by" not in s for s in plain)


def test_find_call_sites_never_matches_a_loader_call_through_the_table(  # AC-80
    default_loader_index,
):
    tool = AstTool(default_loader_index, package="PyYAML", version="5.3")
    for symbol in ("yaml.full_load", "yaml.FullLoader"):
        sites = _ok(tool, "find_call_sites", {"symbol": symbol})["sites"]
        assert all(s["file"] == "main.py" for s in sites)


def test_find_call_sites_without_package_has_no_implied_sites(default_loader_index):  # AC-80
    tool = AstTool(default_loader_index)
    assert _ok(tool, "find_call_sites", {"symbol": "yaml.full_load"})["sites"] == []


# --- inferred source roots (ADR-0037, AC-81) ---


def _source_root_index(root: Path) -> AnalysisIndex:
    return AnalysisIndex.model_validate(agent_index.build_index(str(root), SHA, ["yaml"]))


@pytest.fixture(scope="module")
def source_root_index() -> AnalysisIndex:
    return _source_root_index(FIXTURES / "source_root")


def _loader_paths(index: AnalysisIndex) -> list[dict]:
    tool = AstTool(index, package="PyYAML", version="5.3.1")
    return _ok(tool, "find_paths", {"symbol": "yaml.FullLoader"})["paths"]


def _source_root_finding(index: AnalysisIndex):
    tool = AstTool(index, package="PyYAML", version="5.3.1")
    found = _ok(tool, "find_paths", {"symbol": "yaml.FullLoader"})["paths"]
    assert found
    steps = found[0]["steps"]
    verdict = ModelVerdict.model_validate(
        {
            "verdict": "likely reachable",
            "confidence": "high",
            "call_path": steps,
            "source_locations": [],
            "explanation": "Reached from the training script.",
            "limitations": ["Static analysis only."],
        }
    )
    advisory = Advisory(
        id="GHSA-8q59-q68h-6hv4",
        aliases=[],
        summary="yaml.FullLoader",
        severity="high",
        fixed_versions=[],
        published=None,
        modified=None,
    )
    link = AdvisoryLink(
        url="https://osv.dev/vulnerability/GHSA-8q59-q68h-6hv4", source="osv", title=None
    )
    research = AdvisoryResearch(
        advisory_id=advisory.id,
        identified=True,
        not_identified_reason=None,
        from_cache=False,
        symbols=["yaml.FullLoader"],
        links=[link],
        evidence_urls=[],
        failure=None,
    )
    outcome = ReasoningOutcome(
        verdict=verdict, tool_calls=[], attempts=1, failure=None, failure_detail=None
    )
    candidate = Candidate(package="pyyaml", version="5.3.1", advisory=advisory, direct=True)
    return build_finding(candidate, research, outcome, index, seen_locations=tool.seen_locations)


def test_find_paths_crosses_the_inferred_source_root_as_a_heuristic_path(  # AC-81
    source_root_index,
):
    (path,) = _loader_paths(source_root_index)
    assert path["heuristic"] is True
    steps = path["steps"]
    assert [(s["symbol"], s["edge"]) for s in steps] == [
        ("run.train:<module>", "entry"),
        ("run.train:main", "call"),
        ("lib.core.config:update_config", "heuristic_call"),
        ("yaml.FullLoader", "vulnerable_call"),
    ]
    assert steps[-1]["file"] == "lib/core/config.py"


def test_likely_reachable_through_a_source_root_is_capped_at_medium(source_root_index):  # AC-81
    finding = _source_root_finding(source_root_index)
    assert finding.verdict == "likely reachable"
    assert finding.confidence == "medium"  # the model said high
    assert any(
        "heuristic edges (name-based or an inferred import root)" in limitation
        for limitation in finding.limitations
    )


def _without_import_path_call(root: Path) -> None:
    (root / "run" / "_init_paths.py").write_text("import os.path as osp\n")


def _with_duplicate_suffix(root: Path) -> None:
    (root / "other" / "core").mkdir(parents=True)
    (root / "other" / "core" / "config.py").write_text(
        "def update_config(path):\n    return path\n"
    )


@pytest.mark.parametrize("variant", [_without_import_path_call, _with_duplicate_suffix])
def test_find_paths_returns_no_path_for_the_negative_variants(tmp_path, variant):  # AC-81
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES / "source_root", root)
    variant(root)
    assert _loader_paths(_source_root_index(root)) == []
