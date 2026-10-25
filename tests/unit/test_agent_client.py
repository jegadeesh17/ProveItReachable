"""SandboxAgentClient: workspace, resolve, index, result parsing and the REST network table.

AC-27 (client part): a timeout is `SandboxTimeout(step)`, never success or `sandbox_failed`;
truncated output, a missing or invalid result line and a non-zero exit are `SandboxFailed`.
Security carry-over: `networking.enabled` per command exactly as ARCHITECTURE 7.2 lists it, and
result parsing refuses more than 8 MiB of base64 and 50 MiB decompressed (ADR-0034).
Base-image check (ADR-0033): `selfcheck` runs once per process after prepare and before the first
fetch or unpack, and any difference from the committed fingerprint fails closed.
"""

import asyncio
import base64
import gzip
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
import uuid
import zipfile
from pathlib import Path

import httpx
import pytest
from fakes.contree import FakeContree
from fakes.http import fixture_json

from pir import agent_client
from pir.agent_client import (
    AGENT_PATH,
    MAX_RESULT_B64_BYTES,
    MAX_RESULT_JSON_BYTES,
    SELFCHECK_TIMEOUT_S,
    TARBALL_PATH,
    FetchResult,
    LocalFixtureTarget,
    ResolveResult,
    SandboxAgentClient,
    SandboxFailed,
    SandboxTimeout,
    build_agent_pyz,
    build_fixture_tarball,
    load_fingerprint,
    parse_result,
)
from pir.config import Settings
from pir.executor.base import ExecResult, ExecutorSpendDenied, ExecutorUnavailable, ImageRef
from pir.executor.contree import ContreeExecutor
from pir.executor.fake import FakeExecutor
from pir.github import RepoCoordinates, ValidatedTarget
from pir.sandbox_agent import protocol
from pir.spend import ScanBudget

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "repos"
SHA = "d" * 40
SETTINGS = Settings(_env_file=None)
SELFCHECK = {
    "python_version": "3.13.11",
    "uv_version": "uv 0.9.30",
    "files": {"/usr/local/bin/python3.13": "a" * 64, "/usr/local/bin/uvx": "missing"},
    "stdlib_tree_sha256": "b" * 64,
}
MISMATCH = "base image does not match the reviewed fingerprint"


@pytest.fixture(autouse=True)
def test_fingerprint(monkeypatch):
    """A synthetic fingerprint for the client logic (the real one is measured with Docker)."""
    fingerprint = {
        "format": 1,
        "base_image": SETTINGS.sandbox_base_image,
        "measured_on": "2026-10-25",
        "selfcheck": SELFCHECK,
    }
    monkeypatch.setattr(agent_client, "load_fingerprint", lambda: fingerprint)
    return fingerprint


def budget(nebius: float = 1.0) -> ScanBudget:
    return ScanBudget("scan", {"nebius": nebius, "tavily": 0.0}, {})


def target(name: str = "aliases") -> LocalFixtureTarget:
    return LocalFixtureTarget(path=str(FIXTURES / name), commit_sha=SHA)


def github_target() -> ValidatedTarget:
    return ValidatedTarget(
        coords=RepoCoordinates(owner="pallets", name="itsdangerous", ref=None),
        default_branch="main",
        commit_sha=SHA,
        files_total=3,
        bytes_total=30,
        py_files=1,
        manifests=["pyproject.toml"],
    )


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


def line(payload: object) -> str:
    return protocol.encode_result(payload)


# ---- result parsing (AC-27 client part) -----------------------------------------------------


def test_parse_result_round_trips_the_agent_encoding():
    payload = {"a": [1, 2], "b": "é"}
    assert parse_result("index", exec_result(stdout=f"log\n{line(payload)}\n")) == payload


def test_ac27_timed_out_is_a_timeout_even_with_exit_zero_and_a_result_line():
    result = exec_result(timed_out=True, exit_code=0, stdout=line({"a": 1}))
    with pytest.raises(SandboxTimeout) as caught:
        parse_result("index", result)
    assert caught.value.step == "index" and not isinstance(caught.value, SandboxFailed)


def test_timed_out_wins_over_truncated():
    with pytest.raises(SandboxTimeout):
        parse_result("fetch", exec_result(timed_out=True, truncated=True, exit_code=-1))


def test_ac27_truncated_output_is_a_failure_even_with_a_valid_line():
    with pytest.raises(SandboxFailed, match="truncated") as caught:
        parse_result("index", exec_result(truncated=True, stdout=line({"a": 1})))
    assert caught.value.step == "index"


@pytest.mark.parametrize("code", [1, 2, 3, 137])
def test_non_zero_exit_is_a_failure_with_the_stderr_tail(code):
    result = exec_result(exit_code=code, stderr="x" * 1000 + "\x1b[31mboom\n")
    with pytest.raises(SandboxFailed) as caught:
        parse_result("resolve", result)
    detail = caught.value.detail
    assert f"exit code {code}" in detail and "boom" in detail
    assert "\x1b" not in detail and len(detail) < 400


@pytest.mark.parametrize(
    "stdout",
    ["", "no marker\n", f"{'PIR-RESULT:'}\n", line({"a": 1}) + "\n" + line({"b": 2})],
)
def test_missing_empty_or_repeated_result_line_is_a_failure(stdout):
    with pytest.raises(SandboxFailed):
        parse_result("index", exec_result(stdout=stdout))


@pytest.mark.parametrize(
    "body",
    [
        "!!!not base64!!!",
        base64.b64encode(b"not gzip").decode(),
        base64.b64encode(gzip.compress(b"not json")).decode(),
        base64.b64encode(gzip.compress(b"[1,2]")).decode(),  # JSON, but not an object
        base64.b64encode(gzip.compress(b"\xff\xfe")).decode(),
        base64.b64encode(gzip.compress(b'{"a": 1}')[:-6]).decode(),  # cut gzip stream
    ],
)
def test_invalid_result_lines_are_failures(body):
    with pytest.raises(SandboxFailed):
        parse_result("index", exec_result(stdout="PIR-RESULT:" + body))


def test_more_than_8_mib_of_base64_is_refused_before_decoding():
    oversized = "PIR-RESULT:" + "A" * (MAX_RESULT_B64_BYTES + 4)
    with pytest.raises(SandboxFailed, match="8 MiB"):
        parse_result("index", exec_result(stdout=oversized))


def test_the_result_cap_is_8_mib_on_both_sides():  # ADR-0034
    assert MAX_RESULT_B64_BYTES == protocol.MAX_RESULT_B64_BYTES == 8388608
    exactly = "PIR-RESULT:" + "A" * MAX_RESULT_B64_BYTES  # at the cap it is decoded (and invalid)
    with pytest.raises(SandboxFailed) as caught:
        parse_result("index", exec_result(stdout=exactly))
    assert "8 MiB" not in caught.value.detail


def test_more_than_50_mib_decompressed_is_refused():
    bomb = gzip.compress(b'{"a":"' + b"x" * (MAX_RESULT_JSON_BYTES + 10) + b'"}', compresslevel=1)
    assert len(bomb) < MAX_RESULT_B64_BYTES  # a small download that expands past the cap
    with pytest.raises(SandboxFailed, match="50 MiB"):
        parse_result("index", exec_result(stdout="PIR-RESULT:" + base64.b64encode(bomb).decode()))


def test_a_result_just_under_the_decompressed_cap_is_accepted():
    body = json.dumps({"a": "x" * 1000}).encode()
    data = base64.b64encode(gzip.compress(body)).decode()
    assert parse_result("index", exec_result(stdout="PIR-RESULT:" + data))["a"] == "x" * 1000


# ---- the strict models ----------------------------------------------------------------------


def test_fetch_result_is_strict():
    good = {
        "commit_sha": SHA,
        "files_total": 3,
        "bytes_total": 10,
        "py_files": 1,
        "manifests": ["pyproject.toml"],
    }
    assert FetchResult.model_validate(good).py_files == 1
    for bad in (
        {**good, "extra": 1},
        {**good, "commit_sha": "abc"},
        {**good, "files_total": -1},
        {**good, "files_total": "3"},
        {**good, "manifests": ["../x"]},
        {k: v for k, v in good.items() if k != "py_files"},
    ):
        with pytest.raises(ValueError):
            FetchResult.model_validate(bad)


def _resolve(**kw) -> dict:
    base = {
        "method": "lockfile",
        "lockfile": "uv.lock",
        "compiled_from": None,
        "python_version": None,
        "dependencies": [],
        "notes": [],
        "needs_compile": False,
    }
    return {**base, **kw}


def test_resolve_result_enforces_the_report_rules():
    assert ResolveResult.model_validate(_resolve()).method == "lockfile"
    dep = {
        "name": "requests",
        "source": "uv.lock",
        "groups": ["main"],
        "version": "2.0",
        "resolved": True,
        "unresolved_reason": None,
        "direct": True,
    }
    assert ResolveResult.model_validate(_resolve(dependencies=[dep])).dependencies[0].resolved
    for bad in (
        _resolve(lockfile=None),  # lockfile iff method == lockfile
        _resolve(method="compiled", compiled_from=None, lockfile=None),
        _resolve(lockfile="../uv.lock"),
        _resolve(dependencies=[{**dep, "name": "Not Normalized"}]),
        _resolve(dependencies=[{**dep, "version": "not a version"}]),
        _resolve(dependencies=[{**dep, "resolved": False}]),
        _resolve(dependencies=[{**dep, "groups": ["bad group"]}]),
        _resolve(dependencies=[{**dep, "osv_status": "checked"}]),
        _resolve(extra=1),
    ):
        with pytest.raises(ValueError):
            ResolveResult.model_validate(bad)


# ---- the agent archive ----------------------------------------------------------------------


def test_agent_pyz_holds_the_package_tree_and_a_root_shim():
    data = build_agent_pyz()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        shim = archive.read("__main__.py").decode()
    assert {"__main__.py", "pir/__init__.py"} <= names
    for module in ("__init__", "__main__", "fetch", "index", "protocol", "resolve", "selfcheck"):
        assert f"pir/sandbox_agent/{module}.py" in names
    assert "from pir.sandbox_agent.__main__ import run" in shim and "run()" in shim
    assert data == build_agent_pyz()  # deterministic, so prepare caches by content


@pytest.mark.slow
def test_agent_pyz_runs_under_isolated_python_and_reports_bad_input(tmp_path):
    pyz = tmp_path / "agent.pyz"
    pyz.write_bytes(build_agent_pyz())
    args = base64.b64encode(json.dumps({"targets": ["yaml"], "sha": SHA}).encode()).decode()
    done = subprocess.run(
        [sys.executable, "-I", "-S", str(pyz), "index", "--args-b64", args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # /work/repo does not exist on this host: the agent loaded from the zip and refused (exit 2)
    assert done.returncode == 2, done.stderr
    assert "repository directory does not exist" in done.stderr


def test_fixture_tarball_has_one_top_directory_and_is_deterministic():
    data = build_fixture_tarball(str(FIXTURES / "aliases"))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = tar.getnames()
    assert names[0] == "repo" and all(n == "repo" or n.startswith("repo/") for n in names)
    assert "repo/main_alias.py" in names
    assert data == build_fixture_tarball(str(FIXTURES / "aliases"))
    with pytest.raises(SandboxFailed):
        build_fixture_tarball(str(FIXTURES / "does-not-exist"))


# ---- the client over FakeExecutor -----------------------------------------------------------


async def test_workspace_resolve_index_and_close_over_the_fake_executor():
    fake = FakeExecutor()
    client = SandboxAgentClient(fake, SETTINGS)
    ws = await client.open_workspace(target("aliases"), budget())
    assert ws.commit_sha == SHA and ws.fetch.py_files >= 3 and ws.image.kind == "fake"
    resolved = await client.resolve(ws)
    assert resolved.method == "failed"
    index = await client.index(ws, ["yaml", "jinja2"])
    assert index.commit_sha == SHA and index.target_modules == ["jinja2", "yaml"]
    # AC-30: the index step went through Executor.run
    assert [c.argv[4] for c in fake.calls] == ["unpack", "resolve", "index"]
    index_call = fake.calls[-1]
    assert index_call.network is False and index_call.timeout_s == SETTINGS.timeout_sandbox_index_s
    await client.close(ws)
    assert ws.closed
    assert not os.path.exists(ws.image.ref)
    await client.close(ws)  # idempotent


async def test_lockfile_repo_resolves_without_a_compile_run():
    fake = FakeExecutor()
    client = SandboxAgentClient(fake, SETTINGS)
    ws = await client.open_workspace(target("locks/uv"), budget())
    result = await client.resolve(ws)
    assert result.method == "lockfile" and result.lockfile == "uv.lock"
    assert [c.argv[4] for c in fake.calls] == ["unpack", "resolve"]
    await client.close(ws)


async def test_needs_compile_runs_compile_with_network_on_in_a_disposable_branch():
    compile_stdout = line(
        _resolve(
            method="compiled",
            lockfile=None,
            compiled_from="requirements.txt",
            python_version="3.12",
            dependencies=[
                {
                    "name": "flask",
                    "source": "uv pip compile",
                    "groups": ["main"],
                    "version": "3.1.0",
                    "resolved": True,
                    "unresolved_reason": None,
                    "direct": True,
                }
            ],
        )
    )
    recorded = {"resolve --compile": exec_result(stdout=compile_stdout)}
    fake = FakeExecutor(recorded)
    client = SandboxAgentClient(fake, SETTINGS)
    ws = await client.open_workspace(target("locks/unpinned_reqs"), budget())
    result = await client.resolve(ws)
    assert result.method == "compiled" and result.dependencies[0].name == "flask"
    first, second = fake.calls[1], fake.calls[2]
    assert (first.network, first.save, first.timeout_s) == (
        False,
        False,
        SETTINGS.timeout_sandbox_resolve_s,
    )
    assert (second.network, second.save, second.timeout_s) == (
        True,
        False,
        SETTINGS.timeout_sandbox_compile_s,
    )
    assert second.image == first.image == ws.image  # both branch from image A
    await client.close(ws)


async def test_the_fake_executor_refuses_github_targets():
    client = SandboxAgentClient(FakeExecutor(), SETTINGS)
    with pytest.raises(SandboxFailed, match="refuses GitHub targets") as caught:
        await client.open_workspace(github_target(), budget())
    assert caught.value.step == "fetch"


class LyingExecutor(FakeExecutor):
    """Echoes a different commit than the one asked for, and remembers the image it saved."""

    saved: ImageRef | None = None

    async def run(self, image, argv, **kw):
        result = await super().run(image, argv, **kw)
        self.saved = result.image
        fixed = line({**protocol.decode_result(result.stdout), "commit_sha": "e" * 40})
        return result.model_copy(update={"stdout": fixed})


async def test_a_different_commit_is_refused_and_its_saved_image_discarded():
    lying = LyingExecutor()
    with pytest.raises(SandboxFailed, match="not the requested commit"):
        await SandboxAgentClient(lying, SETTINGS).open_workspace(target(), budget())
    assert lying.saved is not None and not os.path.exists(lying.saved.ref)


async def test_bad_targets_are_rejected_before_any_run():
    fake = FakeExecutor()
    with pytest.raises(TypeError):
        await SandboxAgentClient(fake, SETTINGS).open_workspace(
            "https://github.com/o/r",  # type: ignore[arg-type]
            budget(),
        )
    with pytest.raises(ValueError):
        LocalFixtureTarget(path="x", commit_sha="short")
    assert fake.calls == []


# ---- failures surface as SandboxTimeout / SandboxFailed -------------------------------------


class StubExecutor:
    """Returns scripted results (or raises) and records what it was asked."""

    kind = "fake"

    def __init__(self, *outcomes, kind: str | None = None) -> None:
        self.kind = kind or self.kind
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.discarded: list[ImageRef] = []

    async def prepare(self, files):
        return ImageRef(kind="fake", ref="/stub/base")

    async def run(self, image, argv, *, network, timeout_s, save=False, budget=None):
        self.calls.append(
            {
                "argv": argv,
                "network": network,
                "save": save,
                "timeout_s": timeout_s,
                "budget": budget,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def discard(self, image):
        self.discarded.append(image)


def good_fetch(**kw) -> ExecResult:
    payload = {
        "commit_sha": SHA,
        "files_total": 1,
        "bytes_total": 1,
        "py_files": 1,
        "manifests": [],
    }
    return exec_result(stdout=line({**payload, **kw}), image=ImageRef(kind="fake", ref="/stub/a"))


async def _workspace(stub: StubExecutor):
    client = SandboxAgentClient(stub, SETTINGS)
    return client, await client.open_workspace(target(), budget())


async def test_ac27_timeouts_map_to_sandbox_timeout_per_step():
    stub = StubExecutor(
        good_fetch(), exec_result(timed_out=True, exit_code=-1), exec_result(timed_out=True)
    )
    client, ws = await _workspace(stub)
    with pytest.raises(SandboxTimeout) as resolve_timeout:
        await client.resolve(ws)
    assert resolve_timeout.value.step == "resolve"
    with pytest.raises(SandboxTimeout) as index_timeout:
        await client.index(ws, ["yaml"])
    assert index_timeout.value.step == "index"


async def test_fetch_timeout_and_failures_discard_a_saved_image():
    saved = ImageRef(kind="fake", ref="/stub/a")
    for outcome, error in (
        (exec_result(timed_out=True, image=saved), SandboxTimeout),
        (exec_result(truncated=True, image=saved), SandboxFailed),
        (exec_result(exit_code=3, stderr="limit", image=saved), SandboxFailed),
        (exec_result(stdout=line({"commit_sha": SHA}), image=saved), SandboxFailed),
        (good_fetch(commit_sha="f" * 40), SandboxFailed),
    ):
        stub = StubExecutor(outcome)
        with pytest.raises(error):
            await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())
        assert stub.discarded == [saved]


async def test_a_fetch_that_saved_no_image_is_a_failure():
    stub = StubExecutor(exec_result(stdout=good_fetch().stdout, image=None))
    with pytest.raises(SandboxFailed, match="no image"):
        await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())


async def test_executor_unavailable_becomes_sandbox_failed_in_every_step():
    stub = StubExecutor(good_fetch(), ExecutorUnavailable("HTTP 500"), ExecutorUnavailable("x"))
    client, ws = await _workspace(stub)
    with pytest.raises(SandboxFailed, match="HTTP 500") as resolve_failed:
        await client.resolve(ws)
    assert resolve_failed.value.step == "resolve"
    with pytest.raises(SandboxFailed) as index_failed:
        await client.index(ws, ["yaml"])
    assert index_failed.value.step == "index"
    stub = StubExecutor(ExecutorUnavailable("auth"))
    with pytest.raises(SandboxFailed) as fetch_failed:
        await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())
    assert fetch_failed.value.step == "unpack"


async def test_spend_denied_propagates_unchanged():
    from pir.spend import SpendDenied

    denied = ExecutorSpendDenied(SpendDenied("nebius", "spend_cap_reached"))
    stub = StubExecutor(good_fetch(), denied)
    client, ws = await _workspace(stub)
    with pytest.raises(ExecutorSpendDenied) as caught:
        await client.index(ws, ["yaml"])
    assert caught.value.denied.reason == "spend_cap_reached"


async def test_invalid_or_foreign_results_are_failures():
    index_ok = {
        "agent_protocol": 1,
        "commit_sha": SHA,
        "target_modules": [],
        "files": [],
        "imports": [],
        "defs": [],
        "target_sites": [],
        "edges": [],
        "truncated": False,
        "stats": {},
    }
    stub = StubExecutor(
        good_fetch(),
        exec_result(stdout=line(_resolve(extra=1))),
        exec_result(stdout=line({**index_ok, "commit_sha": "a" * 40})),
        exec_result(stdout=line({**index_ok, "agent_protocol": 2})),
        exec_result(stdout=line(index_ok)),
    )
    client, ws = await _workspace(stub)
    with pytest.raises(SandboxFailed, match="validation"):
        await client.resolve(ws)
    with pytest.raises(SandboxFailed, match="different commit"):
        await client.index(ws, ["yaml"])
    with pytest.raises(SandboxFailed, match="validation"):
        await client.index(ws, ["yaml"])
    assert (await client.index(ws, [])).commit_sha == SHA


async def test_close_discards_every_saved_image_once():
    stub = StubExecutor(good_fetch())
    client, ws = await _workspace(stub)
    await client.close(ws)
    await client.close(ws)
    assert stub.discarded == [ImageRef(kind="fake", ref="/stub/a")]


# ---- the REST network table through ContreeExecutor ------------------------------------------

BASE_URL = "https://api.tokenfactory.nebius.com/sandboxes"


class ScriptedSandbox(httpx.MockTransport):
    """Answers instance POSTs with a canned operation whose stdout is the agent result line."""

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
            args = protocol.parse_args_b64(tokens[-1])
            key = f"resolve --{args['mode']}" if command == "resolve" else command
            op_id = str(uuid.uuid4())
            data = fixture_json("contree", "op_success.json")
            data["metadata"]["result"]["stdout"].update(
                encoding="ascii", value=line(self.results[key])
            )
            if not body["disposable"]:
                data["result_image_uuid"] = str(uuid.uuid4())
            self._ops[op_id] = data
            return httpx.Response(201, json={"uuid": op_id})
        return httpx.Response(200, json=self._ops[request.url.path.rsplit("/", 1)[1]])


def results_for_all_commands() -> dict[str, object]:  # includes the selfcheck answer
    fetched = {
        "commit_sha": SHA,
        "files_total": 1,
        "bytes_total": 1,
        "py_files": 1,
        "manifests": [],
    }
    index = {
        "agent_protocol": 1,
        "commit_sha": SHA,
        "target_modules": ["yaml"],
        "files": [],
        "imports": [],
        "defs": [],
        "target_sites": [],
        "edges": [],
        "truncated": False,
        "stats": {},
    }
    return {
        "selfcheck": SELFCHECK,
        "fetch": fetched,
        "unpack": fetched,
        "resolve --lockfile-only": _resolve(method="failed", lockfile=None, needs_compile=True),
        "resolve --compile": _resolve(method="failed", lockfile=None),
        "index": index,
    }


async def test_rest_networking_matches_the_section_7_2_table_for_every_command():
    scripted = ScriptedSandbox(results_for_all_commands())
    settings = Settings(_env_file=None)
    executor = ContreeExecutor(settings, client=FakeContree(), transport=scripted)
    client = SandboxAgentClient(executor, settings)

    ws = await client.open_workspace(github_target(), budget())  # fetch
    await client.resolve(ws)  # lockfile-only, then compile (needs_compile is true)
    await client.index(ws, ["yaml"])
    ws2 = await client.open_workspace(target(), budget())  # unpack

    def shape(body: dict) -> tuple[str, bool]:
        tokens = shlex.split(body["command"])
        command = tokens[tokens.index(AGENT_PATH) + 1]
        args = protocol.parse_args_b64(tokens[-1])
        label = f"resolve --{args['mode']}" if command == "resolve" else command
        return label, body["networking"]["enabled"]

    assert [shape(b) for b in scripted.posts] == [
        ("selfcheck", False),  # once per process, before the first fetch
        ("fetch", True),
        ("resolve --lockfile-only", False),
        ("resolve --compile", True),
        ("index", False),
        ("unpack", False),
    ]
    for body in scripted.posts:
        assert (body["uid"], body["gid"]) == (65534, 65534)
        assert body["command"].startswith("env -i PATH=")
        assert "UV_NO_BUILD=1" in body["command"] and "UV_PYTHON_DOWNLOADS=never" in body["command"]
    # fetch and unpack save image A; every other step is a disposable branch of it
    assert [b["disposable"] for b in scripted.posts] == [True, False, True, True, True, False]
    assert ws.image.kind == ws2.image.kind == "sandbox"
    # the fetch command carries the validated parts only, in the --args-b64 JSON
    fetch_args = protocol.parse_args_b64(shlex.split(scripted.posts[1]["command"])[-1])
    assert fetch_args == {
        "owner": "pallets",
        "repo": "itsdangerous",
        "sha": SHA,
        "max_bytes": settings.max_repo_mb * 1024 * 1024,
        "max_files": settings.max_repo_files,
    }
    # the agent image is prepared once per distinct file set, by the SDK, with the constant command
    sdk = executor._sdk()
    assert len(sdk.run_calls) == 2  # agent only (fetch), agent + tarball (unpack)
    assert {TARBALL_PATH} <= set(sdk.run_calls[1]["files"])


async def test_ac27_rest_timed_out_state_becomes_sandbox_timeout_and_truncated_sandbox_failed():
    class Fixed(httpx.MockTransport):
        def __init__(self, name: str) -> None:
            super().__init__(self._handle)
            self.name = name

        def _handle(self, request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(201, json=fixture_json("contree", "instance_created.json"))
            return httpx.Response(200, json=fixture_json("contree", self.name))

    settings = Settings(_env_file=None)
    for name, error in (
        ("op_timed_out.json", SandboxTimeout),
        ("op_truncated.json", SandboxFailed),
    ):
        executor = ContreeExecutor(settings, client=FakeContree(), transport=Fixed(name))
        client = SandboxAgentClient(executor, settings)
        client._selfcheck_passed = True  # this test is about the fetch step, not the base image
        with pytest.raises(error):
            await client.open_workspace(target(), budget())


# ---- the base-image check (ADR-0033) ---------------------------------------------------------


def selfcheck_ok(**kw) -> ExecResult:
    return exec_result(stdout=line({**SELFCHECK, **kw}))


def command_of(call: dict) -> str:
    return call["argv"][4]


async def test_selfcheck_runs_right_after_prepare_before_fetch_offline_unsaved_and_metered():
    stub = StubExecutor(selfcheck_ok(), good_fetch(), kind="sandbox")
    scan_budget = budget()
    await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), scan_budget)
    assert [command_of(c) for c in stub.calls] == ["selfcheck", "unpack"]
    check = stub.calls[0]
    assert (check["network"], check["save"]) == (False, False)
    assert check["timeout_s"] == SELFCHECK_TIMEOUT_S == 60
    assert check["budget"] is scan_budget
    assert check["argv"][-1] == "e30="  # no arguments
    assert protocol.parse_args_b64(check["argv"][-1]) == {}


async def test_selfcheck_runs_before_a_github_fetch_too():
    stub = StubExecutor(selfcheck_ok(), good_fetch(), kind="sandbox")
    await SandboxAgentClient(stub, SETTINGS).open_workspace(github_target(), budget())
    assert [command_of(c) for c in stub.calls] == ["selfcheck", "fetch"]


async def test_selfcheck_runs_once_per_process_not_per_scan():
    stub = StubExecutor(selfcheck_ok(), good_fetch(), good_fetch(), kind="sandbox")
    client = SandboxAgentClient(stub, SETTINGS)
    await client.open_workspace(target(), budget())
    await client.open_workspace(target(), budget())
    assert [command_of(c) for c in stub.calls] == ["selfcheck", "unpack", "unpack"]


async def test_concurrent_first_scans_share_one_selfcheck():
    stub = StubExecutor(selfcheck_ok(), good_fetch(), good_fetch(), kind="sandbox")
    client = SandboxAgentClient(stub, SETTINGS)
    await asyncio.gather(
        client.open_workspace(target(), budget()), client.open_workspace(target(), budget())
    )
    assert sum(command_of(c) == "selfcheck" for c in stub.calls) == 1


async def test_the_fake_executor_skips_the_selfcheck(monkeypatch):
    def refuse():
        raise AssertionError("the fingerprint must not be loaded for the fake executor")

    monkeypatch.setattr(agent_client, "load_fingerprint", refuse)
    fake = FakeExecutor()
    await SandboxAgentClient(fake, SETTINGS).open_workspace(target(), budget())
    assert [c.argv[4] for c in fake.calls] == ["unpack"]


@pytest.mark.parametrize(
    "answer",
    [
        selfcheck_ok(uv_version="uv 0.9.31"),
        selfcheck_ok(stdlib_tree_sha256="c" * 64),
        selfcheck_ok(files={"/usr/local/bin/python3.13": "a" * 64}),
        selfcheck_ok(python_version="3.13.12"),
        exec_result(stdout=line({**SELFCHECK, "extra": 1})),
    ],
)
async def test_a_differing_selfcheck_result_fails_closed_before_any_fetch(answer):
    stub = StubExecutor(answer, good_fetch(), kind="sandbox")
    with pytest.raises(SandboxFailed) as caught:
        await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())
    assert caught.value.step == "selfcheck" and caught.value.detail == MISMATCH
    assert [command_of(c) for c in stub.calls] == ["selfcheck"]  # no fetch or unpack request


async def test_a_differing_base_image_in_the_fingerprint_fails_closed(test_fingerprint):
    other = "ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim@sha256:" + "0" * 64
    test_fingerprint["base_image"] = other
    stub = StubExecutor(selfcheck_ok(), good_fetch(), kind="sandbox")
    with pytest.raises(SandboxFailed) as caught:
        await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())
    assert (caught.value.step, caught.value.detail) == ("selfcheck", MISMATCH)
    assert [command_of(c) for c in stub.calls] == ["selfcheck"]


async def test_a_missing_or_malformed_fingerprint_fails_closed(monkeypatch):
    def broken():
        raise SandboxFailed("selfcheck", MISMATCH)

    monkeypatch.setattr(agent_client, "load_fingerprint", broken)
    stub = StubExecutor(selfcheck_ok(), good_fetch(), kind="sandbox")
    with pytest.raises(SandboxFailed) as caught:
        await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())
    assert (caught.value.step, caught.value.detail) == ("selfcheck", MISMATCH)
    assert not [c for c in stub.calls if command_of(c) in ("fetch", "unpack")]


async def test_a_failed_selfcheck_is_not_cached_and_the_next_scan_retries():
    stub = StubExecutor(
        selfcheck_ok(uv_version="uv 0.1"), selfcheck_ok(), good_fetch(), kind="sandbox"
    )
    client = SandboxAgentClient(stub, SETTINGS)
    with pytest.raises(SandboxFailed):
        await client.open_workspace(target(), budget())
    ws = await client.open_workspace(target(), budget())
    assert ws.commit_sha == SHA
    assert [command_of(c) for c in stub.calls] == ["selfcheck", "selfcheck", "unpack"]


async def test_a_selfcheck_timeout_is_a_timeout_and_is_retried():
    stub = StubExecutor(
        exec_result(timed_out=True, exit_code=-1), selfcheck_ok(), good_fetch(), kind="sandbox"
    )
    client = SandboxAgentClient(stub, SETTINGS)
    with pytest.raises(SandboxTimeout) as caught:
        await client.open_workspace(target(), budget())
    assert caught.value.step == "selfcheck"
    await client.open_workspace(target(), budget())
    assert [command_of(c) for c in stub.calls] == ["selfcheck", "selfcheck", "unpack"]


@pytest.mark.parametrize(
    "bad",
    [
        exec_result(exit_code=1, stderr="boom"),
        exec_result(truncated=True, stdout=line(SELFCHECK)),
        exec_result(stdout="no result line"),
    ],
)
async def test_an_unusable_selfcheck_run_is_a_selfcheck_failure(bad):
    stub = StubExecutor(bad, good_fetch(), kind="sandbox")
    with pytest.raises(SandboxFailed) as caught:
        await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())
    assert caught.value.step == "selfcheck"
    assert [command_of(c) for c in stub.calls] == ["selfcheck"]


async def test_spend_denied_in_the_selfcheck_passes_through_and_nothing_runs():
    from pir.spend import SpendDenied

    denied = ExecutorSpendDenied(SpendDenied("nebius", "scan_budget_reached"))
    stub = StubExecutor(denied, good_fetch(), kind="sandbox")
    with pytest.raises(ExecutorSpendDenied) as caught:
        await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())
    assert caught.value is denied
    assert [command_of(c) for c in stub.calls] == ["selfcheck"]


async def test_sandbox_unavailable_in_the_selfcheck_is_a_selfcheck_failure():
    stub = StubExecutor(ExecutorUnavailable("HTTP 500"), kind="sandbox")
    with pytest.raises(SandboxFailed, match="HTTP 500") as caught:
        await SandboxAgentClient(stub, SETTINGS).open_workspace(target(), budget())
    assert caught.value.step == "selfcheck"


# ---- the fingerprint file --------------------------------------------------------------------


def _fingerprint_dir(monkeypatch, tmp_path, text: str | None) -> None:
    if text is not None:
        (tmp_path / "base_image_fingerprint.json").write_text(text, encoding="utf-8")
    monkeypatch.setattr(agent_client.importlib.resources, "files", lambda package: tmp_path)


def test_load_fingerprint_reads_a_valid_file(monkeypatch, tmp_path):
    good = {
        "format": 1,
        "base_image": SETTINGS.sandbox_base_image,
        "measured_on": "2026-10-25",
        "selfcheck": SELFCHECK,
    }
    _fingerprint_dir(monkeypatch, tmp_path, json.dumps(good))
    assert load_fingerprint() == good


@pytest.mark.parametrize(
    "text",
    [
        None,  # file missing
        "",
        "not json",
        "[]",
        json.dumps({"format": 2, "base_image": "x", "measured_on": "d", "selfcheck": {}}),
        json.dumps({"format": 1, "base_image": 3, "measured_on": "d", "selfcheck": {}}),
        json.dumps({"format": 1, "base_image": "x", "measured_on": "d", "selfcheck": []}),
        json.dumps({"format": 1, "base_image": "x", "measured_on": "d"}),
    ],
)
def test_load_fingerprint_refuses_a_missing_or_malformed_file(monkeypatch, tmp_path, text):
    _fingerprint_dir(monkeypatch, tmp_path, text)
    with pytest.raises(SandboxFailed) as caught:
        load_fingerprint()
    assert (caught.value.step, caught.value.detail) == ("selfcheck", MISMATCH)


def test_the_committed_fingerprint_matches_the_default_base_image():
    import importlib.resources as resources

    resource = resources.files("pir.executor").joinpath("base_image_fingerprint.json")
    if not resource.is_file():
        pytest.skip("blocked on Docker: base_image_fingerprint.json has not been measured yet")
    data = json.loads(resource.read_text("utf-8"))
    assert data["format"] == 1 and data["base_image"] == Settings(_env_file=None).sandbox_base_image
    assert set(data["selfcheck"]) == {"python_version", "uv_version", "files", "stdlib_tree_sha256"}


def test_the_fingerprint_file_is_shipped_as_package_data():
    import tomllib

    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = config["tool"]["setuptools"]["package-data"]
    assert "base_image_fingerprint.json" in package_data["pir.executor"]
