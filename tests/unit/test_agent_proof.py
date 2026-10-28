"""Call-chain proof, agent and client side.

AC-66 (wheels-only install argv, uv isolation), AC-67 and AC-82 (the harness: reached, not reached,
main block, argparse, exit), AC-68 (no wheel, timeout), AC-69 (sentinel placeholders only), AC-70
(network ON only for proof-install, OFF for proof-run, asserted on the REST body). ADR-0041
(ADR-0041, AC-83): proof-install downloads the source package of a pin without a wheel (never
opened), proof-build builds it offline, and the client and the REST body for both.

The harness tests run `proof_run_command` in-process against the fixture repositories (each has a
`repo/` and a stub `deps/` folder), with `finish` captured instead of `os._exit`. The tests marked
`slow` run the real harness in a subprocess, where it leaves with `os._exit(0)`.
"""

import base64
import io
import json
import os
import runpy
import shlex
import shutil
import subprocess
import sys
import tarfile
import textwrap
import time
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest
from fakes.contree import FakeContree
from fakes.http import fixture_json
from fakes.proof import (
    REPOS,
    SDIST_BYTES,
    SDIST_FILE,
    FakePyPI,
    FakeResponse,
    fake_release,
    fixture,
    good_pypi,
    json_url,
    line_of,
    run_harness_subprocess,
    sha256,
)

from pir import agent_client
from pir.agent_client import (
    AGENT_PATH,
    ENTRY_ENDED_REASON,
    PROOF_UNAUTHENTICATED_REASON,
    LocalFixtureTarget,
    ProofImage,
    SandboxAgentClient,
    SandboxFailed,
    SandboxTimeout,
    build_agent_pyz,
)
from pir.config import Settings
from pir.executor.base import ExecResult, ExecutorSpendDenied, ImageRef
from pir.executor.contree import ContreeExecutor
from pir.executor.fake import FakeExecutor
from pir.github import RepoCoordinates, ValidatedTarget
from pir.sandbox_agent import proof, protocol, resolve
from pir.sandbox_agent.protocol import AgentError
from pir.spend import ScanBudget, SpendDenied

SRC = Path(__file__).resolve().parents[2] / "src"
SHA = "c" * 40
SETTINGS = Settings(_env_file=None)
PROOF_STDERR = (REPOS / "locks" / "uv_no_build_sdist.stderr.txt").read_text(encoding="utf-8")


# ---- helpers ---------------------------------------------------------------------------------


def line(payload: object) -> str:
    return protocol.encode_result(payload)


def exec_result(**kw) -> ExecResult:
    base = {
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "truncated": False,
        "duration_s": 0.1,
        "cost_usd": 0.0,
        "image": None,
    }
    return ExecResult(**{**base, **kw})


def budget() -> ScanBudget:
    return ScanBudget("scan", {"nebius": 5.0, "tavily": 0.0}, {})


@pytest.fixture
def sandboxed(monkeypatch, tmp_path):
    """Isolate what the harness changes in-process: sys.path, cwd, imported repo modules."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "stdin", sys.stdin)
    monkeypatch.chdir(tmp_path)
    stale = {k: sys.modules.pop(k) for k in list(sys.modules) if k.split(".")[0] == "yaml"}
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]
    sys.modules.update(stale)


def run_harness(monkeypatch, repo: Path, deps: Path, entry: str, target: str, site=None):
    """Run the harness in-process. Returns (payload, stdout text); checks the one result line."""
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    exits: list[int] = []
    args = {"entry": entry, "target": target, **({"site": site} if site else {})}
    result = proof.proof_run_command(
        args, repo_root=str(repo), deps_root=str(deps), finish=exits.append
    )
    lines = [ln for ln in out.getvalue().splitlines() if ln.startswith(protocol.RESULT_PREFIX)]
    assert exits == [0] and len(lines) == 1  # exactly one result line, then the process leaves
    assert protocol.decode_result(lines[0]) == result
    return result, out.getvalue()


def write_repo(root: Path, files: dict[str, str]) -> tuple[Path, Path]:
    repo, deps = root / "repo", root / "deps"
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(textwrap.dedent(text), encoding="utf-8")
    stub = REPOS / "proof_reached" / "deps" / "yaml" / "__init__.py"
    (deps / "yaml").mkdir(parents=True)
    (deps / "yaml" / "__init__.py").write_text(stub.read_text(encoding="utf-8"), encoding="utf-8")
    return repo, deps


# ---- proof-install: the uv argv and isolation (AC-66, round 2 uv isolation) -------------------


class RecordingRunner:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else (0, "")
        if isinstance(outcome, Exception):
            raise outcome
        code, stderr = outcome
        return subprocess.CompletedProcess(argv, code, stdout="", stderr=stderr)


PINS = {"pins": [{"name": "pyyaml", "version": "6.0.2"}, {"name": "requests", "version": "2.32.3"}]}


class NoNetwork:
    """An opener for tests that must not download anything."""

    def __call__(self, url):
        raise AssertionError(f"unexpected download: {url}")


def install(tmp_path, runner, args=PINS, opener=None):
    return proof.proof_install_command(
        args,
        deps_root="/work/deps",
        tmp_root=str(tmp_path),
        runner=runner,
        opener=opener or NoNetwork(),
        sdists_root=str(tmp_path / "sdists"),
        wheels_root=str(tmp_path / "buildwheels"),
    )


def test_ac66_install_argv_is_wheels_only_with_the_pin_after_double_dash(tmp_path):
    runner = RecordingRunner()
    result = install(tmp_path, runner)
    assert result == {
        "installed": ["pyyaml==6.0.2", "requests==2.32.3"],
        "no_wheel": [],
        "sdists": [],
        "unfetched": [],
    }
    assert not (tmp_path / "sdists").exists()  # nothing to download: no folder either
    assert len(runner.calls) == 2  # one uv call per pin, so a missing wheel is attributed
    argv, _ = runner.calls[0]
    assert argv[:4] == ["/usr/local/bin/uv", "pip", "install", "--no-deps"]
    pair = argv.index("--only-binary")
    assert argv[pair : pair + 2] == ["--only-binary", ":all:"]
    assert argv[argv.index("--target") + 1] == "/work/deps"
    assert argv[argv.index("--python") + 1] == "/usr/local/bin/python3.13"
    for flag in ("--no-config", "--no-sources"):
        assert flag in argv
    assert "--no-build" not in argv  # uv refuses --only-binary together with --no-build
    assert argv[argv.index("--index-url") + 1] == "https://pypi.org/simple"
    assert argv[-2:] == ["--", "pyyaml==6.0.2"]  # the pin follows `--`, built from validated parts
    assert runner.calls[1][0][-2:] == ["--", "requests==2.32.3"]


def test_uv_install_runs_from_a_fresh_empty_directory_with_the_fixed_environment(tmp_path):
    runner = RecordingRunner()
    install(tmp_path, runner)
    for _, kwargs in runner.calls:
        assert kwargs["cwd"] == str(tmp_path / "uvwd")  # outside /work/repo
        assert not kwargs["cwd"].startswith("/work/repo")
        assert kwargs["env"] == resolve.UV_WHEELS_ENV  # never os.environ
        assert "UV_NO_BUILD" not in kwargs["env"]  # same conflict as the flag
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["check"] is False
        assert kwargs["timeout"] <= resolve.UV_TIMEOUT_S
    assert list((tmp_path / "uvwd").iterdir()) == []


def test_uv_install_refuses_a_working_directory_that_already_exists(tmp_path):
    (tmp_path / "uvwd").mkdir()
    with pytest.raises(protocol.AgentError, match="already exists"):
        install(tmp_path, RecordingRunner())


def test_ac68_a_pin_with_no_wheel_is_listed_and_the_others_are_still_installed(tmp_path):
    runner = RecordingRunner((1, PROOF_STDERR), (0, ""))
    opener = FakePyPI({})  # PyPI answers 404 for every page: the source package is unfetched
    result = install(
        tmp_path,
        runner,
        {"pins": [{"name": "antlr4-python3-runtime", "version": "4.9.3"}, *PINS["pins"][:1]]},
        opener=opener,
    )
    assert result["installed"] == ["pyyaml==6.0.2"]
    assert result["no_wheel"] == ["antlr4-python3-runtime==4.9.3"]
    assert result["sdists"] == []
    assert result["unfetched"] == [
        {"spec": "antlr4-python3-runtime==4.9.3", "reason": "pypi_error"}
    ]
    assert len(runner.calls) == 2  # the others are still installed


def test_any_other_uv_failure_fails_the_command_and_so_do_a_timeout_and_a_missing_uv(tmp_path):
    cases = [
        ((2, "error: network unreachable"), "uv failed installing pyyaml==6.0.2"),
        (subprocess.TimeoutExpired(["uv"], 1), "timed out"),
        (OSError(), "cannot start uv"),
    ]
    for number, (outcome, message) in enumerate(cases):
        root = tmp_path / str(number)
        root.mkdir()
        with pytest.raises(protocol.AgentError, match=message):
            install(root, RecordingRunner(outcome), PINS)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "pyyaml==6.0",
        [{"name": "PyYAML", "version": "6.0"}],  # not normalized
        [{"name": "pyyaml", "version": "6.0; rm -rf /"}],
        [{"name": "pyyaml", "version": "latest"}],
        [{"name": "pyyaml"}],
        [{"name": "pyyaml", "version": "6.0", "extra": 1}],
        [{"name": "--index-url", "version": "1.0"}],
        ["pyyaml==6.0"],
        [{"name": "pkg-%d" % n, "version": "1.0"} for n in range(proof.MAX_PINS + 1)],
    ],
)
def test_invalid_pins_are_refused_before_uv_runs(tmp_path, bad):
    runner = RecordingRunner()
    with pytest.raises(protocol.BadInput):
        install(tmp_path, runner, {"pins": bad})
    assert runner.calls == []


# ---- proof-run: the harness, function entries (AC-66, AC-67, AC-69) ---------------------------


def test_ac66_a_reached_function_entry_is_proven_with_the_recorded_call(sandboxed, monkeypatch):
    repo, deps = fixture("proof_reached")
    result, _ = run_harness(monkeypatch, repo, deps, "app.handler:handle", "yaml.unsafe_load")
    where = line_of(repo / "app" / "handler.py", "yaml.unsafe_load(payload)")
    assert result == {
        "status": "proven",
        "reason": "yaml.unsafe_load was reached from app.handler:handle",
        "entry": "app.handler:handle",
        "target": "yaml.unsafe_load",
        "recorded_call": f"yaml.unsafe_load called from app/handler.py:{where}",
    }


def test_ac67_an_entry_that_never_reaches_the_target_is_not_proven_with_a_reason(
    sandboxed, monkeypatch
):
    repo, deps = fixture("proof_not_reached")
    result, _ = run_harness(monkeypatch, repo, deps, "app.handler:handle", "yaml.unsafe_load")
    assert result["status"] == "not_proven" and result["recorded_call"] is None
    assert result["reason"] == "entry finished without reaching yaml.unsafe_load"


def test_ac69_the_harness_passes_only_sentinel_placeholders(sandboxed, monkeypatch):
    seen: list[tuple[list, dict]] = []
    real = proof.build_call

    def spy(func):
        args, kwargs = real(func)
        seen.append((args, kwargs))
        return args, kwargs

    monkeypatch.setattr(proof, "build_call", spy)
    repo, deps = fixture("proof_reached")
    run_harness(monkeypatch, repo, deps, "app.handler:handle", "yaml.unsafe_load")
    assert len(seen) == 1
    ((args, kwargs),) = seen
    assert args == [mock.sentinel.payload] and kwargs == {}
    assert all(type(v) is type(mock.sentinel.x) for v in [*args, *kwargs.values()])  # no strings


def test_ac69_build_call_gives_sentinels_for_required_parameters_only():
    def entry(a, b=1, /, c=2, *rest, d, e=3, **more):  # noqa: ARG001
        return None

    args, kwargs = proof.build_call(entry)
    assert args == [mock.sentinel.a] and kwargs == {"d": mock.sentinel.d}
    assert proof.build_call(print) == ([], {})  # a builtin without a signature: no arguments
    assert not any(isinstance(v, str) for v in [*args, *kwargs.values()])


def test_a_method_entry_and_an_async_entry_are_called_with_placeholders(
    sandboxed, monkeypatch, tmp_path
):
    repo, deps = write_repo(
        tmp_path,
        {
            "svc/__init__.py": "",
            "svc/api.py": """
                import yaml


                class Handler:
                    def run(self, payload):
                        return yaml.load(payload)


                async def serve(request):
                    return yaml.full_load(request)
            """,
        },
    )
    method, _ = run_harness(monkeypatch, repo, deps, "svc.api:Handler.run", "yaml.load")
    assert method["status"] == "proven"
    assert method["recorded_call"] == "yaml.load called from svc/api.py:7"
    coroutine, _ = run_harness(monkeypatch, repo, deps, "svc.api:serve", "yaml.full_load")
    assert coroutine["recorded_call"] == "yaml.full_load called from svc/api.py:11"


def test_src_layout_modules_are_found_and_importable(sandboxed, monkeypatch, tmp_path):
    repo, deps = write_repo(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/core.py": "import yaml\n\n\ndef go(x):\n    return yaml.unsafe_load(x)\n",
        },
    )
    assert proof.find_entry_file(str(repo), "pkg.core") == "src/pkg/core.py"
    result, _ = run_harness(monkeypatch, repo, deps, "pkg.core:go", "yaml.unsafe_load")
    assert result["recorded_call"] == "yaml.unsafe_load called from src/pkg/core.py:5"


def test_a_swallowed_stop_is_still_proven(sandboxed, monkeypatch, tmp_path):
    repo, deps = write_repo(
        tmp_path,
        {
            "app.py": """
                import yaml


                def go(x):
                    try:
                        yaml.unsafe_load(x)
                    except BaseException:
                        pass
                    return "carried on"
            """
        },
    )
    result, _ = run_harness(monkeypatch, repo, deps, "app:go", "yaml.unsafe_load")
    assert result["status"] == "proven"
    assert result["recorded_call"] == "yaml.unsafe_load called from app.py:7"


def test_a_call_made_only_by_library_code_is_not_the_repository_call(
    sandboxed, monkeypatch, tmp_path
):
    repo, deps = write_repo(tmp_path, {"app.py": "import yaml\n\n\ndef go(x):\n    return 1\n"})
    (deps / "yaml" / "__init__.py").write_text(
        "def unsafe_load(s):\n    return s\n\n\ndef helper(s):\n    return unsafe_load(s)\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text(
        "import yaml\n\n\ndef go(x):\n    return yaml.helper(x)\n", encoding="utf-8"
    )
    result, _ = run_harness(monkeypatch, repo, deps, "app:go", "yaml.unsafe_load")
    # `helper` calls `unsafe_load` by its module global, which the stub replaced; the nearest
    # repository frame is app.py:5, the repository's own call into the library
    assert result["recorded_call"] == "yaml.unsafe_load called from app.py:5"


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (
            "def go(x):\n    raise RuntimeError('SECRET-OUTPUT')\n",
            "entry raised RuntimeError before reaching yaml.load",
        ),
        (
            "import sys\n\n\ndef go(x):\n    print('SECRET-OUTPUT')\n    sys.exit(3)\n",
            "entry exited with code 3 before reaching yaml.load",
        ),
        (
            "def go(x):\n    raise SystemExit('SECRET-OUTPUT')\n",
            "entry exited with code 1 before reaching yaml.load",
        ),
        ("import not_a_module\n\n\ndef go(x):\n    pass\n", "entry raised ModuleNotFoundError"),
        ("def go(x):\n    print('SECRET-OUTPUT')\n", "entry finished without reaching yaml.load"),
    ],
)
def test_every_ending_is_a_stated_result_and_never_copies_output(
    sandboxed, monkeypatch, tmp_path, source, reason
):
    repo, deps = write_repo(tmp_path, {"app.py": source})
    result, stdout = run_harness(monkeypatch, repo, deps, "app:go", "yaml.load")
    assert result["status"] == "not_proven" and result["reason"].startswith(reason)
    assert "SECRET-OUTPUT" not in json.dumps(result)
    assert "SECRET-OUTPUT" not in stdout  # the script's own output is captured, not forwarded


def test_a_target_that_cannot_be_stubbed_and_a_missing_entry_are_stated(sandboxed, monkeypatch):
    repo, deps = fixture("proof_reached")
    gone, _ = run_harness(monkeypatch, repo, deps, "app.handler:handle", "no_such_pkg.func")
    assert gone["reason"] == "could not stub no_such_pkg.func: ModuleNotFoundError"
    missing, _ = run_harness(monkeypatch, repo, deps, "app.nope:handle", "yaml.unsafe_load")
    assert missing["reason"] == "entry module was not found in the repository"
    attribute, _ = run_harness(monkeypatch, repo, deps, "app.handler:absent", "yaml.unsafe_load")
    assert attribute["reason"] == "entry raised AttributeError before reaching yaml.unsafe_load"


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"entry": "main", "target": "yaml.load"},
        {"entry": "main:<module>; rm", "target": "yaml.load"},
        {"entry": "main:<module>", "target": "yaml.load()"},
        {"entry": "main:<module>", "target": "../yaml"},
        {"entry": "main:<module>", "target": "yaml.load", "site": "main.py"},
        {"entry": "main:<module>", "target": "yaml.load", "site": "../main.py:3"},
        {"entry": "main:<module>", "target": "yaml.load", "site": "/etc/passwd:3"},
        {"entry": "main:<module>", "target": "yaml.load", "site": "main.py:0"},
        {"entry": "main:<module>", "target": "yaml.load", "site": 7},
        {"entry": 5, "target": "yaml.load"},
    ],
)
def test_proof_run_refuses_malformed_arguments_before_running_anything(args):
    with pytest.raises(protocol.BadInput):
        proof.proof_run_command(args, finish=lambda code: None)


# ---- proof-run: main-block entries (AC-82, ADR-0039) ------------------------------------------


def test_ac82_a_main_block_entry_runs_as_a_script_and_records_the_no_loader_call(
    sandboxed, monkeypatch
):
    repo, deps = fixture("proof_main_block")
    main = repo / "main.py"
    site = line_of(main, "config = yaml.load(stream)")
    safe = line_of(main, "yaml.load(safe_stream, Loader=yaml.SafeLoader)")
    started = time.monotonic()
    result, _ = run_harness(
        monkeypatch, repo, deps, "main:<module>", "yaml.load", site=f"main.py:{site}"
    )
    assert time.monotonic() - started < 10  # the script's endless loop is never reached
    assert result["status"] == "proven"
    assert result["recorded_call"] == f"yaml.load called from main.py:{site}"
    assert site != safe  # the executed SafeLoader call was not recorded


def test_ac82_without_a_site_the_first_call_to_the_name_is_recorded(sandboxed, monkeypatch):
    repo, deps = fixture("proof_main_block")
    safe = line_of(repo / "main.py", "yaml.load(safe_stream, Loader=yaml.SafeLoader)")
    result, _ = run_harness(monkeypatch, repo, deps, "main:<module>", "yaml.load")
    assert result["recorded_call"] == f"yaml.load called from main.py:{safe}"


def test_ac82_the_harness_calls_run_path_as_main_with_no_arguments_or_input(sandboxed, monkeypatch):
    repo, deps = fixture("proof_main_block")
    path = str(repo / "main.py")
    calls: list[dict] = []

    def fake_run_path(file, init_globals=None, run_name=None):
        calls.append(
            {
                "file": file,
                "init_globals": init_globals,
                "run_name": run_name,
                "argv": list(sys.argv),
                "stdin": sys.stdin.read(),
                "cwd": os.getcwd(),
                "first": sys.path[0],
                "deps": str(deps) in sys.path,
            }
        )
        raise SystemExit(0)

    monkeypatch.setattr(runpy, "run_path", fake_run_path)
    result, _ = run_harness(monkeypatch, repo, deps, "main:<module>", "yaml.load")
    assert len(calls) == 1
    call = calls[0]
    assert (call["file"], call["run_name"], call["init_globals"]) == (path, "__main__", None)
    assert call["argv"] == [path]  # no argv beyond the path
    assert call["stdin"] == ""  # empty standard input
    assert os.path.realpath(call["cwd"]) == os.path.realpath(repo)
    assert call["first"] == str(repo) and call["deps"] is True
    assert result["reason"] == "entry exited with code 0 before reaching yaml.load"


def test_ac82_a_required_argparse_argument_is_not_proven_with_the_stated_reason(
    sandboxed, monkeypatch
):
    repo, deps = fixture("proof_main_argparse")
    site = line_of(repo / "main.py", "config = yaml.load(stream)")
    result, stdout = run_harness(
        monkeypatch, repo, deps, "main:<module>", "yaml.load", site=f"main.py:{site}"
    )
    assert result["status"] == "not_proven" and result["recorded_call"] is None
    assert result["reason"] == (
        "entry needs command-line arguments; the proof supplies none "
        "(exit code 2 before reaching yaml.load)"
    )
    assert "usage:" not in stdout  # argparse's text went to the captured stderr, not the result


def test_ac82_a_script_that_exits_first_is_not_proven_with_its_exit_code(sandboxed, monkeypatch):
    repo, deps = fixture("proof_main_exit")
    result, _ = run_harness(monkeypatch, repo, deps, "main:<module>", "yaml.load")
    assert result["status"] == "not_proven"
    assert result["reason"] == "entry exited with code 0 before reaching yaml.load"


def test_a_module_without_a_main_guard_is_imported_and_its_module_scope_runs(
    sandboxed, monkeypatch, tmp_path
):
    repo, deps = write_repo(tmp_path, {"boot.py": "import yaml\n\nCONFIG = yaml.load('a: 1')\n"})
    assert proof.has_main_guard(str(repo / "boot.py")) is False
    result, _ = run_harness(monkeypatch, repo, deps, "boot:<module>", "yaml.load")
    assert result["recorded_call"] == "yaml.load called from boot.py:3"
    assert proof.has_main_guard(str(fixture("proof_main_block")[0] / "main.py")) is True


def test_a_main_guard_check_never_runs_the_file_and_survives_bad_syntax(tmp_path):
    marker = tmp_path / "ran"
    bad = tmp_path / "bad.py"
    bad.write_text("def (:\n", encoding="utf-8")
    ok = tmp_path / "ok.py"
    ok.write_text(f"open({str(marker)!r}, 'w')\nif __name__ == '__main__':\n    pass\n")
    assert proof.has_main_guard(str(bad)) is False
    assert proof.has_main_guard(str(ok)) is True
    assert not marker.exists()
    assert proof.has_main_guard(str(tmp_path / "missing.py")) is False


# ---- proof-run in a subprocess: leaving the process, the watchdog (slow) ----------------------


def result_of(done: subprocess.CompletedProcess) -> dict:
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith(protocol.RESULT_PREFIX)]
    assert len(lines) == 1 and "UNREACHED" not in done.stdout, done.stdout
    return protocol.decode_result(lines[0])


@pytest.mark.slow
def test_ac82_subprocess_main_block_is_proven_in_under_ten_seconds_and_exits_cleanly():
    repo, deps = fixture("proof_main_block")
    site = line_of(repo / "main.py", "config = yaml.load(stream)")
    done, seconds = run_harness_subprocess(
        repo, deps, "main:<module>", "yaml.load", f"main.py:{site}"
    )
    assert done.returncode == 0 and seconds < 10
    result = result_of(done)
    assert result["status"] == "proven"
    assert result["recorded_call"] == f"yaml.load called from main.py:{site}"


@pytest.mark.slow
def test_ac82_subprocess_argparse_and_exit_fixtures_end_with_stated_reasons():
    repo, deps = fixture("proof_main_argparse")
    done, _ = run_harness_subprocess(repo, deps, "main:<module>", "yaml.load")
    assert "exit code 2 before reaching yaml.load" in result_of(done)["reason"]
    assert done.returncode == 0
    repo, deps = fixture("proof_main_exit")
    done, _ = run_harness_subprocess(repo, deps, "main:<module>", "yaml.load")
    assert result_of(done)["reason"] == "entry exited with code 0 before reaching yaml.load"


@pytest.mark.slow
def test_a_script_that_ignores_the_stop_stays_proven_when_the_watchdog_reports(tmp_path):
    repo, deps = write_repo(
        tmp_path,
        {
            "main.py": """
                import time

                import yaml

                if __name__ == "__main__":
                    while True:
                        try:
                            yaml.load("x")
                        except BaseException:
                            pass
                        time.sleep(0.05)
            """
        },
    )
    done, seconds = run_harness_subprocess(repo, deps, "main:<module>", "yaml.load", watchdog=1.0)
    assert done.returncode == 0 and seconds < 10
    assert result_of(done)["status"] == "proven"  # reported by the watchdog from the recorder


@pytest.mark.slow
def test_ac68_a_script_that_never_reaches_the_call_is_a_sandbox_timeout_at_the_watchdog(
    tmp_path,
):
    repo, deps = write_repo(
        tmp_path,
        {"main.py": 'import yaml\n\nif __name__ == "__main__":\n    while True:\n        pass\n'},
    )
    done, seconds = run_harness_subprocess(repo, deps, "main:<module>", "yaml.load", watchdog=1.0)
    assert done.returncode == 0 and seconds < 10
    result = result_of(done)
    assert (result["status"], result["reason"]) == ("not_proven", "sandbox timeout")


@pytest.mark.slow
def test_a_script_that_ends_the_process_leaves_no_result_line(tmp_path):
    repo, deps = write_repo(
        tmp_path,
        {"main.py": 'import os\n\nimport yaml\n\nif __name__ == "__main__":\n    os._exit(0)\n'},
    )
    done, _ = run_harness_subprocess(repo, deps, "main:<module>", "yaml.load")
    assert done.returncode == 0 and protocol.RESULT_PREFIX not in done.stdout


@pytest.mark.slow
def test_the_zipped_agent_dispatches_proof_commands_and_refuses_bad_arguments(tmp_path):
    pyz = tmp_path / "agent.pyz"
    pyz.write_bytes(build_agent_pyz())
    for command in ("proof-run", "proof-install"):
        bad = protocol.encode_result({})[len(protocol.RESULT_PREFIX) :]  # not base64 of JSON args
        done = subprocess.run(
            [sys.executable, "-I", "-S", str(pyz), command, "--args-b64", bad],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        assert done.returncode == 2 and "unknown command" not in done.stderr


# ---- the client: proof_install and proof_run (AC-66, AC-68, AC-70) ----------------------------


class Scripted:
    """An executor stub: returns scripted results (or raises) and records every run."""

    kind = "fake"

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[SimpleNamespace] = []

    async def prepare(self, files):
        return ImageRef(kind="fake", ref="/stub/base")

    async def run(self, image, argv, *, network, timeout_s, save=False, budget=None):
        self.calls.append(
            SimpleNamespace(image=image, argv=argv, network=network, timeout_s=timeout_s, save=save)
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def discard(self, image):
        return None


def workspace(executor=None):
    image = ImageRef(kind="fake", ref="/stub/a")
    fetched = agent_client.FetchResult(
        commit_sha=SHA, files_total=1, bytes_total=1, py_files=1, manifests=[]
    )
    return agent_client.Workspace(
        image=image, commit_sha=SHA, fetch=fetched, budget=budget(), saved=[image]
    )


def args_of(call) -> dict:
    return protocol.parse_args_b64(call.argv[-1])


PIN_LIST = [("pyyaml", "6.0.2"), ("requests", "2.32.3")]
P_IMAGE = ImageRef(kind="fake", ref="/stub/p")


EMPTY_LISTS = {"installed": [], "no_wheel": [], "sdists": [], "unfetched": []}


def installed_ok(**kw) -> ExecResult:
    payload = {
        "installed": ["pyyaml==6.0.2", "requests==2.32.3"],
        "no_wheel": [],
        "sdists": [],
        "unfetched": [],
        **kw,
    }
    return exec_result(stdout=line(payload), image=P_IMAGE)


async def test_ac70_proof_install_is_network_on_saves_p_and_passes_validated_pins():
    stub = Scripted(installed_ok())
    ws = workspace()
    proof_image = await SandboxAgentClient(stub, SETTINGS).proof_install(ws, PIN_LIST)
    (call,) = stub.calls
    assert call.argv[3:6] == [AGENT_PATH, "proof-install", "--args-b64"]
    assert (call.network, call.save, call.image) == (True, True, ws.image)
    assert call.timeout_s == SETTINGS.timeout_proof_install_s == 300
    assert args_of(call) == {
        "pins": [
            {"name": "pyyaml", "version": "6.0.2"},
            {"name": "requests", "version": "2.32.3"},
        ]
    }
    assert proof_image.image == P_IMAGE and proof_image.no_wheel == []
    assert P_IMAGE in ws.saved  # discarded with the workspace


async def test_proof_install_with_no_pins_runs_nothing_and_a_is_p():
    stub = Scripted()
    ws = workspace()
    proof_image = await SandboxAgentClient(stub, SETTINGS).proof_install(ws, [])
    assert stub.calls == [] and proof_image.image == ws.image


async def test_ac68_proof_install_reports_the_pin_with_no_wheel():
    stub = Scripted(
        installed_ok(
            installed=["pyyaml==6.0.2"],
            no_wheel=["requests==2.32.3"],
            unfetched=[{"spec": "requests==2.32.3", "reason": "no_sdist"}],
        )
    )
    result = await SandboxAgentClient(stub, SETTINGS).proof_install(workspace(), PIN_LIST)
    assert result.no_wheel == ["requests==2.32.3"] and result.installed == ["pyyaml==6.0.2"]
    assert [u.reason for u in result.unfetched] == ["no_sdist"] and result.sdists == []


@pytest.mark.parametrize(
    "outcome",
    [
        exec_result(stdout=line({**EMPTY_LISTS, "installed": ["pyyaml==6.0.2"]}), image=P_IMAGE),
        exec_result(stdout=line({**EMPTY_LISTS, "installed": ["bad pin"]}), image=P_IMAGE),
        exec_result(stdout=line({"installed": []}), image=P_IMAGE),
        exec_result(stdout=line({"installed": ["pyyaml==6.0.2"], "no_wheel": []}), image=P_IMAGE),
        exec_result(stdout=line({**EMPTY_LISTS, "x": 1}), image=P_IMAGE),
        exec_result(
            stdout=line({**EMPTY_LISTS, "installed": ["pyyaml==6.0.2", "requests==2.32.3"]})
        ),  # no image saved
        exec_result(exit_code=1, stderr="pir-agent: failure"),
        exec_result(truncated=True, image=P_IMAGE),
    ],
)
async def test_proof_install_refuses_a_result_that_does_not_match_the_request(outcome):
    ws = workspace()
    with pytest.raises(SandboxFailed):
        await SandboxAgentClient(Scripted(outcome), SETTINGS).proof_install(ws, PIN_LIST)


async def test_proof_install_timeout_and_spend_denial_pass_through():
    timed_out = exec_result(timed_out=True, exit_code=-1)
    with pytest.raises(SandboxTimeout) as caught:
        await SandboxAgentClient(Scripted(timed_out), SETTINGS).proof_install(workspace(), PIN_LIST)
    assert caught.value.step == "proof-install"
    denied = ExecutorSpendDenied(SpendDenied("nebius", "scan_budget_reached"))
    with pytest.raises(ExecutorSpendDenied):
        await SandboxAgentClient(Scripted(denied), SETTINGS).proof_install(workspace(), PIN_LIST)


async def test_proof_install_refuses_an_invalid_pin_before_any_run():
    stub = Scripted()
    with pytest.raises(ValueError):
        await SandboxAgentClient(stub, SETTINGS).proof_install(workspace(), [("PyYAML", "6.0")])
    assert stub.calls == []


PROVEN = {
    "status": "proven",
    "reason": "yaml.load was reached from main:<module>",
    "entry": "main:<module>",
    "target": "yaml.load",
    "recorded_call": "yaml.load called from main.py:10",
}


def a_proof_image() -> ProofImage:
    return ProofImage(image=P_IMAGE, installed=["pyyaml==5.3"], no_wheel=[])


async def run_proof_step(stub, **kw):
    return await SandboxAgentClient(stub, SETTINGS).proof_run(
        workspace(), a_proof_image(), entry="main:<module>", target="yaml.load", **kw
    )


async def test_ac70_proof_run_is_network_off_from_p_and_saves_nothing():
    nonce = "a" * 32
    stub = Scripted(exec_result(stdout=line({**PROVEN, "nonce": nonce})))
    result = await run_proof_step(stub, site="main.py:10", nonce=nonce)
    (call,) = stub.calls
    assert call.argv[3:6] == [AGENT_PATH, "proof-run", "--args-b64"]
    assert (call.network, call.save, call.image) == (False, False, P_IMAGE)
    assert call.timeout_s == SETTINGS.timeout_proof_run_s == 60
    assert args_of(call) == {
        "entry": "main:<module>",
        "target": "yaml.load",
        "site": "main.py:10",
        "nonce": nonce,
    }
    assert result.status == "proven" and result.recorded_call == PROVEN["recorded_call"]
    assert result.nonce == nonce


async def test_proof_run_omits_site_unless_given():
    nonce = "b" * 32
    stub = Scripted(exec_result(stdout=line({**PROVEN, "nonce": nonce})))
    await run_proof_step(stub, nonce=nonce)
    assert args_of(stub.calls[0]) == {
        "entry": "main:<module>",
        "target": "yaml.load",
        "nonce": nonce,
    }


async def test_ac68_a_timed_out_proof_run_is_sandbox_timeout():
    with pytest.raises(SandboxTimeout) as caught:
        await run_proof_step(Scripted(exec_result(timed_out=True, exit_code=-1)))
    assert caught.value.step == "proof-run"


async def test_a_clean_exit_without_a_result_line_is_not_proven_not_a_failure():
    result = await run_proof_step(Scripted(exec_result(stdout="repo output only\n")))
    assert (result.status, result.reason) == ("not_proven", ENTRY_ENDED_REASON)
    assert result.recorded_call is None


@pytest.mark.parametrize(
    "outcome",
    [
        exec_result(stdout="repo output only\n", exit_code=1),
        exec_result(stdout=line({**PROVEN, "recorded_call": None})),
        exec_result(stdout=line({**PROVEN, "status": "not_proven"})),
        exec_result(stdout=line({**PROVEN, "recorded_call": "other.fn called from main.py:10"})),
        exec_result(stdout=line({**PROVEN, "recorded_call": "yaml.load called from ../x.py:10"})),
        exec_result(stdout=line({**PROVEN, "entry": "other:<module>"})),
        exec_result(stdout=line({**PROVEN, "reason": ""})),
        exec_result(stdout=line({**PROVEN, "extra": 1})),
        exec_result(stdout=line(PROVEN), truncated=True),
        exec_result(stdout=line(PROVEN) + "\n" + line(PROVEN)),  # a second result line is forged
    ],
)
async def test_proof_run_refuses_a_result_it_cannot_trust(outcome):
    with pytest.raises(SandboxFailed):
        await run_proof_step(Scripted(outcome))


# ---- AC-70: the REST body through the real executor -------------------------------------------


class ScriptedSandbox(httpx.MockTransport):
    """Answers instance POSTs with an operation whose stdout is the scripted agent result line."""

    def __init__(self, results: dict[str, object]) -> None:
        super().__init__(self._handle)
        self.results = results
        self.posts: list[dict] = []
        self._ops: dict[str, dict] = {}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append(body)
            tokens = shlex.split(body["command"])
            command = tokens[tokens.index(AGENT_PATH) + 1]
            op_id = str(uuid.uuid4())
            data = fixture_json("contree", "op_success.json")
            res_dict = dict(self.results[command])
            if command == "proof-run":
                args = protocol.parse_args_b64(tokens[tokens.index("--args-b64") + 1])
                if "nonce" in args and res_dict.get("status") == "proven":
                    res_dict["nonce"] = args["nonce"]
            data["metadata"]["result"]["stdout"].update(encoding="ascii", value=line(res_dict))
            if not body["disposable"]:
                data["result_image_uuid"] = str(uuid.uuid4())
            self._ops[op_id] = data
            return httpx.Response(201, json={"uuid": op_id})
        return httpx.Response(200, json=self._ops[request.url.path.rsplit("/", 1)[1]])


async def test_ac70_rest_networking_is_on_for_proof_install_and_off_for_proof_run():
    scripted = ScriptedSandbox(
        {
            "fetch": {
                "commit_sha": SHA,
                "files_total": 1,
                "bytes_total": 1,
                "py_files": 1,
                "manifests": [],
            },
            "proof-install": {**EMPTY_LISTS, "installed": ["pyyaml==5.3"]},
            "proof-run": PROVEN,
        }
    )
    settings = Settings(_env_file=None)
    executor = ContreeExecutor(settings, client=FakeContree(), transport=scripted)
    client = SandboxAgentClient(executor, settings)
    client._selfcheck_passed = True  # this test is about the proof steps, not the base image
    github = ValidatedTarget(
        coords=RepoCoordinates(owner="octo", name="demo", ref=None),
        default_branch="main",
        commit_sha=SHA,
        files_total=1,
        bytes_total=1,
        py_files=1,
        manifests=["requirements.txt"],
    )

    ws = await client.open_workspace(github, budget())
    installed = await client.proof_install(ws, [("pyyaml", "5.3")])
    result = await client.proof_run(
        ws, installed, entry="main:<module>", target="yaml.load", site="main.py:10"
    )

    def shape(body: dict) -> tuple[str, bool, bool]:
        tokens = shlex.split(body["command"])
        command = tokens[tokens.index(AGENT_PATH) + 1]
        return command, body["networking"]["enabled"], body["disposable"]

    assert [shape(b) for b in scripted.posts] == [
        ("fetch", True, False),  # saves A
        ("proof-install", True, False),  # network ON, saves P
        ("proof-run", False, True),  # network OFF, disposable
    ]
    for body in scripted.posts:
        assert (body["uid"], body["gid"]) == (65534, 65534)
        assert body["command"].startswith("env -i PATH=")
    assert installed.image != ws.image  # proof-run starts from P, not A
    assert result.status == "proven"
    await client.close(ws)


async def test_the_fake_executor_saves_a_copy_of_the_image_for_a_recorded_proof_install():
    fake = FakeExecutor(recorded={"proof-install": exec_result(stdout=line(EMPTY_LISTS))})
    client = SandboxAgentClient(fake, SETTINGS)
    ws = await client.open_workspace(
        LocalFixtureTarget(path=str(REPOS / "proof_reached" / "repo"), commit_sha=SHA), budget()
    )
    pins = [("pyyaml", "6.0.2")]
    fake.recorded["proof-install"] = exec_result(
        stdout=line({**EMPTY_LISTS, "installed": ["pyyaml==6.0.2"]})
    )
    installed = await client.proof_install(ws, pins)
    assert installed.image.kind == "fake" and installed.image != ws.image
    assert os.path.isdir(installed.image.ref)
    await client.close(ws)
    assert not os.path.exists(installed.image.ref)  # discarded with the workspace


# ---- ADR-0041, AC-83: proof-install saves source packages, proof-build builds them offline -----

PYPI_FIXTURES = REPOS.parent / "http" / "pypi"
SDIST_PINS = {"pins": [{"name": "pyyaml", "version": "5.3.1"}]}
SDIST_SPEC = "pyyaml==5.3.1"
BACKEND_FILES = ["setuptools-75.8.0-py3-none-any.whl", "wheel-0.45.1-py3-none-any.whl"]
SDIST_URL = next(
    u["url"]
    for u in fixture_json("pypi", "pyyaml-5.3.1.json")["urls"]
    if u["filename"] == SDIST_FILE
)


@pytest.fixture
def pypi(monkeypatch):
    """Routes for pyyaml 5.3.1 and the two build tools. The committed hash constants are swapped
    for the hashes of the fake wheels, since the fake bytes cannot match PyPI's real ones."""
    routes, constants = good_pypi()
    monkeypatch.setattr(proof, "BUILD_BACKEND_WHEELS", constants)
    return routes


def simple_release(routes, name, content, *, version="1.0", declared_size=None):
    """A minimal PyPI page for `name` with one sdist whose bytes are `content`."""
    filename = f"{name}-{version}.tar.gz"
    url = f"https://files.pythonhosted.org/packages/aa/bb/{filename}"
    entry = {
        "filename": filename,
        "packagetype": "sdist",
        "url": url,
        "size": len(content) if declared_size is None else declared_size,
        "digests": {"sha256": sha256(content)},
    }
    routes[json_url(name, version)] = json.dumps({"urls": [entry]}).encode("utf-8")
    routes[url] = content
    return filename


def pins_of(*names):
    return [{"name": name, "version": "1.0"} for name in names]


def install_sdists(tmp_path, routes, pins=None, *, runner=None):
    """proof-install where uv refuses every pin for lack of a wheel."""
    pins = pins or SDIST_PINS["pins"]
    opener = FakePyPI(routes)
    runner = runner or RecordingRunner(*[(1, PROOF_STDERR)] * len(pins))
    return install(tmp_path, runner, {"pins": pins}, opener=opener), runner, opener


def test_ac83_a_pin_without_a_wheel_has_its_source_package_saved_and_never_opened(
    tmp_path, monkeypatch, pypi
):
    def refuse(*_args, **_kwargs):
        raise AssertionError("the source package must never be opened")

    monkeypatch.setattr(tarfile, "open", refuse)
    monkeypatch.setattr(zipfile, "ZipFile", refuse)
    result, runner, opener = install_sdists(tmp_path, pypi)
    sdist = tmp_path / "sdists" / SDIST_FILE
    assert result == {
        "installed": [],
        "no_wheel": [SDIST_SPEC],
        "sdists": [{"spec": SDIST_SPEC, "file": str(sdist), "sha256": sha256(SDIST_BYTES)}],
        "unfetched": [],
    }
    assert sdist.read_bytes() == SDIST_BYTES
    assert sorted(p.name for p in (tmp_path / "buildwheels").iterdir()) == BACKEND_FILES
    assert len(runner.calls) == 1 and runner.calls[0][0][-2:] == ["--", SDIST_SPEC]
    for called, _ in runner.calls:  # neither uv nor pip is given the source package
        assert not any(SDIST_FILE in part or "sdists" in part for part in called)
    wheel_urls = [
        u["url"]
        for name, version in (("setuptools", "75.8.0"), ("wheel", "0.45.1"))
        for u in fixture_json("pypi", f"{name}-{version}.json")["urls"]
        if u["filename"].endswith(".whl")
    ]
    assert opener.urls == [
        json_url("pyyaml", "5.3.1"),
        SDIST_URL,
        json_url("setuptools", "75.8.0"),
        wheel_urls[0],
        json_url("wheel", "0.45.1"),
        wheel_urls[1],
    ]
    assert all(
        u.startswith(("https://pypi.org/pypi/", "https://files.pythonhosted.org/packages/"))
        for u in opener.urls
    )


def test_ac83_the_committed_build_tool_hashes_are_the_ones_recorded_from_pypi():
    assert {n: v for n, (v, _) in proof.BUILD_BACKEND_WHEELS.items()} == {
        "setuptools": "75.8.0",
        "wheel": "0.45.1",
    }
    for name, (version, sha) in proof.BUILD_BACKEND_WHEELS.items():
        urls = fixture_json("pypi", f"{name}-{version}.json")["urls"]
        wheel = next(u for u in urls if u["filename"] == f"{name}-{version}-py3-none-any.whl")
        assert wheel["digests"]["sha256"] == sha and len(sha) == 64


def change(**changes):
    def apply(entry):
        entry.update(changes)

    return apply


def remove(key):
    def apply(entry):
        entry.pop(key)

    return apply


NO_SDIST_CASES = {
    "foreign host": change(url="https://evil.example/packages/aa/PyYAML-5.3.1.tar.gz"),
    "plain http": change(url=SDIST_URL.replace("https", "http")),
    "userinfo": change(
        url="https://files.pythonhosted.org@evil.example/packages/aa/PyYAML-5.3.1.tar.gz"
    ),
    "port": change(url=SDIST_URL.replace(".org/", ".org:8443/")),
    "query": change(url=SDIST_URL + "?token=1"),
    "fragment": change(url=SDIST_URL + "#x"),
    "not under /packages/": change(url="https://files.pythonhosted.org/other/x.tar.gz"),
    "dot-dot segment": change(
        url="https://files.pythonhosted.org/packages/../x/PyYAML-5.3.1.tar.gz"
    ),
    "filename with a path": change(filename="../x.tar.gz"),
    "filename with a space": change(filename="bad name.tar.gz"),
    "filename not an archive": change(filename="pkg.exe"),
    "filename starts with a dot": change(filename=".hidden.tar.gz"),
    "filename too long": change(filename="a" * 300 + ".tar.gz"),
    "filename is a bz2": change(filename="pkg.tar.bz2"),
    "not an sdist": change(packagetype="bdist_wheel"),
    "sha256 not hex": change(digests={"sha256": "xyz"}),
    "sha256 uppercase": change(digests={"sha256": "A" * 64}),
    "no digests": remove("digests"),
    "size not an int": change(size="269377"),
}


@pytest.mark.parametrize("case", list(NO_SDIST_CASES))
def test_ac83_a_page_with_no_acceptable_source_package_is_no_sdist(tmp_path, pypi, case):
    page = json.loads((PYPI_FIXTURES / "pyyaml-5.3.1.json").read_text(encoding="utf-8"))
    entry = next(u for u in page["urls"] if u["filename"] == SDIST_FILE)
    NO_SDIST_CASES[case](entry)
    pypi[json_url("pyyaml", "5.3.1")] = json.dumps(page).encode("utf-8")
    result, _, opener = install_sdists(tmp_path, pypi)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "no_sdist"}]
    assert result["sdists"] == []
    assert opener.urls == [json_url("pyyaml", "5.3.1")]  # nothing was downloaded
    assert list((tmp_path / "sdists").iterdir()) == []
    assert not (tmp_path / "buildwheels").exists()  # no source package: no build tools either


def test_ac83_a_tar_gz_is_preferred_to_a_zip_and_a_zip_alone_is_accepted(tmp_path, pypi):
    page = {"urls": []}
    for filename in ("pkg-1.0.zip", "pkg-1.0.tar.gz"):
        url = f"https://files.pythonhosted.org/packages/aa/bb/{filename}"
        content = filename.encode()
        page["urls"].append(
            {
                "filename": filename,
                "packagetype": "sdist",
                "url": url,
                "size": len(content),
                "digests": {"sha256": sha256(content)},
            }
        )
        pypi[url] = content
    pypi[json_url("pkg", "1.0")] = json.dumps(page).encode("utf-8")
    result, _, _ = install_sdists(tmp_path, pypi, pins_of("pkg"))
    assert [Path(s["file"]).name for s in result["sdists"]] == ["pkg-1.0.tar.gz"]

    page["urls"].pop()  # only the zip is left
    pypi[json_url("pkg", "1.0")] = json.dumps(page).encode("utf-8")
    other = tmp_path / "zip"
    other.mkdir()
    result, _, _ = install_sdists(other, pypi, pins_of("pkg"))
    assert [Path(s["file"]).name for s in result["sdists"]] == ["pkg-1.0.zip"]


def breaks_mid_read():
    class Broken(FakeResponse):
        def read(self, n=-1):
            raise OSError("connection reset")

    return Broken(b"")


PYPI_ERROR_CASES = {
    "a redirect": (json_url("pyyaml", "5.3.1"), AgentError("unexpected redirect (302)")),
    "status 500": (json_url("pyyaml", "5.3.1"), 500),
    "status 204": (json_url("pyyaml", "5.3.1"), 204),
    "a timeout": (json_url("pyyaml", "5.3.1"), TimeoutError("timed out")),
    "invalid json": (json_url("pyyaml", "5.3.1"), b"{not json"),
    "json that is a list": (json_url("pyyaml", "5.3.1"), b"[]"),
    "json without urls": (json_url("pyyaml", "5.3.1"), b'{"info": {}}'),
    "urls that is not a list": (json_url("pyyaml", "5.3.1"), b'{"urls": "x"}'),
    "not utf-8": (json_url("pyyaml", "5.3.1"), b"\xff\xfe{}"),
    "a page that fails mid-read": (json_url("pyyaml", "5.3.1"), breaks_mid_read),
    "the file is a 503": (SDIST_URL, 503),
    "the file is a redirect": (SDIST_URL, AgentError("unexpected redirect (301)")),
    "the file fails mid-read": (SDIST_URL, breaks_mid_read),
}


@pytest.mark.parametrize("case", list(PYPI_ERROR_CASES))
def test_ac83_http_errors_redirects_and_bad_json_are_pypi_error(tmp_path, pypi, case):
    url, route = PYPI_ERROR_CASES[case]
    pypi[url] = route
    result, _, _ = install_sdists(tmp_path, pypi)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "pypi_error"}]
    assert result["sdists"] == []
    assert list((tmp_path / "sdists").iterdir()) == []  # no partial file is left behind


def test_ac83_a_json_page_over_the_cap_is_pypi_error(tmp_path, monkeypatch, pypi):
    monkeypatch.setattr(proof, "MAX_PYPI_JSON_BYTES", 10)
    result, _, _ = install_sdists(tmp_path, pypi)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "pypi_error"}]


def test_ac83_a_declared_size_over_20_mib_is_too_large_and_nothing_is_downloaded(tmp_path, pypi):
    assert proof.MAX_SDIST_BYTES == 20 * 1024 * 1024
    declared = proof.MAX_SDIST_BYTES + 1
    fake_release(
        pypi,
        "pyyaml-5.3.1.json",
        "pyyaml",
        "5.3.1",
        SDIST_FILE,
        SDIST_BYTES,
        declared_size=declared,
    )
    result, _, opener = install_sdists(tmp_path, pypi)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "too_large"}]
    assert SDIST_URL not in opener.urls


def test_ac83_a_stream_over_the_cap_is_too_large_even_when_the_page_understates_it(
    tmp_path, monkeypatch, pypi
):
    monkeypatch.setattr(proof, "MAX_SDIST_BYTES", 64)
    fake_release(
        pypi, "pyyaml-5.3.1.json", "pyyaml", "5.3.1", SDIST_FILE, b"x" * 200, declared_size=10
    )
    result, _, _ = install_sdists(tmp_path, pypi)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "too_large"}]
    assert list((tmp_path / "sdists").iterdir()) == []


def test_ac83_a_sha256_mismatch_is_hash_mismatch_and_the_file_is_deleted(tmp_path, pypi):
    # PyPI's recorded page, but bytes that do not hash to its sha256
    recorded = (PYPI_FIXTURES / "pyyaml-5.3.1.json").read_text(encoding="utf-8")
    pypi[json_url("pyyaml", "5.3.1")] = recorded.encode("utf-8")
    pypi[SDIST_URL] = SDIST_BYTES
    result, _, opener = install_sdists(tmp_path, pypi)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "hash_mismatch"}]
    assert SDIST_URL in opener.urls
    assert list((tmp_path / "sdists").iterdir()) == []
    assert not (tmp_path / "buildwheels").exists()


def test_ac83_the_eleventh_source_package_is_over_the_limit(tmp_path, pypi):
    names = [f"pkg-{n}" for n in range(11)]
    for name in names:
        simple_release(pypi, name, name.encode())
    result, runner, opener = install_sdists(tmp_path, pypi, pins_of(*names))
    assert [s["spec"] for s in result["sdists"]] == [f"{n}==1.0" for n in names[:10]]
    assert result["unfetched"] == [{"spec": "pkg-10==1.0", "reason": "limit"}]
    assert json_url("pkg-10", "1.0") not in opener.urls
    assert len(runner.calls) == 11  # every pin still went through uv


def test_ac83_the_total_over_50_mib_is_limit_by_declared_and_by_streamed_size(
    tmp_path, monkeypatch, pypi
):
    assert proof.MAX_SDIST_TOTAL_BYTES == 50 * 1024 * 1024
    monkeypatch.setattr(proof, "MAX_SDIST_TOTAL_BYTES", 150)
    simple_release(pypi, "pkg-a", b"a" * 100)
    simple_release(pypi, "pkg-b", b"b" * 100)  # declared 100: 100 + 100 is over 150
    simple_release(pypi, "pkg-c", b"c" * 100, declared_size=10)  # says 10, streams 100
    result, _, opener = install_sdists(tmp_path, pypi, pins_of("pkg-a", "pkg-b"))
    assert [s["spec"] for s in result["sdists"]] == ["pkg-a==1.0"]
    assert result["unfetched"] == [{"spec": "pkg-b==1.0", "reason": "limit"}]
    assert "https://files.pythonhosted.org/packages/aa/bb/pkg-b-1.0.tar.gz" not in opener.urls
    other = tmp_path / "streamed"
    other.mkdir()
    result, _, _ = install_sdists(other, pypi, pins_of("pkg-a", "pkg-c"))
    assert result["unfetched"] == [{"spec": "pkg-c==1.0", "reason": "limit"}]
    assert not (other / "sdists" / "pkg-c-1.0.tar.gz").exists()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def test_ac83_downloads_share_the_install_budget_and_report_time_limit(tmp_path, monkeypatch, pypi):
    clock = Clock()
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=clock.monotonic))

    def slow_uv(argv, **_kwargs):
        clock.now += 300  # the budget is spent before the download phase starts
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=PROOF_STDERR)

    result, _, opener = install_sdists(tmp_path, pypi, runner=slow_uv)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "time_limit"}]
    assert opener.urls == []


def test_ac83_a_download_that_outlasts_the_budget_is_time_limit_and_deleted(
    tmp_path, monkeypatch, pypi
):
    clock = Clock()
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=clock.monotonic))

    class Slow(FakeResponse):
        def read(self, n=-1):
            clock.now += 1000
            return super().read(n)

    pypi[SDIST_URL] = lambda: Slow(SDIST_BYTES)
    result, _, _ = install_sdists(tmp_path, pypi)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "time_limit"}]
    assert list((tmp_path / "sdists").iterdir()) == []


def test_ac83_a_build_tool_that_does_not_match_its_constant_makes_every_package_unavailable(
    tmp_path, monkeypatch, pypi
):
    constants = dict(proof.BUILD_BACKEND_WHEELS)
    constants["setuptools"] = ("75.8.0", "0" * 64)  # PyPI's page and the file differ from this
    monkeypatch.setattr(proof, "BUILD_BACKEND_WHEELS", constants)
    simple_release(pypi, "pkg-0", b"zero")
    pins = [*SDIST_PINS["pins"], *pins_of("pkg-0")]
    result, _, _ = install_sdists(tmp_path, pypi, pins)
    assert result["sdists"] == []
    assert result["unfetched"] == [
        {"spec": SDIST_SPEC, "reason": "backend_unavailable"},
        {"spec": "pkg-0==1.0", "reason": "backend_unavailable"},
    ]
    assert list((tmp_path / "sdists").iterdir()) == []  # the saved packages were removed


def test_ac83_a_build_tool_file_whose_bytes_differ_from_its_hash_is_unavailable(
    tmp_path, monkeypatch, pypi
):
    original = b"the reviewed wheel"
    wheel = "wheel-0.45.1-py3-none-any.whl"
    fake_release(
        pypi,
        "wheel-0.45.1.json",
        "wheel",
        "0.45.1",
        wheel,
        b"a tampered wheel",
        sha=sha256(original),
    )
    constants = dict(proof.BUILD_BACKEND_WHEELS)
    constants["wheel"] = ("0.45.1", sha256(original))  # PyPI's page and the constant agree
    monkeypatch.setattr(proof, "BUILD_BACKEND_WHEELS", constants)
    result, _, _ = install_sdists(tmp_path, pypi)
    assert result["unfetched"] == [{"spec": SDIST_SPEC, "reason": "backend_unavailable"}]
    assert not (tmp_path / "buildwheels" / wheel).exists()  # the bytes do not


def test_ac83_an_unreachable_build_tool_page_is_unavailable_and_other_reasons_are_kept(
    tmp_path, pypi
):
    del pypi[json_url("wheel", "0.45.1")]
    pins = [*SDIST_PINS["pins"], *pins_of("pkg-0")]  # pkg-0 has no page at all
    result, _, _ = install_sdists(tmp_path, pypi, pins)
    assert result["unfetched"] == [
        {"spec": SDIST_SPEC, "reason": "backend_unavailable"},
        {"spec": "pkg-0==1.0", "reason": "pypi_error"},
    ]
    assert result["sdists"] == []


def test_ac83_every_no_wheel_pin_is_in_exactly_one_list(tmp_path, pypi):
    simple_release(pypi, "pkg-0", b"zero")
    pins = [*SDIST_PINS["pins"], *pins_of("pkg-0", "pkg-1")]
    result, _, _ = install_sdists(tmp_path, pypi, pins)
    listed = [s["spec"] for s in result["sdists"]] + [u["spec"] for u in result["unfetched"]]
    assert sorted(listed) == sorted(result["no_wheel"]) and len(listed) == 3
    assert [u["spec"] for u in result["unfetched"]] == ["pkg-1==1.0"]


def test_ac83_nothing_is_downloaded_for_a_pin_that_has_a_wheel(tmp_path, pypi):
    opener = FakePyPI(pypi)
    result = install(tmp_path, RecordingRunner((0, "")), SDIST_PINS, opener=opener)
    assert result["installed"] == [SDIST_SPEC] and result["sdists"] == []
    assert opener.urls == []


# ---- proof-build: the agent command ------------------------------------------------------------


def build_roots(tmp_path):
    (tmp_path / "tmp").mkdir(exist_ok=True)
    return {
        "deps_root": "/work/deps",
        "tmp_root": str(tmp_path / "tmp"),
        "sdists_root": str(tmp_path / "sdists"),
        "wheels_root": str(tmp_path / "buildwheels"),
    }


def build(tmp_path, runner, args):
    return proof.proof_build_command(args, runner=runner, **build_roots(tmp_path))


def staged(tmp_path, pypi, names=()):
    """Run proof-install against the fake PyPI and return the `proof-build` args for its result."""
    for name in names:
        simple_release(pypi, name, name.encode())
    result, _, _ = install_sdists(tmp_path, pypi, [*SDIST_PINS["pins"], *pins_of(*names)])
    assert result["unfetched"] == []
    builds = []
    for entry in result["sdists"]:
        name, version = entry["spec"].split("==")
        builds.append(
            {"name": name, "version": version, "file": entry["file"], "sha256": entry["sha256"]}
        )
    return {"builds": builds}


def test_ac83_proof_build_runs_the_adr_argv_in_a_fresh_directory_with_the_build_environment(
    tmp_path, pypi
):
    args = staged(tmp_path, pypi, names=("pkg-0",))
    runner = RecordingRunner((0, ""), (0, ""))
    result = build(tmp_path, runner, args)
    assert result == {"built": [SDIST_SPEC, "pkg-0==1.0"], "failed": []}
    constraints = tmp_path / "tmp" / "pirbuild" / "build-constraints.txt"
    assert constraints.read_text(encoding="ascii") == "setuptools==75.8.0\nwheel==0.45.1\n"
    assert len(runner.calls) == 2
    for number, (argv, kwargs) in enumerate(runner.calls):
        assert argv == [
            "/usr/local/bin/uv",
            "pip",
            "install",
            "--no-deps",
            "--target",
            "/work/deps",
            "--python",
            "/usr/local/bin/python3.13",
            "--no-config",
            "--no-sources",
            "--offline",
            "--no-index",
            "--find-links",
            str(tmp_path / "buildwheels"),
            "--build-constraints",
            str(constraints),
            "--",
            args["builds"][number]["file"],
        ]
        for flag in ("--no-build", "--index-url", "--only-binary"):
            assert flag not in argv  # the build is allowed, and no index is configured
        env = kwargs["env"]
        assert "UV_NO_BUILD" not in env and env["UV_OFFLINE"] == "1"
        assert env == {
            **{k: v for k, v in resolve.UV_ENV.items() if k != "UV_NO_BUILD"},
            "UV_OFFLINE": "1",
        }
        assert kwargs["cwd"] == str(tmp_path / "tmp" / f"uvbuild-{number}")  # fresh, per package
        assert os.listdir(kwargs["cwd"]) == []
        assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert "capture_output" not in kwargs  # build output is never read
        assert kwargs["check"] is False and kwargs["timeout"] <= proof.BUILD_TIMEOUT_S == 120


def test_ac83_proof_build_reports_built_build_failed_and_build_timeout(tmp_path, pypi):
    args = staged(tmp_path, pypi, names=("pkg-0", "pkg-1"))
    runner = RecordingRunner((0, ""), (1, "ignored"), subprocess.TimeoutExpired(["uv"], 120))
    result = build(tmp_path, runner, args)
    assert result == {
        "built": [SDIST_SPEC],
        "failed": [
            {"spec": "pkg-0==1.0", "reason": "build_failed"},
            {"spec": "pkg-1==1.0", "reason": "build_timeout"},
        ],
    }
    assert "ignored" not in json.dumps(result)  # build output never reaches a result


def test_ac83_a_spent_budget_is_build_timeout_for_the_packages_not_started(
    tmp_path, monkeypatch, pypi
):
    args = staged(tmp_path, pypi, names=("pkg-0",))
    clock = Clock()
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=clock.monotonic))

    def slow(argv, **kwargs):
        assert kwargs["timeout"] == proof.BUILD_TIMEOUT_S  # min(120, 270)
        clock.now += proof.BUILD_BUDGET_S  # the first build uses the whole budget
        return subprocess.CompletedProcess(argv, 0)

    result = build(tmp_path, slow, args)
    assert result == {
        "built": [SDIST_SPEC],
        "failed": [{"spec": "pkg-0==1.0", "reason": "build_timeout"}],
    }
    assert not (tmp_path / "tmp" / "uvbuild-1").exists()  # it never started


def test_ac83_the_per_package_timeout_never_exceeds_the_remaining_budget(
    tmp_path, monkeypatch, pypi
):
    args = staged(tmp_path, pypi)
    clock = Clock()
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(proof, "BUILD_BUDGET_S", 30.0)
    runner = RecordingRunner((0, ""))
    build(tmp_path, runner, args)
    assert runner.calls[0][1]["timeout"] == 30.0


def test_ac83_proof_build_with_no_time_budget_runs_nothing(tmp_path, monkeypatch, pypi):
    args = staged(tmp_path, pypi)
    monkeypatch.setattr(proof, "BUILD_BUDGET_S", 0.0)
    runner = RecordingRunner()
    result = build(tmp_path, runner, args)
    assert result == {"built": [], "failed": [{"spec": SDIST_SPEC, "reason": "build_timeout"}]}
    assert runner.calls == []


def test_proof_build_a_missing_uv_fails_the_command(tmp_path, pypi):
    args = staged(tmp_path, pypi)
    with pytest.raises(protocol.AgentError, match="cannot start uv"):
        build(tmp_path, RecordingRunner(OSError()), args)


def bad_builds(tmp_path, args):
    first = args["builds"][0]
    sdists = tmp_path / "sdists"
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / SDIST_FILE).write_bytes(SDIST_BYTES)
    (sdists / "sub").mkdir()
    (sdists / "sub" / SDIST_FILE).write_bytes(SDIST_BYTES)
    (sdists / "tool.exe").write_bytes(SDIST_BYTES)

    def one(**changes):
        return {"builds": [{**first, **changes}]}

    return {
        "empty": {"builds": []},
        "not a list": {"builds": first},
        "missing": {},
        "too many": {"builds": [first] * 11},
        "duplicate": {"builds": [first, first]},
        "extra key": one(extra=1),
        "missing key": {"builds": [{k: v for k, v in first.items() if k != "sha256"}]},
        "name not normalized": one(name="PyYAML"),
        "version not pep 440": one(version="latest"),
        "sha256 uppercase": one(sha256=first["sha256"].upper()),
        "sha256 mismatch": one(sha256="0" * 64),
        "outside the folder": one(file=str(other / SDIST_FILE)),
        "system path": one(file="/etc/passwd"),
        "dot-dot": one(file=str(sdists / ".." / "sdists" / SDIST_FILE)),
        "subfolder": one(file=str(sdists / "sub" / SDIST_FILE)),
        "not an archive name": one(file=str(sdists / "tool.exe")),
        "missing file": one(file=str(sdists / "nothing.tar.gz")),
        "file is a number": one(file=5),
    }


BAD_BUILD_CASES = [
    "empty",
    "not a list",
    "missing",
    "too many",
    "duplicate",
    "extra key",
    "missing key",
    "name not normalized",
    "version not pep 440",
    "sha256 uppercase",
    "sha256 mismatch",
    "outside the folder",
    "system path",
    "dot-dot",
    "subfolder",
    "not an archive name",
    "missing file",
    "file is a number",
]


@pytest.mark.parametrize("case", BAD_BUILD_CASES)
def test_ac83_proof_build_refuses_bad_arguments_with_exit_2_and_runs_nothing(tmp_path, pypi, case):
    args = staged(tmp_path, pypi)
    runner = RecordingRunner()
    with pytest.raises(protocol.BadInput) as caught:
        build(tmp_path, runner, bad_builds(tmp_path, args)[case])
    assert caught.value.exit_code == 2
    assert runner.calls == []
    assert not (tmp_path / "tmp" / "pirbuild").exists()  # nothing was written either


def test_ac83_proof_build_exits_2_through_the_agent_protocol(tmp_path, pypi):
    args = staged(tmp_path, pypi)
    args["builds"][0]["sha256"] = "0" * 64
    handlers = {"proof-build": lambda a: proof.proof_build_command(a, **build_roots(tmp_path))}
    encoded = base64.b64encode(json.dumps(args).encode()).decode()
    code = protocol.run_command(
        ["proof-build", "--args-b64", encoded], handlers, io.StringIO(), io.StringIO()
    )
    assert code == 2


def test_ac83_a_source_package_that_changed_after_download_is_refused(tmp_path, pypi):
    args = staged(tmp_path, pypi)
    path = Path(args["builds"][0]["file"])
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(protocol.BadInput, match="does not match its sha256"):
        build(tmp_path, RecordingRunner(), args)


def test_ac83_a_symlink_in_place_of_the_source_package_is_refused(tmp_path, pypi):
    args = staged(tmp_path, pypi)
    link = tmp_path / "sdists" / "link.tar.gz"
    try:
        link.symlink_to(Path(args["builds"][0]["file"]))
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create symbolic links here")
    bad = {"builds": [{**args["builds"][0], "file": str(link)}]}
    with pytest.raises(protocol.BadInput, match="not a regular file"):
        build(tmp_path, RecordingRunner(), bad)


def test_ac83_a_source_package_over_the_size_cap_is_refused(tmp_path, monkeypatch, pypi):
    args = staged(tmp_path, pypi)
    monkeypatch.setattr(proof, "MAX_SDIST_BYTES", 5)
    with pytest.raises(protocol.BadInput, match="over"):
        build(tmp_path, RecordingRunner(), args)


def test_ac83_missing_altered_or_extra_build_tool_wheels_are_refused(tmp_path, pypi):
    args = staged(tmp_path, pypi)
    wheels = tmp_path / "buildwheels"
    runner = RecordingRunner()
    altered = wheels / BACKEND_FILES[0]
    altered.write_bytes(altered.read_bytes() + b"x")
    with pytest.raises(protocol.BadInput, match="pinned sha256"):
        build(tmp_path, runner, args)
    altered.unlink()  # missing
    with pytest.raises(protocol.BadInput, match="exactly the pinned wheels"):
        build(tmp_path, runner, args)
    altered.write_bytes(b"restored")
    (wheels / "extra-1.0-py3-none-any.whl").write_bytes(b"x")  # extra
    with pytest.raises(protocol.BadInput, match="exactly the pinned wheels"):
        build(tmp_path, runner, args)
    for child in wheels.iterdir():
        child.unlink()
    wheels.rmdir()  # the folder itself is gone
    with pytest.raises(protocol.BadInput, match="folder is missing"):
        build(tmp_path, runner, args)
    assert runner.calls == []


def test_ac83_proof_build_refuses_to_reuse_its_working_folders(tmp_path, pypi):
    args = staged(tmp_path, pypi)
    (tmp_path / "tmp" / "pirbuild").mkdir(parents=True)
    with pytest.raises(protocol.AgentError, match="already exists"):
        build(tmp_path, RecordingRunner(), args)


def test_ac83_the_real_subprocess_runner_discards_build_output(tmp_path, pypi):
    """A real `subprocess.run` with a stand-in for uv: its output goes nowhere."""
    args = staged(tmp_path, pypi)
    stand_in = tmp_path / "uv.py"
    stand_in.write_text("print('SECRET build output')\n", encoding="utf-8")
    seen = []

    def run_stand_in(_argv, **kwargs):
        done = subprocess.run([sys.executable, "-I", str(stand_in)], **kwargs)
        seen.append(done)
        return done

    assert build(tmp_path, run_stand_in, args) == {"built": [SDIST_SPEC], "failed": []}
    assert seen[0].stdout is None and seen[0].stderr is None  # nothing was captured


# ---- the client: proof_build (AC-83) ------------------------------------------------------------

P2_IMAGE = ImageRef(kind="fake", ref="/stub/p2")
SDIST_ENTRY = {"spec": SDIST_SPEC, "file": f"/work/sdists/{SDIST_FILE}", "sha256": "a" * 64}
SDIST_PIN_LIST = [("pyyaml", "5.3.1")]


def sdist_installed(**kw) -> ExecResult:
    payload = {
        "installed": [],
        "no_wheel": [SDIST_SPEC],
        "sdists": [SDIST_ENTRY],
        "unfetched": [],
        **kw,
    }
    return exec_result(stdout=line(payload), image=P_IMAGE)


def build_ok(**kw) -> ExecResult:
    return exec_result(stdout=line({"built": [SDIST_SPEC], "failed": [], **kw}), image=P2_IMAGE)


class Discarding(Scripted):
    def __init__(self, *outcomes) -> None:
        super().__init__(*outcomes)
        self.discarded: list[ImageRef] = []

    async def discard(self, image):
        self.discarded.append(image)


async def test_ac83_proof_build_is_network_off_saves_p_prime_and_close_discards_both():
    stub = Discarding(sdist_installed(), build_ok())
    client = SandboxAgentClient(stub, SETTINGS)
    ws = workspace()
    installed = await client.proof_install(ws, SDIST_PIN_LIST)
    assert [s.spec for s in installed.sdists] == [SDIST_SPEC] and installed.unfetched == []
    built = await client.proof_build(ws, installed)

    call = stub.calls[1]
    assert call.argv[3:6] == [AGENT_PATH, "proof-build", "--args-b64"]
    assert (call.network, call.save, call.image) == (False, True, P_IMAGE)
    assert call.timeout_s == SETTINGS.timeout_proof_build_s == 300
    assert args_of(call) == {
        "builds": [
            {
                "name": "pyyaml",
                "version": "5.3.1",
                "file": SDIST_ENTRY["file"],
                "sha256": SDIST_ENTRY["sha256"],
            }
        ]
    }
    assert built.image == P2_IMAGE and built.built == [SDIST_SPEC] and built.failed == []
    assert P_IMAGE in ws.saved and P2_IMAGE in ws.saved
    await client.close(ws)
    assert P_IMAGE in stub.discarded and P2_IMAGE in stub.discarded  # P and P' both go


async def test_ac83_proof_build_returns_the_failed_builds_with_their_reasons():
    failed = [{"spec": SDIST_SPEC, "reason": "build_failed"}]
    stub = Scripted(sdist_installed(), build_ok(built=[], failed=failed))
    client = SandboxAgentClient(stub, SETTINGS)
    ws = workspace()
    built = await client.proof_build(ws, await client.proof_install(ws, SDIST_PIN_LIST))
    assert [(f.spec, f.reason) for f in built.failed] == [(SDIST_SPEC, "build_failed")]
    assert built.built == []


async def test_ac83_proof_build_with_nothing_to_build_runs_nothing():
    stub = Scripted(installed_ok())
    client = SandboxAgentClient(stub, SETTINGS)
    ws = workspace()
    installed = await client.proof_install(ws, PIN_LIST)
    assert await client.proof_build(ws, installed) is installed
    assert len(stub.calls) == 1


async def test_ac83_proof_build_refuses_a_proof_image_with_an_unfetched_package():
    unfetched = [{"spec": SDIST_SPEC, "reason": "pypi_error"}]
    stub = Scripted(sdist_installed(sdists=[], unfetched=unfetched))
    client = SandboxAgentClient(stub, SETTINGS)
    ws = workspace()
    installed = await client.proof_install(ws, SDIST_PIN_LIST)
    assert [u.reason for u in installed.unfetched] == ["pypi_error"]
    with pytest.raises(ValueError):
        await client.proof_build(ws, installed)
    assert len(stub.calls) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        build_ok(built=[], failed=[]),  # a requested package is in neither list
        build_ok(built=[SDIST_SPEC, SDIST_SPEC]),  # reported twice
        build_ok(built=["other==1.0"]),  # not the requested set
        build_ok(failed=[{"spec": SDIST_SPEC, "reason": "build_failed"}]),  # in both lists
        build_ok(built=[], failed=[{"spec": SDIST_SPEC, "reason": "weird"}]),  # unknown reason
        build_ok(extra=1),
        exec_result(stdout=line({"built": [SDIST_SPEC], "failed": []})),  # no image saved
        exec_result(exit_code=2, stderr="pir-agent: BadInput"),
        exec_result(truncated=True, image=P2_IMAGE),
        exec_result(stdout="", image=P2_IMAGE),  # no result line
    ],
)
async def test_ac83_proof_build_refuses_a_result_that_does_not_match_the_request(outcome):
    stub = Scripted(sdist_installed(), outcome)
    client = SandboxAgentClient(stub, SETTINGS)
    ws = workspace()
    installed = await client.proof_install(ws, SDIST_PIN_LIST)
    with pytest.raises(SandboxFailed):
        await client.proof_build(ws, installed)


@pytest.mark.parametrize(
    ("last", "expected"),
    [
        (exec_result(timed_out=True, exit_code=-1), SandboxTimeout),
        (ExecutorSpendDenied(SpendDenied("nebius", "scan_budget_reached")), ExecutorSpendDenied),
    ],
)
async def test_ac83_proof_build_timeout_and_spend_denial_pass_through(last, expected):
    client = SandboxAgentClient(Scripted(sdist_installed(), last), SETTINGS)
    ws = workspace()
    installed = await client.proof_install(ws, SDIST_PIN_LIST)
    with pytest.raises(expected):
        await client.proof_build(ws, installed)


@pytest.mark.parametrize(
    "payload",
    [
        {"no_wheel": [SDIST_SPEC], "sdists": [], "unfetched": []},  # in neither list
        {
            "no_wheel": [SDIST_SPEC],
            "sdists": [SDIST_ENTRY],
            "unfetched": [{"spec": SDIST_SPEC, "reason": "no_sdist"}],  # in both
        },
        {"no_wheel": [], "sdists": [SDIST_ENTRY], "unfetched": []},  # not a no_wheel pin
        {"no_wheel": [SDIST_SPEC], "sdists": [{**SDIST_ENTRY, "file": "/tmp/evil.tar.gz"}]},
        {"no_wheel": [SDIST_SPEC], "sdists": [{**SDIST_ENTRY, "file": "/work/sdists/../x.zip"}]},
        {"no_wheel": [SDIST_SPEC], "sdists": [{**SDIST_ENTRY, "sha256": "short"}]},
        {"no_wheel": [SDIST_SPEC], "sdists": [{**SDIST_ENTRY, "extra": 1}]},
    ],
)
async def test_ac83_proof_install_refuses_a_result_that_does_not_account_for_every_pin(payload):
    full = {"installed": [], "sdists": [], "unfetched": [], **payload}
    client = SandboxAgentClient(Scripted(exec_result(stdout=line(full), image=P_IMAGE)), SETTINGS)
    with pytest.raises(SandboxFailed):
        await client.proof_install(workspace(), SDIST_PIN_LIST)


def test_ac83_the_build_timeout_setting_defaults_to_300_and_is_in_env_example():
    assert Settings(_env_file=None).timeout_proof_build_s == 300
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "\nTIMEOUT_PROOF_BUILD_S=300\n" in example


# ---- AC-70 / ADR-0041: the REST body of proof-build, and the fake executor ---------------------


async def test_ac70_rest_networking_is_off_for_proof_build_and_it_has_no_index_url():
    scripted = ScriptedSandbox(
        {
            "fetch": {
                "commit_sha": SHA,
                "files_total": 1,
                "bytes_total": 1,
                "py_files": 1,
                "manifests": [],
            },
            "proof-install": {
                "installed": [],
                "no_wheel": [SDIST_SPEC],
                "sdists": [SDIST_ENTRY],
                "unfetched": [],
            },
            "proof-build": {"built": [SDIST_SPEC], "failed": []},
            "proof-run": PROVEN,
        }
    )
    settings = Settings(_env_file=None)
    executor = ContreeExecutor(settings, client=FakeContree(), transport=scripted)
    client = SandboxAgentClient(executor, settings)
    client._selfcheck_passed = True
    github = ValidatedTarget(
        coords=RepoCoordinates(owner="octo", name="demo", ref=None),
        default_branch="main",
        commit_sha=SHA,
        files_total=1,
        bytes_total=1,
        py_files=1,
        manifests=["requirements.txt"],
    )
    ws = await client.open_workspace(github, budget())
    installed = await client.proof_install(ws, [("pyyaml", "5.3.1")])
    built = await client.proof_build(ws, installed)
    await client.proof_run(ws, built, entry="main:<module>", target="yaml.load", site="main.py:10")

    def shape(body: dict) -> tuple[str, bool, bool]:
        tokens = shlex.split(body["command"])
        command = tokens[tokens.index(AGENT_PATH) + 1]
        return command, body["networking"]["enabled"], body["disposable"]

    assert [shape(b) for b in scripted.posts] == [
        ("fetch", True, False),
        ("proof-install", True, False),  # ON: downloads only, saves P
        ("proof-build", False, False),  # OFF, saves P'
        ("proof-run", False, True),  # OFF, from P'
    ]
    build_body = scripted.posts[2]
    assert "--index-url" not in shlex.split(build_body["command"])
    assert (build_body["uid"], build_body["gid"]) == (65534, 65534)
    assert build_body["command"].startswith("env -i PATH=")
    assert "--index-url" not in proof.build_build_argv("/a.tar.gz", "/c.txt")
    assert built.image != installed.image  # proof-run starts from P', not P
    await client.close(ws)


async def test_the_fake_executor_saves_a_copy_of_the_image_for_a_recorded_proof_build():
    install_payload = {
        "installed": [],
        "no_wheel": [SDIST_SPEC],
        "sdists": [SDIST_ENTRY],
        "unfetched": [],
    }
    fake = FakeExecutor(
        recorded={
            "proof-install": exec_result(stdout=line(install_payload)),
            "proof-build": exec_result(stdout=line({"built": [SDIST_SPEC], "failed": []})),
        }
    )
    client = SandboxAgentClient(fake, SETTINGS)
    ws = await client.open_workspace(
        LocalFixtureTarget(path=str(REPOS / "proof_reached" / "repo"), commit_sha=SHA), budget()
    )
    installed = await client.proof_install(ws, SDIST_PIN_LIST)
    built = await client.proof_build(ws, installed)
    assert built.image.kind == "fake" and built.image not in (installed.image, ws.image)
    assert os.path.isdir(built.image.ref) and os.path.isdir(installed.image.ref)
    await client.close(ws)
    assert not os.path.exists(built.image.ref) and not os.path.exists(installed.image.ref)


# ---- the real uv accepts the proof argv and environment (found live) -------------


def real_uv() -> str:
    found = shutil.which("uv") or str(Path(sys.executable).with_name("uv"))
    for candidate in (found, found + ".exe"):
        if os.path.isfile(candidate):
            return candidate
    pytest.skip("no uv binary next to the test interpreter")


def local_uv_env(env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """The agent's environment with the paths this machine needs (HOME, cache, PATH, SYSTEMROOT)."""
    local = {**env, "HOME": str(tmp_path), "UV_CACHE_DIR": str(tmp_path / "uvcache")}
    local["PATH"] = os.environ.get("PATH", "")
    if "SYSTEMROOT" in os.environ:
        local["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    local["UV_PYTHON_DOWNLOADS"] = "never"
    return local


def run_real_uv(argv: list[str], env: dict[str, str], tmp_path: Path):
    """Run the real uv with the agent's argv, only the binary and the interpreter swapped, and
    `--offline`: it can never reach a network, so any error left is not a usage error."""
    argv = [real_uv(), *argv[1:]]
    argv[argv.index("--python") + 1] = sys.executable
    (tmp_path / "cwd").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        argv,
        cwd=tmp_path / "cwd",
        env=local_uv_env(env, tmp_path),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )


def assert_no_usage_error(done) -> None:
    assert done.returncode != 2, done.stderr
    for text in ("cannot be used with", "unexpected argument", "Usage:"):
        assert text not in done.stderr, done.stderr


@pytest.mark.slow
def test_ac66_the_real_uv_accepts_the_proof_install_argv_and_environment(tmp_path):
    base = proof.build_install_argv("pyyaml==5.3.1", str(tmp_path / "deps"))
    cut = base.index("--")  # options go before `--`
    argv = [*base[:cut], "--offline", *base[cut:]]
    done = run_real_uv(argv, resolve.UV_WHEELS_ENV, tmp_path)
    assert_no_usage_error(done)
    # control: --only-binary :all: plus --no-build is the combination uv rejects outright
    conflicting = [*base[:cut], "--no-build", "--offline", *base[cut:]]
    again = run_real_uv(conflicting, resolve.UV_WHEELS_ENV, tmp_path / "again")
    assert again.returncode == 2 and "cannot be used with" in again.stderr, again.stderr


@pytest.mark.slow
def test_ac83_the_real_uv_accepts_the_proof_build_argv_and_environment(tmp_path):
    wheels, sdists = tmp_path / "wheels", tmp_path / "sdists"
    wheels.mkdir()
    sdists.mkdir()
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("setuptools==75.8.0\nwheel==0.45.1\n", encoding="ascii")
    package = tmp_path / "pkg-1.0"
    package.mkdir()
    (package / "setup.py").write_text(
        "from setuptools import setup\nsetup(name='pkg', version='1.0')\n", encoding="utf-8"
    )
    sdist = sdists / "pkg-1.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        archive.add(package, arcname="pkg-1.0")
    argv = proof.build_build_argv(str(sdist), str(constraints), str(tmp_path / "deps"), str(wheels))
    done = run_real_uv(argv, resolve.UV_BUILD_ENV, tmp_path)
    assert_no_usage_error(done)
    # the build started in isolation and asked for the pinned tool from the (empty) local folder
    assert "setuptools==75.8.0" in done.stderr, done.stderr


def test_the_proof_install_argv_and_environment_never_combine_only_binary_with_no_build():
    argv = proof.build_install_argv("pyyaml==5.3.1")
    assert "--only-binary" in argv and "--no-build" not in argv
    assert "UV_NO_BUILD" not in resolve.UV_WHEELS_ENV
    assert "UV_NO_BUILD" not in resolve.UV_BUILD_ENV
    assert resolve.UV_BUILD_ENV["UV_OFFLINE"] == "1"
    assert "--no-build" in resolve.UV_COMMON_FLAGS  # compile has no --only-binary
    assert resolve.UV_ENV["UV_NO_BUILD"] == "1"


# ---- AC-87: proof-result authenticity nonce verification (ADR-0044) --------------------------


def test_ac87_proof_run_command_includes_nonce_when_provided():
    repo, deps = fixture("proof_reached")
    out = proof.proof_run_command(
        {
            "entry": "app.handler:handle",
            "target": "yaml.unsafe_load",
            "nonce": "test-nonce-12345",
        },
        repo_root=str(repo),
        deps_root=str(deps),
        finish=lambda code: None,
    )
    assert out["status"] == "proven"
    assert out["nonce"] == "test-nonce-12345"


def test_ac87_proof_run_command_omits_nonce_when_not_provided():
    repo, deps = fixture("proof_reached")
    out = proof.proof_run_command(
        {
            "entry": "app.handler:handle",
            "target": "yaml.unsafe_load",
        },
        repo_root=str(repo),
        deps_root=str(deps),
        finish=lambda code: None,
    )
    assert out["status"] == "proven"
    assert "nonce" not in out


@pytest.mark.parametrize(
    "bad_nonce",
    [
        "",  # empty string
        "a" * 65,  # > 64 chars
        "bad nonce with spaces",
        "bad$char!",
        12345,  # not a string
    ],
)
def test_ac87_proof_run_command_refuses_invalid_nonce(bad_nonce):
    repo, deps = fixture("proof_reached")
    with pytest.raises(protocol.BadInput):
        proof.proof_run_command(
            {
                "entry": "app.handler:handle",
                "target": "yaml.unsafe_load",
                "nonce": bad_nonce,
            },
            repo_root=str(repo),
            deps_root=str(deps),
            finish=lambda code: None,
        )


async def test_ac87_client_generates_random_nonce_and_verifies_matching_nonce():
    ws = workspace()
    captured_args = None

    class CaptureExecutor:
        kind = "fake"

        async def run(self, image, argv, *, network, timeout_s, save=False, budget=None):
            nonlocal captured_args
            captured_args = protocol.parse_args_b64(argv[-1])
            # Return matching nonce as the isolated runner harness does
            proven_payload = {**PROVEN, "nonce": captured_args["nonce"]}
            return exec_result(stdout=line(proven_payload))

        async def discard(self, image):
            pass

    client = SandboxAgentClient(CaptureExecutor(), SETTINGS)
    result = await client.proof_run(
        ws,
        a_proof_image(),
        entry="main:<module>",
        target="yaml.load",
    )
    assert captured_args is not None
    assert "nonce" in captured_args
    # Nonce is a cryptographically secure 16-byte hex token (32 hex characters)
    assert len(captured_args["nonce"]) == 32
    assert result.status == "proven"
    assert result.nonce == captured_args["nonce"]
    assert result.recorded_call == PROVEN["recorded_call"]


async def test_ac87_client_rejects_missing_nonce_as_not_proven():
    # Repository code prints a PIR-RESULT line without the random nonce
    stub = Scripted(exec_result(stdout=line(PROVEN)))
    result = await SandboxAgentClient(stub, SETTINGS).proof_run(
        workspace(),
        a_proof_image(),
        entry="main:<module>",
        target="yaml.load",
    )
    assert result.status == "not_proven"
    assert result.recorded_call is None
    assert result.reason == PROOF_UNAUTHENTICATED_REASON
    assert "authenticity check failed" in result.reason


async def test_ac87_client_rejects_mismatched_nonce_as_not_proven():
    # Repository code prints a PIR-RESULT line with a guessed or forged nonce
    forged = {**PROVEN, "nonce": "guessed_or_spoofed_nonce_123"}
    stub = Scripted(exec_result(stdout=line(forged)))
    result = await SandboxAgentClient(stub, SETTINGS).proof_run(
        workspace(),
        a_proof_image(),
        entry="main:<module>",
        target="yaml.load",
        nonce="expected_host_nonce_456",
    )
    assert result.status == "not_proven"
    assert result.recorded_call is None
    assert result.reason == PROOF_UNAUTHENTICATED_REASON
