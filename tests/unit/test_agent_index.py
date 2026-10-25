"""Sandbox agent ast index on synthetic fixture repos, in-process (M1-TASK-05; AC-14, AC-15)."""

import base64
import io
import json
import os
import shutil
from pathlib import Path

import pytest

from pir.models.index import AnalysisIndex
from pir.sandbox_agent import index as agent_index
from pir.sandbox_agent import protocol

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "repos"
SHA = "b" * 40


def _build(name: str, targets: list[str], root: Path | None = None) -> dict:
    return agent_index.build_index(str(root or FIXTURES / name), SHA, targets)


def _sites(index: dict) -> dict[tuple[str, int], dict]:
    return {(s["file"], s["line"]): s for s in index["target_sites"]}


@pytest.fixture(scope="module")
def aliases() -> dict:
    return _build("aliases", ["yaml", "jinja2"])


@pytest.fixture(scope="module")
def entrypoints() -> dict:
    return _build("entrypoints", ["yaml"])


# --- determinism (AC-14) and model validity ---


def test_same_fixture_twice_is_byte_identical(tmp_path):  # AC-14
    first = agent_index.index_json(_build("aliases", ["yaml", "jinja2"]))
    second = agent_index.index_json(_build("aliases", ["jinja2", "yaml"]))
    assert first == second
    copy = tmp_path / "copy"
    shutil.copytree(FIXTURES / "aliases", copy)
    assert agent_index.index_json(_build("aliases", ["yaml", "jinja2"], copy)) == first


def test_result_line_bytes_are_identical_across_runs():  # AC-14
    one = protocol.encode_result(_build("entrypoints", ["yaml"]))
    two = protocol.encode_result(_build("entrypoints", ["yaml"]))
    assert one == two


def test_lists_are_sorted_and_hold_no_wall_clock_values(aliases):  # AC-14
    assert [f["path"] for f in aliases["files"]] == sorted(f["path"] for f in aliases["files"])
    keys = [(s["file"], s["line"], s["col"]) for s in aliases["target_sites"]]
    assert keys == sorted(keys)
    assert all(isinstance(v, int) for v in aliases["stats"].values())
    assert "time" not in json.dumps(aliases["stats"])


@pytest.mark.parametrize(
    ("name", "targets"),
    [("aliases", ["yaml", "jinja2"]), ("entrypoints", ["yaml"]), ("hostile", ["yaml"])],
)
def test_output_validates_against_analysis_index(name, targets):  # AC-15
    index = AnalysisIndex.model_validate(_build(name, targets))
    assert index.agent_protocol == 1
    assert index.commit_sha == SHA
    assert index.target_modules == sorted(targets)


# --- alias handling (AC-15) ---


def test_import_m_as_a_and_from_m_import_f_as_g_are_resolved(aliases):  # AC-15
    sites = _sites(aliases)
    assert sites[("main_alias.py", 10)]["target"] == "yaml.load"  # y.load(...)
    assert (
        sites[("main_alias.py", 14)]["target"] == "yaml.load"
    )  # ld(...) from yaml import load as ld
    assert sites[("main_alias.py", 18)]["target"] == "yaml.safe_load"
    assert {sites[("main_alias.py", n)]["kind"] for n in (10, 14, 18)} == {"call"}


def test_dotted_imports_with_and_without_alias_are_resolved(aliases):  # AC-15
    sites = _sites(aliases)
    assert sites[("main_alias.py", 22)]["target"] == "yaml.constructor.FullConstructor"
    assert sites[("main_alias.py", 26)]["target"] == "yaml.loader.Loader"  # import a.b as ab


def test_function_passed_without_a_call_is_a_reference(aliases):  # AC-15
    site = _sites(aliases)[("main_alias.py", 30)]
    assert site["kind"] == "reference"
    assert site["target"] == "yaml.load"
    assert site["caller"] == "main_alias:passes_reference"
    assert site["snippet"] == "return list(map(yaml.load, items))"


def test_function_level_and_relative_imports_are_bound(aliases):  # AC-15
    sites = _sites(aliases)
    assert sites[("main_alias.py", 36)]["target"] == "jinja2.Template"  # import inside a function
    assert sites[("pkg/helpers.py", 4)]["target"] == "yaml.safe_load"
    by_line = {(r["file"], r["line"]): r for r in aliases["imports"]}
    assert by_line[("pkg/sub/mod.py", 1)]["module"] == "pkg"  # from .. import helpers
    assert by_line[("pkg/sub/mod.py", 1)]["name"] == "helpers"
    assert (
        by_line[("pkg/sub/mod.py", 2)]["module"] == "pkg.helpers"
    )  # from ..helpers import run as r
    assert by_line[("pkg/sub/mod.py", 2)]["bound_as"] == "r"
    assert by_line[("pkg/sub/mod.py", 3)]["module"] == "pkg.sub"  # from . import sibling
    assert by_line[("pkg/__init__.py", 1)]["module"] == "pkg.helpers"  # from .helpers import run


def test_import_records_carry_alias_aware_bound_as(aliases):  # AC-15
    rows = {(r["line"]): r for r in aliases["imports"] if r["file"] == "main_alias.py"}
    assert (rows[1]["module"], rows[1]["asname"], rows[1]["bound_as"]) == ("yaml", "y", "y")
    assert (rows[2]["module"], rows[2]["bound_as"]) == ("yaml.constructor", "yaml")
    assert (rows[3]["module"], rows[3]["asname"], rows[3]["bound_as"]) == (
        "yaml.loader",
        "yl",
        "yl",
    )
    assert (rows[4]["module"], rows[4]["name"], rows[4]["bound_as"]) == ("yaml", "load", "ld")


def test_parameter_shadowing_an_import_is_not_a_site(aliases):  # AC-15
    assert not [s for s in aliases["target_sites"] if s["caller"] == "main_alias:shadowed"]


def test_only_target_modules_produce_sites(aliases):
    assert all(s["target"].split(".")[0] in ("yaml", "jinja2") for s in aliases["target_sites"])
    assert not _build("aliases", ["lxml"])["target_sites"]


def test_relative_import_beyond_the_repo_is_kept_unresolved_without_binding(aliases):
    row = next(r for r in aliases["imports"] if r["line"] == 4 and r["file"] == "pkg/sub/mod.py")
    assert row["module"] == "..."
    assert row["name"] == "beyond_the_repo"


def test_snippet_is_capped_and_deep_attribute_chains_do_not_crash(tmp_path):  # AC-15
    (tmp_path / "chain.py").write_text("import yaml\nx = yaml" + ".b" * 400 + "\n")
    index = _build("-", ["yaml"], tmp_path)
    assert index["files"][0]["parsed"] is True
    (site,) = index["target_sites"]
    assert len(site["snippet"]) == 200
    assert site["target"].startswith("yaml.b.b")
    AnalysisIndex.model_validate(index)


# --- defs, entry kinds, edges ---


def test_entry_kinds(entrypoints):
    kinds = {d["id"]: d["entry_kind"] for d in entrypoints["defs"]}
    assert kinds["app:load_view"] == "route"  # @app.route(...)
    assert kinds["app:health"] == "route"  # @app.get("/health")
    assert kinds["app:cli"] == "cli"  # @click.group()
    assert kinds["app:sync"] == "cli"  # @cli.command()
    assert kinds["app:<module>"] == "main_block"
    assert kinds["tool.__main__:<module>"] == "script"
    assert kinds["worker:<module>"] == "module_level"
    assert kinds["app:parse_body"] is None
    assert kinds["plain:<module>"] is None


def test_def_ids_strip_src_and_use_qualified_names(entrypoints):
    ids = {d["id"] for d in entrypoints["defs"]}
    assert "lib.core:Service.run" in ids
    assert "lib.core:Service.helper.<locals>.inner" in ids
    assert not any(i.startswith("src.") for i in ids)
    load_view = next(d for d in entrypoints["defs"] if d["id"] == "app:load_view")
    assert load_view["decorators"] == ["app.route"]
    assert load_view["file"] == "app.py"
    assert load_view["end_line"] >= load_view["line"]


def test_caller_of_a_nested_function_site_is_its_qualified_id(entrypoints):
    site = next(s for s in entrypoints["target_sites"] if s["file"] == "src/lib/core.py")
    assert site["caller"] == "lib.core:Service.helper.<locals>.inner"
    assert site["target"] == "yaml.load"


def test_test_files_are_flagged(entrypoints):
    flags = {f["path"]: f["is_test"] for f in entrypoints["files"]}
    assert flags["tests/test_app.py"] is True
    assert flags["app.py"] is False
    assert flags["src/lib/core.py"] is False


def test_edges_resolve_direct_calls_and_flag_heuristics(entrypoints):
    edges = {(e["caller"], e["callee"]): e for e in entrypoints["edges"]}
    direct = edges[("app:load_view", "app:parse_body")]
    assert direct["heuristic"] is False and direct["file"] == "app.py"
    assert edges[("app:<module>", "app:main")]["heuristic"] is False  # call in the main block
    assert edges[("lib.core:Service.run", "lib.core:Service.helper")]["heuristic"] is True  # self.*
    assert ("lib.core:Service.run", "lib.core:Service.missing") not in edges
    assert edges[("tool.__main__:<module>", "tool.runner:start")]["heuristic"] is False
    assert edges[("tests.test_app:test_service", "lib.core:Service.__init__")]["heuristic"] is False


def test_edges_follow_aliased_and_relative_imports(aliases):
    edges = {(e["caller"], e["callee"], e["line"]) for e in aliases["edges"]}
    assert ("pkg.sub.mod:call_all", "pkg.helpers:run", 8) in edges  # helpers.run(...)
    assert (
        "pkg.sub.mod:call_all",
        "pkg.helpers:run",
        9,
    ) in edges  # r(...) from ..helpers import run as r
    assert ("pkg.helpers:Runner.go", "pkg.helpers:run", 9) in edges
    assert (
        "pkg.sub.mod:<module>",
        "pkg.sub.sibling:<module>",
        3,
    ) in edges  # import executes module


# --- hostile input (security review finding 1/6) ---


def test_hostile_files_are_skipped_with_a_reason_and_the_index_completes():  # SEC-1/6
    index = _build("hostile", ["yaml"])
    reasons = {f["path"]: f["skip_reason"] for f in index["files"]}
    assert reasons["deep_nesting.py"] == "nesting too deep"
    assert reasons["null_byte.py"] == "null bytes"
    assert reasons["lambda_stack.py"] == "out of memory"
    assert reasons["syntax_error.py"] == "syntax error"
    assert reasons["bad_encoding.py"] == "syntax error"
    assert reasons["ok.py"] is None
    assert [f["parsed"] for f in index["files"] if f["path"] == "ok.py"] == [True]
    assert [s["target"] for s in index["target_sites"]] == ["yaml.load"]  # still analysed
    assert index["stats"]["files_skipped"] == 5


def test_one_megabyte_single_line_is_skipped_with_a_reason(tmp_path):  # SEC-1/6
    under = "x = 1" + " + 1" * 249_000 + "\n"  # a single line just under the size cap
    over = "x = 1" + " + 1" * 251_000 + "\n"  # a single line over MAX_PY_FILE_BYTES
    (tmp_path / "under.py").write_text(under)
    (tmp_path / "over.py").write_text(over)
    (tmp_path / "fine.py").write_text("import yaml\nyaml.load('a')\n")
    assert len(under) < agent_index.MAX_PY_FILE_BYTES < len(over)
    index = _build("-", ["yaml"], tmp_path)
    reasons = {f["path"]: f["skip_reason"] for f in index["files"]}
    assert reasons["under.py"] == "nesting too deep"
    assert reasons["over.py"] == "file over the size limit"
    assert reasons["fine.py"] is None
    assert len(index["target_sites"]) == 1
    AnalysisIndex.model_validate(index)


def test_unreadable_and_non_regular_files_do_not_stop_the_index(tmp_path):
    (tmp_path / "ok.py").write_text("import yaml\nyaml.load('a')\n")
    (tmp_path / "linked.py").symlink_to(tmp_path / "ok.py") if _can_symlink(tmp_path) else None
    index = _build("-", ["yaml"], tmp_path)
    assert [f["path"] for f in index["files"]] == ["ok.py"]  # symlinks are never followed
    assert len(index["target_sites"]) == 1


def _can_symlink(tmp_path: Path) -> bool:
    probe = tmp_path / "probe-link"
    try:
        os.symlink("ok.py", probe)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def test_vendored_and_cache_directories_are_not_indexed(tmp_path):
    for name in (".venv", "node_modules", ".git", "__pycache__", "site-packages"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "evil.py").write_text("import yaml\nyaml.load('x')\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    index = _build("-", ["yaml"], tmp_path)
    assert [f["path"] for f in index["files"]] == ["app.py"]
    assert index["stats"]["dirs_skipped"] == 5


def test_index_never_executes_repository_code(tmp_path):
    marker = tmp_path / "marker.txt"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    (repo / "setup.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    index = _build("-", ["yaml"], repo)
    assert index["stats"]["files_parsed"] == 2
    assert not marker.exists()


def test_exceeding_an_output_cap_sets_truncated(monkeypatch):
    assert _build("aliases", ["yaml", "jinja2"])["truncated"] is False
    monkeypatch.setattr(agent_index, "MAX_SITES", 2)
    index = _build("aliases", ["yaml", "jinja2"])
    assert index["truncated"] is True
    assert len(index["target_sites"]) == 2
    assert index["stats"]["target_sites_dropped"] == 1
    AnalysisIndex.model_validate(index)


def test_file_walk_limit_sets_truncated(monkeypatch):
    monkeypatch.setattr(agent_index, "MAX_INDEXED_FILES", 3)
    index = _build("aliases", ["yaml"])
    assert index["truncated"] is True
    assert len(index["files"]) == 3


def test_empty_repository_gives_an_empty_valid_index(tmp_path):
    index = _build("-", ["yaml"], tmp_path)
    assert index["files"] == [] and index["truncated"] is False
    AnalysisIndex.model_validate(index)


# --- command arguments ---


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        "yaml",
        ["yaml.constructor"],
        ["../x"],
        ["a b"],
        [1],
        [""],
        ["yaml;rm"],
        ["a"] * 101,
    ],
)
def test_bad_target_lists_are_bad_input(bad):
    with pytest.raises(protocol.BadInput):
        agent_index.normalize_targets(bad)


def test_targets_are_deduplicated_and_sorted():
    assert agent_index.normalize_targets(["jinja2", "yaml", "jinja2"]) == ["jinja2", "yaml"]


def test_index_command_needs_a_valid_sha_and_an_existing_repo(tmp_path):
    root = str(FIXTURES / "aliases")
    with pytest.raises(protocol.BadInput):
        agent_index.index_command({"targets": ["yaml"], "sha": "main"}, repo_root=root)
    with pytest.raises(protocol.BadInput):
        agent_index.index_command({"targets": ["yaml"]}, repo_root=root)
    with pytest.raises(protocol.BadInput):
        agent_index.index_command(
            {"targets": ["yaml"], "sha": SHA}, repo_root=str(tmp_path / "missing")
        )


def test_index_command_through_the_protocol_emits_a_valid_index():
    root = str(FIXTURES / "aliases")
    args = base64.b64encode(json.dumps({"targets": ["yaml"], "sha": SHA}).encode()).decode()
    handlers = {"index": lambda a: agent_index.index_command(a, repo_root=root)}
    out, err = io.StringIO(), io.StringIO()
    code = protocol.run_command(["index", "--args-b64", args], handlers, out, err)
    assert code == 0
    assert out.getvalue().count("\n") == 1
    index = AnalysisIndex.model_validate(protocol.decode_result(out.getvalue()))
    assert any(s.target == "yaml.load" and s.kind == "reference" for s in index.target_sites)


def test_module_name_for_paths():
    assert agent_index.module_name_for("pkg/mod.py") == ("pkg.mod", False)
    assert agent_index.module_name_for("pkg/__init__.py") == ("pkg", True)
    assert agent_index.module_name_for("src/pkg/mod.py") == ("pkg.mod", False)
    assert agent_index.module_name_for("setup.py") == ("setup", False)
    assert agent_index.module_name_for("__init__.py") == ("__init__", True)
