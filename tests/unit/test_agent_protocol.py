"""Sandbox agent protocol (M1-TASK-05): args, result line, exit codes, stdlib-only imports."""

import ast
import base64
import gzip
import io
import json
import os
import sys
from pathlib import Path

import pytest

from pir.sandbox_agent import __main__ as agent_main
from pir.sandbox_agent import protocol

AGENT_DIR = Path(__file__).resolve().parents[2] / "src" / "pir" / "sandbox_agent"


def _b64(payload: object) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _run(argv: list[str], handlers: dict) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = protocol.run_command(argv, handlers, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_result_line_round_trips_through_base64_and_gzip():  # M1-TASK-05 protocol
    payload = {"b": [1, 2, {"z": None}], "a": "café", "commit_sha": "a" * 40}
    line = protocol.encode_result(payload)
    assert line.startswith("PIR-RESULT:")
    assert "\n" not in line
    body = line[len("PIR-RESULT:") :]
    assert json.loads(gzip.decompress(base64.b64decode(body))) == payload
    assert protocol.decode_result(line) == payload


def test_result_line_is_deterministic():  # AC-14
    first = protocol.encode_result({"b": 1, "a": 2})
    assert first == protocol.encode_result({"a": 2, "b": 1})


def test_decode_result_rejects_non_result_and_oversized_lines():
    with pytest.raises(ValueError):
        protocol.decode_result("hello")
    with pytest.raises(ValueError):
        protocol.decode_result("PIR-RESULT:" + "A" * (protocol.MAX_RESULT_B64_BYTES + 1))


def test_success_prints_exactly_one_result_line_and_exits_0():
    code, out, err = _run(
        ["echo", "--args-b64", _b64({"n": 1})], {"echo": lambda args: {"seen": args}}
    )
    assert code == protocol.EXIT_OK == 0
    assert out.count("\n") == 1 and out.endswith("\n")
    assert protocol.decode_result(out) == {"seen": {"n": 1}}
    assert "PIR-RESULT" not in err


@pytest.mark.parametrize(
    "argv",
    [
        ["echo", "--args-b64", "!!!not-base64!!!"],
        ["echo", "--args-b64", base64.b64encode(b"not json").decode()],
        ["echo", "--args-b64", base64.b64encode(b"[1, 2]").decode()],
        ["echo", "--args-b64", ""],
        ["echo"],
        [],
        ["nosuchcommand", "--args-b64", base64.b64encode(b"{}").decode()],
    ],
)
def test_bad_args_exit_2_without_a_result_line(argv):  # M1-TASK-05 protocol: bad args exit 2
    code, out, err = _run(argv, {"echo": lambda args: {}})
    assert code == protocol.EXIT_BAD_INPUT == 2
    assert out == ""
    assert err


def test_args_over_the_cap_are_refused():
    with pytest.raises(protocol.BadInput):
        protocol.parse_args_b64("A" * (protocol.MAX_ARGS_B64_BYTES + 1))


def test_limit_exceeded_exits_3():  # M1-TASK-05 protocol: limit exceeded exits 3
    def handler(args):
        raise protocol.LimitExceeded("too many files")

    code, out, err = _run(["x", "--args-b64", _b64({})], {"x": handler})
    assert code == protocol.EXIT_LIMIT == 3
    assert out == ""
    assert "too many files" in err


def test_other_failures_exit_1_and_never_print_a_result():
    def boom(args):
        raise RuntimeError("kaput")

    code, out, err = _run(["x", "--args-b64", _b64({})], {"x": boom})
    assert code not in (0, 2, 3)
    assert out == ""
    assert "RuntimeError" in err


def test_require_int_rejects_bools_strings_and_out_of_range():
    for bad in (True, "5", 0, 11, None, 1.5):
        with pytest.raises(protocol.BadInput):
            protocol.require_int({"k": bad}, "k", 1, 10)
    assert protocol.require_int({"k": 5}, "k", 1, 10) == 5


def test_main_dispatches_the_part_one_commands():
    assert set(agent_main.HANDLERS) == {"fetch", "unpack", "index"}
    code = agent_main.main(["fetch", "--args-b64", _b64({"owner": "../x"})])
    assert code == 2


def test_run_changes_directory_to_root_first(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(os, "chdir", lambda path: calls.append(f"chdir:{path}"))
    monkeypatch.setattr(agent_main, "main", lambda: calls.append("main") or 0)
    assert agent_main.run() == 0
    assert calls == ["chdir:/", "main"]


def test_sandbox_agent_imports_only_the_standard_library():  # M1-TASK-05 stdlib-only
    sources = sorted(AGENT_DIR.glob("*.py"))
    assert {p.name for p in sources} >= {"__init__.py", "__main__.py", "protocol.py", "fetch.py"}
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    assert node.level == 1, f"{source.name}: relative import leaves the package"
                    continue
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                top = module.split(".")[0]
                assert top in sys.stdlib_module_names or module.startswith("pir.sandbox_agent"), (
                    f"{source.name} imports {module}"
                )
