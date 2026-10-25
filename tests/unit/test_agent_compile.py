"""Sandbox agent `resolve --compile`: sanitized input and the isolated uv call (M1-TASK-06)."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from pir.models.report import Dependency
from pir.sandbox_agent import resolve
from pir.sandbox_agent.protocol import AgentError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOCKS = FIXTURES / "repos" / "locks"
DYNAMIC = "dynamic metadata needs a build; not run"
PYTHON313 = "/usr/local/bin/python3.13"


def _load_canary():
    spec = importlib.util.spec_from_file_location("uv_canary_fixture", FIXTURES / "uv_canary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canary = _load_canary()


class _Recorder:
    """Stands in for subprocess.run and records what the agent would have spawned."""

    def __init__(self, stdout="", returncode=0, stderr="", raises=None, writes=None):
        self.calls: list[dict] = []
        self.argvs: list[list[str]] = []
        self._reply = (stdout, returncode, stderr)
        self._raises = raises
        self._writes = writes

    def __call__(self, argv, **kwargs):
        cwd = kwargs["cwd"]
        self.argvs.append(list(argv))
        self.calls.append(
            {
                **kwargs,
                "cwd_listing_at_call": sorted(os.listdir(cwd)),
                "input_at_call": Path(argv[3]).read_text(encoding="utf-8"),
            }
        )
        if self._raises:
            raise self._raises
        if self._writes:
            (Path(cwd) / "-").write_text(self._writes, encoding="utf-8")
        stdout, code, stderr = self._reply
        return subprocess.CompletedProcess(argv, code, stdout, stderr)


def _validate(result: dict) -> None:
    for dep in result["dependencies"]:
        osv = {"osv_status": "checked" if dep["resolved"] else "not_queried", "osv_error": None}
        Dependency(**dep, **osv)


def _compile(repo: Path, tmp_path: Path, recorder: _Recorder, python_version: str = "3.12") -> dict:
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir(exist_ok=True)
    result = resolve.resolve_command(
        {"mode": "compile", "python_version": python_version},
        repo_root=str(repo),
        tmp_root=str(tmp_root),
        runner=recorder,
    )
    _validate(result)
    return result


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    return repo


def _by_name(result: dict) -> dict[str, dict]:
    return {d["name"]: d for d in result["dependencies"]}


@pytest.fixture
def canary_repo(tmp_path):
    repo = tmp_path / "repo"
    try:
        canary.write_tree(repo, canary.uv_canary_tree())
    except OSError:
        pytest.skip("symlinks are not available here")
    return repo


# --- the uv call (round 2 findings 1 and 2) ---


def test_uv_runs_from_a_fresh_empty_directory_outside_the_repo(canary_repo, tmp_path):  # round 2
    recorder = _Recorder(stdout="requests==2.32.3\npyyaml==6.0.2\n")
    _compile(canary_repo, tmp_path, recorder)
    (call,) = recorder.calls
    cwd = Path(call["cwd"])
    assert cwd == tmp_path / "tmp" / "uvwd" and call["cwd_listing_at_call"] == []
    assert not str(cwd.resolve()).startswith(str(canary_repo.resolve()))


def test_a_pre_existing_uvwd_fails_the_step_without_running_uv(canary_repo, tmp_path):  # round 2
    (tmp_path / "tmp" / "uvwd").mkdir(parents=True)
    recorder = _Recorder(stdout="requests==2.32.3\n")
    with pytest.raises(AgentError, match="already exists"):
        _compile(canary_repo, tmp_path, recorder)
    assert recorder.calls == []


def test_a_pre_existing_pirin_fails_the_step_without_running_uv(canary_repo, tmp_path):  # round 2
    (tmp_path / "tmp" / "pirin").mkdir(parents=True)
    recorder = _Recorder(stdout="requests==2.32.3\n")
    with pytest.raises(AgentError, match="already exists"):
        _compile(canary_repo, tmp_path, recorder)
    assert recorder.calls == []


def test_uv_argv_has_the_isolation_flags_and_an_input_outside_the_repo(canary_repo, tmp_path):
    recorder = _Recorder(stdout="requests==2.32.3\n")
    _compile(canary_repo, tmp_path, recorder, python_version="3.11")  # round 2, findings 1 and 2
    (argv,) = recorder.argvs
    assert argv[:3] == [resolve.UV_BIN, "pip", "compile"]
    for flag in ("--no-build", "--no-config", "--no-sources", "--no-header", "--no-annotate"):
        assert flag in argv
    assert argv[argv.index("--python") + 1] == PYTHON313
    assert argv[argv.index("--index-url") + 1] == "https://pypi.org/simple"
    assert argv[argv.index("--python-version") + 1] == "3.11"
    assert argv[argv.index("--output-file") + 1] == "-" and "--quiet" in argv
    input_path = Path(argv[3])
    assert input_path.is_absolute() and input_path == tmp_path / "tmp" / "pirin" / "requirements.in"
    assert not str(input_path.resolve()).startswith(str(canary_repo.resolve()))
    assert not any(str(canary_repo) in part for part in argv)  # no repo path anywhere in argv


def test_uv_env_is_the_fixed_environment_not_os_environ(canary_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/repo/.venv")  # round 2
    monkeypatch.setenv("CONDA_PREFIX", "/conda")
    monkeypatch.setenv("UV_INDEX_URL", "https://evil.invalid/simple")
    recorder = _Recorder(stdout="requests==2.32.3\n")
    _compile(canary_repo, tmp_path, recorder)
    env = recorder.calls[0]["env"]
    assert env == resolve.UV_ENV
    assert env["UV_PYTHON_DOWNLOADS"] == "never" and env["UV_NO_BUILD"] == "1"
    assert env["UV_NO_CONFIG"] == "1" and env["HOME"] == "/tmp" and env["UV_CACHE_DIR"] == "/tmp/uv"
    for absent in ("VIRTUAL_ENV", "CONDA_PREFIX", "UV_INDEX_URL", "PYTHONPATH"):
        assert absent not in env


def test_sanitized_input_holds_only_allowlisted_lines(canary_repo, tmp_path):  # round 2
    recorder = _Recorder(stdout="requests==2.32.3\npyyaml==6.0.2\n")
    _compile(canary_repo, tmp_path, recorder)
    text = recorder.calls[0]["input_at_call"]
    assert text.splitlines() == canary.SANITIZED_LINES
    for line in text.splitlines():
        assert resolve.ALLOWED_REQUIREMENT_RE.fullmatch(line)
        assert not line.startswith("-") and "://" not in line and "@" not in line
    assert not any(leak in text for leak in ("canary.invalid", "git+", "./local", "outside.txt"))


def test_options_become_notes_and_non_registry_requirements_are_unresolved(canary_repo, tmp_path):
    result = _compile(canary_repo, tmp_path, _Recorder(stdout="requests==2.32.3\n"))  # round 2
    for flag in canary.DROPPED_OPTIONS:
        assert f"ignored option {flag}" in result["notes"]
    assert any(n.startswith("ignored option -r") for n in result["notes"])
    deps = _by_name(result)
    for name in canary.NON_REGISTRY_NAMES:
        assert deps[name]["resolved"] is False
        assert deps[name]["unresolved_reason"] == "non-registry source"
    assert deps["requests"]["direct"] is True


def test_nothing_in_the_canary_repo_is_executed_or_read_as_config(canary_repo, tmp_path):  # round 2
    recorder = _Recorder(stdout="requests==2.32.3\n")
    _compile(canary_repo, tmp_path, recorder)
    assert not os.path.exists(canary.CANARY_MARKER)
    assert recorder.calls[0]["cwd"] != str(canary_repo)
    assert "canary.invalid" not in recorder.calls[0]["input_at_call"]


# --- compile results ---


def test_static_pyproject_is_compiled_with_normalized_names(tmp_path):  # AC-08
    recorder = _Recorder(stdout="certifi==2024.8.30\npyyaml==6.0.2\nrequests==2.32.3\n")
    result = _compile(LOCKS / "pyproject_static", tmp_path, recorder)
    assert result["method"] == "compiled" and result["compiled_from"] == "pyproject.toml"
    assert result["python_version"] == "3.12"
    assert "Versions were compiled with uv pip compile, not locked." in result["notes"]
    assert recorder.calls[0]["input_at_call"].splitlines() == [
        "Requests>=2.31",
        "PyYAML[extra] ; python_version > '3.8'",
    ]
    deps = _by_name(result)
    assert deps["certifi"]["direct"] is False and deps["requests"]["direct"] is True
    assert deps["pkg"]["unresolved_reason"] == "non-registry source"
    assert deps["gitpkg"]["unresolved_reason"] == "non-registry source"
    assert "pytest" not in deps  # optional groups are not compiled


def test_requires_python_raises_the_compile_target(tmp_path):  # AC-08
    recorder = _Recorder(stdout="requests==2.32.3\n")
    _compile(LOCKS / "pyproject_static", tmp_path, recorder, python_version="3.9")
    argv = recorder.argvs[0]
    assert argv[argv.index("--python-version") + 1] == "3.12"


def test_uv_output_versions_are_validated_and_sorted(tmp_path):  # AC-08
    recorder = _Recorder(stdout="Zeta_Pkg==2.0\nalpha==1.0\nweird==not.a.version\n# comment\n")
    result = _compile(LOCKS / "unpinned_reqs", tmp_path, recorder)
    assert [d["name"] for d in result["dependencies"]] == ["alpha", "weird", "zeta-pkg"]
    assert _by_name(result)["weird"]["unresolved_reason"] == "not a PEP 440 version"


def test_output_file_dash_written_to_the_uv_directory_is_read(tmp_path):  # AC-08
    recorder = _Recorder(stdout="", writes="requests==2.32.3\n")
    result = _compile(LOCKS / "unpinned_reqs", tmp_path, recorder)
    assert _by_name(result)["requests"]["version"] == "2.32.3"


def test_success_without_any_output_is_a_failed_compile(tmp_path):  # AC-09
    result = _compile(LOCKS / "unpinned_reqs", tmp_path, _Recorder(stdout=""))
    deps = _by_name(result)
    assert result["method"] == "failed"
    assert deps["flask"]["unresolved_reason"].startswith("compile failed: ")


# --- failures (AC-09) ---


def test_a_build_needed_uv_error_discards_output_and_marks_names_dynamic(tmp_path):  # AC-09
    stderr = (LOCKS / "uv_no_build_sdist.stderr.txt").read_text(encoding="utf-8")
    recorder = _Recorder(stdout="requests==2.32.3\n", returncode=1, stderr=stderr)
    result = _compile(LOCKS / "unpinned_reqs", tmp_path, recorder)
    assert result["method"] == "failed" and result["lockfile"] is None
    deps = _by_name(result)
    assert set(deps) == {"flask", "requests"}
    assert {d["unresolved_reason"] for d in deps.values()} == {DYNAMIC}
    assert DYNAMIC in result["notes"]


def test_the_pinned_uv_fixture_text_is_recognised_as_build_needed():  # AC-09
    stderr = (LOCKS / "uv_no_build_sdist.stderr.txt").read_text(encoding="utf-8")
    other = (LOCKS / "uv_package_not_found.stderr.txt").read_text(encoding="utf-8")
    assert resolve.BUILD_NEEDED_RE.search(stderr)
    assert not resolve.BUILD_NEEDED_RE.search(other)


def test_another_uv_error_is_a_compile_failure_with_the_stderr_tail(tmp_path):  # AC-09
    stderr = (LOCKS / "uv_package_not_found.stderr.txt").read_text(encoding="utf-8")
    result = _compile(LOCKS / "unpinned_reqs", tmp_path, _Recorder(returncode=1, stderr=stderr))
    deps = _by_name(result)
    reason = deps["flask"]["unresolved_reason"]
    assert reason == "compile failed: " + stderr.strip()
    assert result["method"] == "failed" and reason in result["notes"]


def test_the_compile_failure_reason_keeps_only_the_last_500_characters(tmp_path):  # AC-09
    stderr = "x" * 2000 + "THE-END"
    result = _compile(LOCKS / "unpinned_reqs", tmp_path, _Recorder(returncode=2, stderr=stderr))
    reason = _by_name(result)["flask"]["unresolved_reason"]
    assert reason == "compile failed: " + stderr[-500:]


def test_uv_timeout_and_a_missing_uv_binary_are_compile_failures(tmp_path):  # AC-09
    timeout = subprocess.TimeoutExpired(cmd="uv", timeout=1)
    result = _compile(LOCKS / "unpinned_reqs", tmp_path, _Recorder(raises=timeout))
    assert _by_name(result)["flask"]["unresolved_reason"].startswith("compile failed: uv timed out")
    other = tmp_path / "second"
    other.mkdir()
    missing = _Recorder(raises=FileNotFoundError("uv"))
    result = _compile(LOCKS / "unpinned_reqs", other, missing)
    assert "cannot start uv" in _by_name(result)["flask"]["unresolved_reason"]


def test_compile_on_a_setup_py_fixture_runs_nothing(tmp_path):  # security round 1
    recorder = _Recorder(stdout="requests==2.32.3\n")
    result = _compile(LOCKS / "setup_py_only", tmp_path, recorder)
    assert recorder.argvs == []  # no uv call at all, so no build can happen
    assert all(d["unresolved_reason"] == DYNAMIC for d in result["dependencies"])
    for argv in recorder.argvs:  # and were it ever called, it would carry --no-build
        assert "--no-build" in argv


def test_only_non_registry_requirements_need_no_uv_call(tmp_path):  # AC-09
    repo = _repo(tmp_path, {"requirements.in": "git+https://x.invalid/r.git#egg=only\n"})
    recorder = _Recorder()
    result = _compile(repo, tmp_path, recorder)
    assert recorder.argvs == []
    assert _by_name(result)["only"]["unresolved_reason"] == "non-registry source"


def test_requirements_in_wins_over_an_unpinned_requirements_txt(tmp_path):  # AC-08
    repo = _repo(tmp_path, {"requirements.in": "alpha\n", "requirements.txt": "beta>=1\n"})
    recorder = _Recorder(stdout="alpha==1.0\n")
    result = _compile(repo, tmp_path, recorder)
    assert result["compiled_from"] == "requirements.in"
    assert recorder.calls[0]["input_at_call"] == "alpha\n"


def test_a_pyproject_without_a_project_table_falls_through_to_requirements_in(tmp_path):  # AC-08
    repo = _repo(
        tmp_path,
        {"pyproject.toml": "[tool.ruff]\nline-length = 100\n", "requirements.in": "alpha\n"},
    )
    result = _compile(repo, tmp_path, _Recorder(stdout="alpha==1.0\n"))
    assert result["compiled_from"] == "requirements.in"


# --- R3-2: manifest size and parse failures ---


@pytest.mark.parametrize("name", ["pyproject.toml", "requirements.in", "requirements.txt"])
def test_a_manifest_over_one_mib_is_unreadable(name, tmp_path):  # R3-2
    big = "# " + "x" * (1024 * 1024 + 10) + "\nrequests\n"
    repo = _repo(tmp_path, {name: big})
    recorder = _Recorder(stdout="requests==2.32.3\n")
    result = _compile(repo, tmp_path, recorder)
    assert recorder.argvs == []
    assert result["method"] == "failed" and result["dependencies"] == []
    assert "compile failed: unreadable manifest" in result["notes"]


def test_a_deeply_nested_toml_is_unreadable_not_a_crash(tmp_path):  # R3-2 (RecursionError)
    nested = "a = " + "[" * 5000 + "]" * 5000 + "\n"
    repo = _repo(tmp_path, {"pyproject.toml": nested})
    recorder = _Recorder()
    result = _compile(repo, tmp_path, recorder)
    assert recorder.argvs == []
    assert result["method"] == "failed"
    assert "compile failed: unreadable manifest" in result["notes"]


def test_a_memory_error_while_parsing_is_unreadable(tmp_path, monkeypatch):  # R3-2 (MemoryError)
    repo = _repo(tmp_path, {"pyproject.toml": "[project]\nname = 'x'\n"})

    def boom(text):
        raise MemoryError

    monkeypatch.setattr(resolve.tomllib, "loads", boom)
    result = _compile(repo, tmp_path, _Recorder())
    assert "compile failed: unreadable manifest" in result["notes"]


def test_a_binary_manifest_is_unreadable(tmp_path):  # R3-2
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.in").write_bytes(b"\xff\xfe\x00bad")
    result = _compile(repo, tmp_path, _Recorder())
    assert "compile failed: unreadable manifest" in result["notes"]
