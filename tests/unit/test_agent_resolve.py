"""Sandbox agent `resolve`: lockfiles, pinned requirements and the no-uv cases (M1-TASK-06)."""

import base64
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from pir.models import PEP440_RE as MODEL_PEP440_RE
from pir.models.report import Dependency
from pir.sandbox_agent import __main__ as agent_main
from pir.sandbox_agent import protocol, resolve
from pir.sandbox_agent.protocol import BadInput

LOCKS = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "locks"
COMPILE_NOTE = "Versions were compiled with uv pip compile, not locked."
DYNAMIC = "dynamic metadata needs a build; not run"


def _never_called(*args, **kwargs):
    raise AssertionError("uv must not be called")


class _Recorder:
    def __init__(self, stdout: str = "requests==2.32.3\n", returncode: int = 0, stderr: str = ""):
        self.calls: list[tuple[list[str], dict]] = []
        self._result = (stdout, returncode, stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        stdout, code, stderr = self._result
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


def _run(name: str, mode: str = "lockfile-only", runner=_never_called, tmp_path=None) -> dict:
    return resolve.resolve_command(
        {"mode": mode, "python_version": "3.12"},
        repo_root=str(LOCKS / name),
        tmp_root=str(tmp_path or LOCKS),
        runner=runner,
    )


def _validate(result: dict) -> list[Dependency]:
    """Every record must be a valid Dependency once the host adds the OSV fields."""
    models = []
    for dep in result["dependencies"]:
        osv = {"osv_status": "checked" if dep["resolved"] else "not_queried", "osv_error": None}
        models.append(Dependency(**dep, **osv))
    return models


def _by_name(result: dict) -> dict[str, dict]:
    return {d["name"]: d for d in result["dependencies"]}


# --- lockfiles (AC-07) ---


def test_uv_lock_yields_exact_versions_with_transitives_and_groups():  # AC-07
    result = _run("uv")
    deps = _by_name(result)
    assert result["method"] == "lockfile" and result["lockfile"] == "uv.lock"
    assert result["needs_compile"] is False and "demo" not in deps
    assert deps["requests"]["version"] == "2.32.3" and deps["requests"]["direct"] is True
    assert deps["urllib3"]["version"] == "2.2.3" and deps["urllib3"]["direct"] is False
    assert deps["urllib3"]["groups"] == ["main"]
    assert deps["pytest"]["groups"] == ["dev"] and deps["iniconfig"]["groups"] == ["dev"]
    assert deps["gitdep"]["resolved"] is False
    assert deps["gitdep"]["unresolved_reason"] == "non-registry source"
    assert all(d["source"] == "uv.lock" for d in deps.values())
    _validate(result)


def test_poetry_lock_yields_every_package_with_groups_and_direct():  # AC-07
    result = _run("poetry")
    deps = _by_name(result)
    assert result["method"] == "lockfile" and result["lockfile"] == "poetry.lock"
    assert deps["flask"]["version"] == "3.0.3" and deps["flask"]["direct"] is True
    assert deps["jinja2"]["version"] == "3.1.4" and deps["jinja2"]["direct"] is False
    assert deps["pytest"]["groups"] == ["dev"]
    assert deps["localpkg"]["unresolved_reason"] == "non-registry source"
    _validate(result)


def test_pipfile_lock_reads_default_and_develop_with_pinned_versions():  # AC-07
    result = _run("pipfile")
    deps = _by_name(result)
    assert result["lockfile"] == "Pipfile.lock"
    assert deps["requests"]["version"] == "2.32.3" and deps["requests"]["groups"] == ["main"]
    assert deps["requests"]["direct"] is True and deps["urllib3"]["direct"] is False
    assert deps["pytest"]["groups"] == ["develop"]
    assert deps["mylib"]["unresolved_reason"] == "non-registry source"
    _validate(result)


def test_fully_pinned_requirements_follow_includes_and_note_limits():  # AC-07
    result = _run("pinned_reqs")
    deps = _by_name(result)
    assert result["method"] == "lockfile" and result["lockfile"] == "requirements.txt"
    assert {n: d["version"] for n, d in deps.items()} == {
        "jinja2": "3.1.4",
        "pyyaml": "6.0.2",
        "requests": "2.32.3",
    }
    assert all(d["source"] == "requirements.txt" and d["direct"] is True for d in deps.values())
    assert "ignored option --index-url" in result["notes"]
    assert any("transitive" in note for note in result["notes"])
    _validate(result)


@pytest.mark.parametrize("name", ["uv", "poetry", "pipfile", "pinned_reqs"])
def test_lockfile_resolution_never_reaches_uv_even_in_compile_mode(name, tmp_path):  # AC-07
    result = _run(name, mode="compile", runner=_never_called, tmp_path=tmp_path)
    assert result["method"] == "lockfile" and result["dependencies"]
    assert not (tmp_path / "uvwd").exists() and not (tmp_path / "pirin").exists()


def test_dependency_lists_are_sorted_and_deterministic():  # AC-07
    first, second = _run("uv"), _run("uv")
    assert first == second
    names = [d["name"] for d in first["dependencies"]]
    assert names == sorted(names)


def test_agent_version_pattern_matches_the_model_pattern():  # AC-07 (versions validated)
    assert resolve.PEP440_RE.pattern == MODEL_PEP440_RE.pattern
    assert resolve.PEP440_RE.flags == MODEL_PEP440_RE.flags


def test_an_invalid_version_in_a_lockfile_is_unresolved_not_guessed(tmp_path):  # AC-07, AC-09
    (tmp_path / "Pipfile.lock").write_text(
        '{"default": {"Bad_Pkg": {"version": "==not a version"}, "ok.pkg": {"version": "==1.0"}}}',
        encoding="utf-8",
    )
    result = resolve.resolve_command({"mode": "lockfile-only"}, repo_root=str(tmp_path))
    deps = _by_name(result)
    assert deps["bad-pkg"]["resolved"] is False
    assert deps["bad-pkg"]["unresolved_reason"] == "not a PEP 440 version"
    assert deps["ok-pkg"]["version"] == "1.0"  # PEP 503 normalized
    _validate(result)


def test_a_corrupt_lockfile_is_an_unreadable_manifest(tmp_path):  # AC-09 (invalid input)
    (tmp_path / "uv.lock").write_text("not [valid toml", encoding="utf-8")
    result = resolve.resolve_command(
        {"mode": "compile"}, repo_root=str(tmp_path), runner=_never_called
    )
    assert result["method"] == "failed" and result["dependencies"] == []
    assert "compile failed: unreadable manifest" in result["notes"]


# --- compile is deferred in lockfile-only mode (AC-08) ---


@pytest.mark.parametrize("name", ["unpinned_reqs", "pyproject_static"])
def test_unpinned_manifest_defers_compile_in_lockfile_only_mode(name):  # AC-08
    result = _run(name)
    assert result["needs_compile"] is True and result["dependencies"] == []


def test_unpinned_manifest_triggers_compile_and_notes_it(tmp_path):  # AC-08
    recorder = _Recorder(stdout="flask==3.0.3\nrequests==2.32.3\nidna==3.10\n")
    result = _run("unpinned_reqs", mode="compile", runner=recorder, tmp_path=tmp_path)
    assert len(recorder.calls) == 1
    assert result["method"] == "compiled" and result["compiled_from"] == "requirements.txt"
    assert COMPILE_NOTE in result["notes"]
    deps = _by_name(result)
    assert deps["idna"]["direct"] is False and deps["flask"]["direct"] is True
    assert {d["source"] for d in deps.values()} == {"uv pip compile"}
    _validate(result)


# --- dynamic metadata: nothing is built or run (AC-09) ---


@pytest.mark.parametrize("mode", ["lockfile-only", "compile"])
def test_setup_py_only_is_unresolved_without_a_uv_call(mode, tmp_path):  # AC-09
    result = _run("setup_py_only", mode=mode, runner=_never_called, tmp_path=tmp_path)
    deps = _by_name(result)
    assert result["needs_compile"] is False and result["method"] == "failed"
    assert set(deps) == {"requests", "flask-login"}
    assert {d["unresolved_reason"] for d in deps.values()} == {DYNAMIC}
    assert not os.path.exists("/tmp/pir-fixture-setup-py-ran")
    _validate(result)


def test_setup_cfg_only_names_are_unresolved_dynamic():  # AC-09
    deps = _by_name(_run("setup_cfg_only"))
    assert set(deps) == {"requests", "click"}
    assert {d["unresolved_reason"] for d in deps.values()} == {DYNAMIC}


@pytest.mark.parametrize("mode", ["lockfile-only", "compile"])
def test_dynamic_pyproject_is_not_compiled(mode, tmp_path):  # AC-09
    result = _run("pyproject_dynamic", mode=mode, runner=_never_called, tmp_path=tmp_path)
    assert result["needs_compile"] is False and result["method"] == "failed"
    assert DYNAMIC in result["notes"]


def test_bare_pipfile_is_unresolved():  # AC-09
    result = _run("bare_pipfile")
    deps = _by_name(result)
    assert set(deps) == {"django", "requests"}
    reasons = {d["unresolved_reason"] for d in deps.values()}
    assert reasons == {"Pipfile without Pipfile.lock is not supported"}
    _validate(result)


def test_repo_without_any_manifest_is_failed_with_a_note(tmp_path):  # AC-09
    result = resolve.resolve_command(
        {"mode": "compile"}, repo_root=str(tmp_path), runner=_never_called
    )
    assert result["method"] == "failed" and result["dependencies"] == []
    assert result["notes"] == ["No supported dependency manifest was found."]


# --- R3-1: includes stay inside the repository ---


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")


def _escape_repo(tmp_path: Path, include_line: str) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "tmp").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("evil==9.9\n", encoding="utf-8")
    (repo / "requirements.txt").write_text(f"requests>=2\n{include_line}\n", encoding="utf-8")
    return repo, outside


def _compile_inputs(repo: Path, tmp_path: Path) -> tuple[dict, str]:
    result = resolve.resolve_command(
        {"mode": "compile", "python_version": "3.12"},
        repo_root=str(repo),
        tmp_root=str(tmp_path / "tmp"),
        runner=_Recorder(),
    )
    return result, (tmp_path / "tmp" / "pirin" / "requirements.in").read_text(encoding="utf-8")


def _assert_refused(result: dict, text: str) -> None:
    assert "evil" not in text and "evil" not in str(result)
    assert text == "requests>=2\n"
    assert any(note.startswith("ignored option -r") for note in result["notes"])


def test_relative_escape_include_is_refused(tmp_path):  # R3-1
    repo, _ = _escape_repo(tmp_path, "-r ../outside.txt")
    _assert_refused(*_compile_inputs(repo, tmp_path))


def test_absolute_path_include_is_refused(tmp_path):  # R3-1
    repo, outside = _escape_repo(tmp_path, "placeholder")
    (repo / "requirements.txt").write_text(f"requests>=2\n-r {_posix(outside)}\n", encoding="utf-8")
    _assert_refused(*_compile_inputs(repo, tmp_path))


def test_in_tree_symlink_pointing_outside_is_refused(tmp_path):  # R3-1
    repo, outside = _escape_repo(tmp_path, "-r link.txt")
    try:
        os.symlink(outside, repo / "link.txt")
    except OSError:
        pytest.skip("symlinks are not available here")
    _assert_refused(*_compile_inputs(repo, tmp_path))


def test_a_refused_include_means_the_file_is_not_fully_pinned(tmp_path):  # R3-1, AC-07
    repo, _ = _escape_repo(tmp_path, "-r ../outside.txt")
    (repo / "requirements.txt").write_text("requests==2.0\n-r ../outside.txt\n", encoding="utf-8")
    result = resolve.resolve_command({"mode": "lockfile-only"}, repo_root=str(repo))
    assert result["needs_compile"] is True and result["method"] != "lockfile"


def test_a_root_manifest_symlinked_outside_the_repo_is_unreadable(tmp_path):  # R3-1
    repo, outside = _escape_repo(tmp_path, "")
    (repo / "requirements.txt").unlink()
    try:
        os.symlink(outside, repo / "requirements.txt")
    except OSError:
        pytest.skip("symlinks are not available here")
    result = resolve.resolve_command({"mode": "lockfile-only"}, repo_root=str(repo))
    assert result["method"] == "failed" and "compile failed: unreadable manifest" in result["notes"]
    assert "evil" not in str(result)


def test_includes_deeper_than_five_levels_are_not_followed(tmp_path):  # R3-1
    for level in range(8):
        name = "requirements.txt" if level == 0 else f"r{level}.txt"
        include = f"-r r{level + 1}.txt\n" if level < 7 else ""
        (tmp_path / name).write_text(f"pkg{level}==1.0\n{include}", encoding="utf-8")
    result = resolve.resolve_command({"mode": "lockfile-only"}, repo_root=str(tmp_path))
    assert result["needs_compile"] is True  # the refused deep include blocks "fully pinned"


def test_includes_within_five_levels_and_circular_includes_resolve(tmp_path):  # R3-1
    (tmp_path / "requirements.txt").write_text("a==1.0\n-r r1.txt\n", encoding="utf-8")
    (tmp_path / "r1.txt").write_text("b==2.0\n-r requirements.txt\n", encoding="utf-8")
    result = resolve.resolve_command({"mode": "lockfile-only"}, repo_root=str(tmp_path))
    assert {d["name"]: d["version"] for d in result["dependencies"]} == {"a": "1.0", "b": "2.0"}


# --- protocol wiring ---


def test_resolve_is_registered_with_the_agent_dispatcher():  # AC-07
    assert agent_main.HANDLERS["resolve"] is resolve.resolve_command


def test_bad_mode_and_bad_python_version_are_bad_input(tmp_path):  # AC-09 (invalid input)
    for args in ({}, {"mode": "install"}, {"mode": "compile", "python_version": "3.12; rm -rf /"}):
        with pytest.raises(BadInput):
            resolve.resolve_command(args, repo_root=str(tmp_path))
    with pytest.raises(BadInput):
        resolve.resolve_command({"mode": "compile"}, repo_root=str(tmp_path / "missing"))


def test_result_line_round_trips_through_the_protocol(tmp_path):  # AC-07
    (tmp_path / "requirements.txt").write_text("requests==2.0\n", encoding="utf-8")
    expected = resolve.resolve_command({"mode": "lockfile-only"}, repo_root=str(tmp_path))
    out = io.StringIO()
    handlers = {"resolve": lambda args: resolve.resolve_command(args, repo_root=str(tmp_path))}
    args = base64.b64encode(json.dumps({"mode": "lockfile-only"}).encode()).decode()
    assert protocol.run_command(["resolve", "--args-b64", args], handlers, stdout=out) == 0
    assert protocol.decode_result(out.getvalue()) == expected
