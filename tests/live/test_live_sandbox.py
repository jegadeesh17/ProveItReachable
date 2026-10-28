"""Live Sandbox gates (ARCHITECTURE 22.1): run with PIR_LIVE_TESTS=1 (slow + live).

Each test spends a few Sandbox operations (free in the beta, metered anyway). Credentials come
from Settings (NEBIUS_API_KEY, NEBIUS_PROJECT_ID).

`images.oci` cannot import an `@sha256:` reference (the Sandbox importer answers 404 for any
digest reference), so ContreeExecutor imports the version tag and `selfcheck` proves the image
equals the committed fingerprint (ADR-0033). Run the whole file again after any image change.
"""

import importlib.resources
import importlib.util
import io
import json
import shlex
import tarfile
from pathlib import Path

import pytest

from pir.agent_client import (
    AGENT_PATH,
    PYTHON,
    TARBALL_PATH,
    FetchResult,
    LocalFixtureTarget,
    ResolveResult,
    SandboxAgentClient,
    SandboxFailed,
    _agent_argv,
    build_agent_pyz,
    parse_result,
)
from pir.config import Settings
from pir.executor.base import ExecResult, ImageRef
from pir.executor.contree import ContreeExecutor
from pir.github import parse_repo_url, validate_repo
from pir.sandbox_agent.resolve import BUILD_NEEDED_RE, UV_BIN, build_uv_argv
from pir.spend import ScanBudget

pytestmark = [pytest.mark.slow, pytest.mark.live]

TESTS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURES = TESTS_FIXTURES / "repos"
SHA = "a" * 40
SANDBOX_FIXTURES = TESTS_FIXTURES / "http" / "contree"
SHARED_SENTENCES = (
    "Because antlr4-python3-runtime==4.9.3 has no usable wheels and you require "
    "antlr4-python3-runtime==4.9.3, we can conclude that your requirements are unsatisfiable.",
    "hint: Wheels are required for `antlr4-python3-runtime` because building from source is "
    "disabled for all packages (i.e., with `--no-build`)",
)


def _load_canary():
    spec = importlib.util.spec_from_file_location(
        "uv_canary_fixture", TESTS_FIXTURES / "uv_canary.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canary = _load_canary()
CANARY_MARKER, CANARY_OUTPUT, uv_canary_tree = (
    canary.CANARY_MARKER,
    canary.CANARY_OUTPUT,
    canary.uv_canary_tree,
)


@pytest.fixture
def settings() -> Settings:
    loaded = Settings()
    if loaded.nebius_api_key is None or not loaded.nebius_project_id:
        pytest.skip("NEBIUS_API_KEY and NEBIUS_PROJECT_ID are required")
    return loaded


@pytest.fixture
def executor(settings: Settings) -> ContreeExecutor:
    return ContreeExecutor(settings)


@pytest.fixture
def budget() -> ScanBudget:
    return ScanBudget("live", {"nebius": 5.0, "tavily": 0.0}, {})


def _tarball(tree: dict) -> bytes:
    """A gzip tarball of the canary tree (modes and the in-tree symlink preserved)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for relative, entry in tree.items():
            info = tarfile.TarInfo(f"repo/{relative}")
            if entry.link_to is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = entry.link_to
                info.mode = 0o777
                tar.addfile(info)
                continue
            data = entry.content.encode("utf-8")
            info.size = len(data)
            info.mode = entry.mode
            tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


async def _sh(executor, image, script, *, network=False, save=False, timeout_s=60, budget=None):
    return await executor.run(
        image, ["/bin/sh", "-c", script], network=network, timeout_s=timeout_s, save=save,
        budget=budget,
    )  # fmt: skip


async def _unpack_canary(executor, budget) -> ImageRef:
    files = {AGENT_PATH: build_agent_pyz(), TARBALL_PATH: _tarball(uv_canary_tree())}
    base = await executor.prepare(files)
    args = {"sha": SHA, "max_bytes": 50 * 1024 * 1024, "max_files": 5000}
    unpacked = await executor.run(
        base, _agent_argv("unpack", args), network=False, timeout_s=60, save=True, budget=budget
    )
    FetchResult.model_validate(parse_result("unpack", unpacked))
    assert unpacked.image is not None
    return unpacked.image


async def test_base_image_fingerprint_matches(executor, settings, budget):
    """ADR-0033: the version tag imports with the tag= override and selfcheck equals the file."""
    ref = settings.sandbox_base_image
    assert ref and "@sha256:" in ref
    resource = importlib.resources.files("pir.executor").joinpath("base_image_fingerprint.json")
    assert resource.is_file(), "blocked: base_image_fingerprint.json has not been measured"
    fingerprint = json.loads(resource.read_text("utf-8"))
    assert fingerprint["format"] == 1 and fingerprint["base_image"] == ref
    base = await executor.prepare({AGENT_PATH: build_agent_pyz()})
    done = await executor.run(
        base,
        _agent_argv("selfcheck", {}),
        network=False,
        timeout_s=60,
        save=False,
        budget=budget,
    )
    assert done.image is None
    assert parse_result("selfcheck", done) == fingerprint["selfcheck"]
    # the client's own check passes on the same image, and its failure type is what scans see
    client = SandboxAgentClient(executor, settings)
    try:
        ws = await client.open_workspace(
            LocalFixtureTarget(path=str(FIXTURES / "locks" / "uv"), commit_sha=SHA), budget
        )
    except SandboxFailed as exc:  # pragma: no cover - only on a real mismatch
        pytest.fail(f"selfcheck failed in the client: {exc}")
    await client.close(ws)


_PROBE = (
    "import os, socket\n"
    "print('uid', os.getuid())\n"
    "try:\n"
    "    socket.getaddrinfo('pypi.org', 443)\n"
    "    print('DNS-OK')\n"
    "except OSError:\n"
    "    print('DNS-BLOCKED')\n"
    "try:\n"
    "    socket.create_connection(('1.1.1.1', 443), timeout=5).close()\n"
    "    print('TCP-OK')\n"
    "except OSError:\n"
    "    print('TCP-BLOCKED')\n"
)


async def test_network_off_blocks_egress(executor, settings, budget):
    client = SandboxAgentClient(executor, settings)
    ws = await client.open_workspace(
        LocalFixtureTarget(path=str(FIXTURES / "locks" / "uv"), commit_sha=SHA), budget
    )
    try:
        # the two network-OFF agent commands still work from image A ...
        assert (await client.resolve(ws)).method == "lockfile"
        assert (await client.index(ws, ["yaml"])).commit_sha == SHA
        # ... and the same run configuration (A, uid 65534, network OFF) cannot reach out
        argv = [PYTHON, "-I", "-S", "-c", _PROBE]
        off = await executor.run(ws.image, argv, network=False, timeout_s=60, budget=budget)
        assert off.exit_code == 0, off.stderr
        assert "uid 65534" in off.stdout
        assert "DNS-BLOCKED" in off.stdout and "TCP-BLOCKED" in off.stdout, off.stdout
        assert "OK" not in off.stdout
        # control: the probe does see the network when it is switched on
        on = await executor.run(ws.image, argv, network=True, timeout_s=60, budget=budget)
        assert "DNS-OK" in on.stdout and "TCP-OK" in on.stdout, on.stdout
    finally:
        await client.close(ws)


async def test_uv_repo_interpreter_never_runs(executor, budget):
    """GATE for live `resolve --compile`: nothing from the canary repo runs during a compile."""
    image_a = await _unpack_canary(executor, budget)
    saved: list[ImageRef] = [image_a]
    try:
        compiled = await executor.run(
            image_a,
            _agent_argv("resolve", {"mode": "compile", "python_version": "3.12"}),
            network=True,
            timeout_s=180,
            save=True,  # this test only: so a following network-OFF run can inspect /tmp
            budget=budget,
        )
        assert compiled.image is not None
        saved.append(compiled.image)
        assert compiled.exit_code == 0, compiled.stderr
        assert CANARY_OUTPUT not in compiled.stdout + compiled.stderr
        result = ResolveResult.model_validate(parse_result("resolve", compiled))
        # the compile really ran uv on the sanitized requirements (so the check is meaningful)
        assert result.method == "compiled", result.notes
        assert {"requests", "pyyaml"} <= {d.name for d in result.dependencies if d.resolved}

        check = f"test ! -e {CANARY_MARKER} && echo CLEAN || echo CANARY-RAN"
        after = await _sh(executor, compiled.image, check, budget=budget)
        assert after.stdout.strip() == "CLEAN", after.stdout
        assert CANARY_OUTPUT not in after.stdout + after.stderr

        # control: the same check does notice a marker written by a saved run
        marked = await _sh(
            executor, image_a, f"touch {CANARY_MARKER}", save=True, budget=budget
        )  # fmt: skip
        assert marked.image is not None
        saved.append(marked.image)
        seen = await _sh(executor, marked.image, check, budget=budget)
        assert seen.stdout.strip() == "CANARY-RAN"
    finally:
        for image in saved:
            await executor.discard(image)


async def test_uv_no_build_refuses_sdist(executor, budget):
    """Compile part: `uv pip compile --no-build` on an sdist-only package fails without building."""
    base = await executor.prepare({AGENT_PATH: build_agent_pyz()})
    local = (FIXTURES / "locks" / "uv_no_build_sdist.stderr.txt").read_text(encoding="utf-8")
    pinned = (SANDBOX_FIXTURES / "uv_no_build_sdist.sandbox.stderr.txt").read_text(encoding="utf-8")
    argv = build_uv_argv("/tmp/pirin/requirements.in", "3.12", "/tmp/pirin/requirements.txt")
    assert argv[0] == UV_BIN and "--no-build" in argv
    script = (
        "mkdir -p /tmp/pirin /tmp/uvwd && "
        "printf 'antlr4-python3-runtime==4.9.3\\n' > /tmp/pirin/requirements.in && "
        f"cd /tmp/uvwd && exec {shlex.join(argv)}"
    )
    result: ExecResult = await _sh(
        executor, base, script, network=True, timeout_s=120, budget=budget
    )
    assert result.exit_code != 0 and not result.timed_out
    assert "Successfully built" not in result.stderr and "Building" not in result.stderr
    assert result.stdout == ""  # nothing compiled
    assert BUILD_NEEDED_RE.search(result.stderr), result.stderr
    # The image's uv (0.9.30) words the error differently from the dev uv that recorded the
    # fixture (wrapping, "x ... " heading): its exact text is pinned beside the REST
    # fixtures, and both texts carry the same cause and hint sentences.
    assert result.stderr == pinned
    for text in (result.stderr, local):
        flat = " ".join(text.split())
        for sentence in SHARED_SENTENCES:
            assert sentence in flat, (sentence, flat)


async def test_sandbox_round_trip_fetch_resolve_index_and_uid(executor, settings, budget):
    coords = parse_repo_url("https://github.com/pallets/itsdangerous")
    target = await validate_repo(coords, None, settings=settings)
    client = SandboxAgentClient(executor, settings)
    ws = await client.open_workspace(target, budget)  # fetch, network ON
    try:
        assert ws.fetch.commit_sha == target.commit_sha
        assert ws.fetch.py_files > 0 and "pyproject.toml" in ws.fetch.manifests
        resolved = await client.resolve(ws)
        assert resolved.method in ("lockfile", "compiled"), resolved.notes
        assert resolved.dependencies
        index = await client.index(ws, ["itsdangerous"])
        assert index.commit_sha == target.commit_sha and index.stats["files_parsed"] > 0
        who = await executor.run(
            ws.image, ["/usr/bin/id", "-u"], network=False, timeout_s=30, budget=budget
        )
        assert who.stdout.strip() == "65534"
        assert budget.usage_usd()["nebius"] > 0  # the Sandbox runs were metered
        print(  # noqa: T201
            "\nround trip:", target.coords.owner, target.coords.name, target.commit_sha[:12],
            "deps", len(resolved.dependencies), "index files", index.stats["files_parsed"],
        )  # fmt: skip
    finally:
        await client.close(ws)
