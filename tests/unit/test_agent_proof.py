"""Call-chain proof, agent and client side (M2-TASK-09).

AC-66 (wheels-only install argv, uv isolation), AC-67 and AC-82 (the harness: reached, not reached,
main block, argparse, exit), AC-68 (no wheel, timeout), AC-69 (sentinel placeholders only), AC-70
(network ON only for proof-install, OFF for proof-run, asserted on the REST body).

The harness tests run `proof_run_command` in-process against the fixture repositories (each has a
`repo/` and a stub `deps/` folder), with `finish` captured instead of `os._exit`. The tests marked
`slow` run the real harness in a subprocess, where it leaves with `os._exit(0)`.
"""

import io
import json
import os
import runpy
import shlex
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest
from fakes.contree import FakeContree
from fakes.http import fixture_json
from fakes.proof import REPOS, fixture, line_of, run_harness_subprocess

from pir import agent_client
from pir.agent_client import (
    AGENT_PATH,
    ENTRY_ENDED_REASON,
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


def install(tmp_path, runner, args=PINS):
    return proof.proof_install_command(
        args, deps_root="/work/deps", tmp_root=str(tmp_path), runner=runner
    )


def test_ac66_install_argv_is_wheels_only_with_the_pin_after_double_dash(tmp_path):
    runner = RecordingRunner()
    result = install(tmp_path, runner)
    assert result == {"installed": ["pyyaml==6.0.2", "requests==2.32.3"], "no_wheel": []}
    assert len(runner.calls) == 2  # one uv call per pin, so a missing wheel is attributed
    argv, _ = runner.calls[0]
    assert argv[:4] == ["/usr/local/bin/uv", "pip", "install", "--no-deps"]
    pair = argv.index("--only-binary")
    assert argv[pair : pair + 2] == ["--only-binary", ":all:"]
    assert argv[argv.index("--target") + 1] == "/work/deps"
    assert argv[argv.index("--python") + 1] == "/usr/local/bin/python3.13"
    for flag in ("--no-build", "--no-config", "--no-sources"):
        assert flag in argv
    assert argv[argv.index("--index-url") + 1] == "https://pypi.org/simple"
    assert argv[-2:] == ["--", "pyyaml==6.0.2"]  # the pin follows `--`, built from validated parts
    assert runner.calls[1][0][-2:] == ["--", "requests==2.32.3"]


def test_uv_install_runs_from_a_fresh_empty_directory_with_the_fixed_environment(tmp_path):
    runner = RecordingRunner()
    install(tmp_path, runner)
    for _, kwargs in runner.calls:
        assert kwargs["cwd"] == str(tmp_path / "uvwd")  # outside /work/repo
        assert not kwargs["cwd"].startswith("/work/repo")
        assert kwargs["env"] == resolve.UV_ENV  # never os.environ
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["check"] is False
        assert kwargs["timeout"] <= resolve.UV_TIMEOUT_S
    assert list((tmp_path / "uvwd").iterdir()) == []


def test_uv_install_refuses_a_working_directory_that_already_exists(tmp_path):
    (tmp_path / "uvwd").mkdir()
    with pytest.raises(protocol.AgentError, match="already exists"):
        install(tmp_path, RecordingRunner())


def test_ac68_a_pin_with_no_wheel_is_listed_not_built(tmp_path):
    runner = RecordingRunner((1, PROOF_STDERR), (0, ""))
    result = install(
        tmp_path,
        runner,
        {"pins": [{"name": "antlr4-python3-runtime", "version": "4.9.3"}, *PINS["pins"][:1]]},
    )
    assert result == {"installed": ["pyyaml==6.0.2"], "no_wheel": ["antlr4-python3-runtime==4.9.3"]}
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


def installed_ok(**kw) -> ExecResult:
    payload = {"installed": ["pyyaml==6.0.2", "requests==2.32.3"], "no_wheel": [], **kw}
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
    stub = Scripted(installed_ok(installed=["pyyaml==6.0.2"], no_wheel=["requests==2.32.3"]))
    result = await SandboxAgentClient(stub, SETTINGS).proof_install(workspace(), PIN_LIST)
    assert result.no_wheel == ["requests==2.32.3"] and result.installed == ["pyyaml==6.0.2"]


@pytest.mark.parametrize(
    "outcome",
    [
        exec_result(stdout=line({"installed": ["pyyaml==6.0.2"], "no_wheel": []}), image=P_IMAGE),
        exec_result(stdout=line({"installed": ["bad pin"], "no_wheel": []}), image=P_IMAGE),
        exec_result(stdout=line({"installed": []}), image=P_IMAGE),
        exec_result(stdout=line({"installed": [], "no_wheel": [], "x": 1}), image=P_IMAGE),
        exec_result(
            stdout=line({"installed": ["pyyaml==6.0.2", "requests==2.32.3"], "no_wheel": []})
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
    stub = Scripted(exec_result(stdout=line(PROVEN)))
    result = await run_proof_step(stub, site="main.py:10")
    (call,) = stub.calls
    assert call.argv[3:6] == [AGENT_PATH, "proof-run", "--args-b64"]
    assert (call.network, call.save, call.image) == (False, False, P_IMAGE)
    assert call.timeout_s == SETTINGS.timeout_proof_run_s == 60
    assert args_of(call) == {"entry": "main:<module>", "target": "yaml.load", "site": "main.py:10"}
    assert result.status == "proven" and result.recorded_call == PROVEN["recorded_call"]


async def test_proof_run_omits_site_unless_given():
    stub = Scripted(exec_result(stdout=line(PROVEN)))
    await run_proof_step(stub)
    assert args_of(stub.calls[0]) == {"entry": "main:<module>", "target": "yaml.load"}


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
            data["metadata"]["result"]["stdout"].update(
                encoding="ascii", value=line(self.results[command])
            )
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
            "proof-install": {"installed": ["pyyaml==5.3"], "no_wheel": []},
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
    fake = FakeExecutor(recorded={"proof-install": exec_result(stdout=line({"installed": []}))})
    client = SandboxAgentClient(fake, SETTINGS)
    ws = await client.open_workspace(
        LocalFixtureTarget(path=str(REPOS / "proof_reached" / "repo"), commit_sha=SHA), budget()
    )
    pins = [("pyyaml", "6.0.2")]
    fake.recorded["proof-install"] = exec_result(
        stdout=line({"installed": ["pyyaml==6.0.2"], "no_wheel": []})
    )
    installed = await client.proof_install(ws, pins)
    assert installed.image.kind == "fake" and installed.image != ws.image
    assert os.path.isdir(installed.image.ref)
    await client.close(ws)
    assert not os.path.exists(installed.image.ref)  # discarded with the workspace
