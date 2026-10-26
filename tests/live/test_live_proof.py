"""Live checks of the call-chain proof (ARCHITECTURE 22.1): run with PIR_LIVE_TESTS=1 (slow + live).

These are the gates for enabling FEATURE_PROOF anywhere (M2-TASK-09): proof-install never runs repo
code (the uv-canary gate), uv accepts `--` before the pin with the full argv, `--no-build` refuses
an sdist, proof-run has no network, and a fixture is proven in a real Sandbox. M2-TASK-16 (ADR-0041)
adds `test_proof_build_pyyaml_sdist_offline`: a source-only pin is downloaded with the network on,
built with it off, and proven. Each test spends a few Sandbox operations (metered at
SANDBOX_USD_PER_SECOND; the whole file is well under USD 1; the ADR-0041 test about USD 0.30).
Credentials come from Settings (NEBIUS_API_KEY, NEBIUS_PROJECT_ID). Nothing here calls an LLM.
"""

import json
import shlex
from pathlib import Path

import pytest
from test_live_sandbox import (
    _PROBE,
    CANARY_MARKER,
    CANARY_OUTPUT,
    SHA,
    _sh,
    _unpack_canary,
)

from pir.agent_client import (
    AGENT_PATH,
    PYTHON,
    LocalFixtureTarget,
    ProofInstallResult,
    SandboxAgentClient,
    _agent_argv,
    build_agent_pyz,
    parse_result,
)
from pir.config import Settings
from pir.executor.base import ImageRef
from pir.executor.contree import ContreeExecutor
from pir.sandbox_agent.proof import BUILD_BACKEND_WHEELS, build_install_argv
from pir.sandbox_agent.resolve import BUILD_NEEDED_RE
from pir.spend import ScanBudget

pytestmark = [pytest.mark.slow, pytest.mark.live]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "repos"
YAML_PIN = ("pyyaml", "6.0.2")  # publishes cp313 wheels; the fixtures use only wheel dependencies
SDIST_PIN = ("antlr4-python3-runtime", "4.9.3")  # sdist only (the M1-TASK-06 fixture)


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


def fixture_target(name: str) -> LocalFixtureTarget:
    return LocalFixtureTarget(path=str(FIXTURES / name / "repo"), commit_sha=SHA)


def pins_args(*pins: tuple[str, str]) -> dict:
    return {"pins": [{"name": name, "version": version} for name, version in pins]}


async def test_uv_repo_interpreter_never_runs(executor, budget):
    """GATE for live proof-install: nothing from the canary repo runs while wheels are installed."""
    image_a = await _unpack_canary(executor, budget)
    saved: list[ImageRef] = [image_a]
    try:
        installed = await executor.run(
            image_a,
            _agent_argv("proof-install", pins_args(YAML_PIN)),
            network=True,
            timeout_s=180,
            save=True,  # this test only: so a following network-OFF run can inspect /tmp
            budget=budget,
        )
        assert installed.image is not None
        saved.append(installed.image)
        assert installed.exit_code == 0, installed.stderr
        assert CANARY_OUTPUT not in installed.stdout + installed.stderr
        result = ProofInstallResult.model_validate(parse_result("proof-install", installed))
        assert result.installed == ["pyyaml==6.0.2"], result  # the install really ran uv

        check = f"test ! -e {CANARY_MARKER} && echo CLEAN || echo CANARY-RAN"
        after = await _sh(executor, installed.image, check, budget=budget)
        assert after.stdout.strip() == "CLEAN", after.stdout
        assert CANARY_OUTPUT not in after.stdout + after.stderr
    finally:
        for image in saved:
            await executor.discard(image)


async def test_uv_accepts_double_dash(executor, budget):
    """`uv pip install` accepts `--` before the pin together with --target and --python."""
    base = await executor.prepare({AGENT_PATH: build_agent_pyz()})
    argv = build_install_argv("pyyaml==6.0.2")
    assert argv[-2:] == ["--", "pyyaml==6.0.2"] and "--target" in argv and "--python" in argv
    script = f"mkdir -p /tmp/uvwd /work/deps && cd /tmp/uvwd && {shlex.join(argv)} && ls /work/deps"
    result = await _sh(executor, base, script, network=True, timeout_s=120, budget=budget)
    assert result.exit_code == 0, result.stderr
    assert "yaml" in result.stdout.split(), result.stdout  # the wheel was unpacked, not built


async def test_uv_no_build_refuses_sdist(executor, budget):
    """Install part: an sdist-only pin is refused without building; the agent lists it."""
    base = await executor.prepare({AGENT_PATH: build_agent_pyz()})
    argv = build_install_argv("antlr4-python3-runtime==4.9.3")
    script = f"mkdir -p /tmp/uvwd /work/deps && cd /tmp/uvwd && exec {shlex.join(argv)}"
    refused = await _sh(executor, base, script, network=True, timeout_s=120, budget=budget)
    assert refused.exit_code != 0 and not refused.timed_out
    assert "Successfully built" not in refused.stderr and "Building" not in refused.stderr
    assert BUILD_NEEDED_RE.search(refused.stderr), refused.stderr

    client = SandboxAgentClient(executor, Settings())
    ws = await client.open_workspace(fixture_target("proof_reached"), budget)
    try:
        installed = await client.proof_install(ws, [YAML_PIN, SDIST_PIN])
        assert installed.installed == ["pyyaml==6.0.2"]
        assert installed.no_wheel == ["antlr4-python3-runtime==4.9.3"]
        # ADR-0041: it was not built (the install above refused it); its source package is saved
        assert [e.spec for e in installed.sdists] == ["antlr4-python3-runtime==4.9.3"]
        assert installed.unfetched == []
    finally:
        await client.close(ws)


async def test_network_off_blocks_egress(executor, settings, budget):
    """Proof-run part: the run configuration (P, uid 65534, network OFF) cannot reach out, and the
    agent's own proof-run still works from P."""
    client = SandboxAgentClient(executor, settings)
    ws = await client.open_workspace(fixture_target("proof_reached"), budget)
    try:
        installed = await client.proof_install(ws, [YAML_PIN])
        proven = await client.proof_run(
            ws, installed, entry="app.handler:handle", target="yaml.unsafe_load"
        )
        assert proven.status == "proven", proven
        argv = [PYTHON, "-I", "-S", "-c", _PROBE]
        off = await executor.run(installed.image, argv, network=False, timeout_s=60, budget=budget)
        assert off.exit_code == 0, off.stderr
        assert "uid 65534" in off.stdout
        assert "DNS-BLOCKED" in off.stdout and "TCP-BLOCKED" in off.stdout, off.stdout
        assert "OK" not in off.stdout
    finally:
        await client.close(ws)


async def test_live_proof(executor, settings, budget):
    """The fixtures in a real Sandbox: reached is proven with the exact line, not reached is not,
    and a PyRL-shaped main block is proven in seconds with the SafeLoader call left unrecorded."""
    client = SandboxAgentClient(executor, settings)
    ws = await client.open_workspace(fixture_target("proof_reached"), budget)
    try:
        installed = await client.proof_install(ws, [YAML_PIN])
        assert installed.no_wheel == [] and installed.installed == ["pyyaml==6.0.2"]
        reached = await client.proof_run(
            ws, installed, entry="app.handler:handle", target="yaml.unsafe_load"
        )
        assert reached.status == "proven"
        assert reached.recorded_call == "yaml.unsafe_load called from app/handler.py:7"
    finally:
        await client.close(ws)

    ws = await client.open_workspace(fixture_target("proof_not_reached"), budget)
    try:
        installed = await client.proof_install(ws, [YAML_PIN])
        missed = await client.proof_run(
            ws, installed, entry="app.handler:handle", target="yaml.unsafe_load"
        )
        assert missed.status == "not_proven" and missed.recorded_call is None
        assert missed.reason == "entry finished without reaching yaml.unsafe_load"
    finally:
        await client.close(ws)

    main = (FIXTURES / "proof_main_block" / "repo" / "main.py").read_text(encoding="utf-8")
    site = next(
        n for n, text in enumerate(main.splitlines(), 1) if "config = yaml.load(stream)" in text
    )
    ws = await client.open_workspace(fixture_target("proof_main_block"), budget)
    try:
        installed = await client.proof_install(ws, [YAML_PIN])
        proven = await client.proof_run(
            ws, installed, entry="main:<module>", target="yaml.load", site=f"main.py:{site}"
        )
        assert proven.status == "proven", proven
        assert proven.recorded_call == f"yaml.load called from main.py:{site}"
    finally:
        await client.close(ws)


PYPI_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "http" / "pypi"
SDIST_FIXTURE = "proof_main_block_sdist"
PYYAML_SDIST_PIN = ("pyyaml", "5.3.1")  # no Linux wheel: only Windows wheels and a source package


async def test_proof_build_pyyaml_sdist_offline(executor, settings, budget):
    """ADR-0041 / AC-83 (about USD 0.30 of promo credit): the source package of pyyaml==5.3.1 is
    downloaded with the network ON (install lists it with PyPI's sha256), built with the network
    OFF, and the PyRL-shaped main block is proven from the built image, which cannot reach out."""
    recorded = json.loads((PYPI_FIXTURES / "pyyaml-5.3.1.json").read_text(encoding="utf-8"))
    pypi_sha = next(u for u in recorded["urls"] if u["filename"] == "PyYAML-5.3.1.tar.gz")[
        "digests"
    ]["sha256"]
    main = (FIXTURES / SDIST_FIXTURE / "repo" / "main.py").read_text(encoding="utf-8")
    site = next(
        n for n, text in enumerate(main.splitlines(), 1) if "config = yaml.load(stream)" in text
    )

    client = SandboxAgentClient(executor, settings)
    ws = await client.open_workspace(fixture_target(SDIST_FIXTURE), budget)
    try:
        installed = await client.proof_install(ws, [PYYAML_SDIST_PIN])
        assert installed.installed == [] and installed.unfetched == [], installed
        assert installed.no_wheel == ["pyyaml==5.3.1"]
        (sdist,) = installed.sdists
        assert sdist.spec == "pyyaml==5.3.1" and sdist.sha256 == pypi_sha, sdist

        # the build-tool wheels in image P match the committed constants (checked in the Sandbox)
        listing = await _sh(
            executor, installed.image, "cd /work/buildwheels && sha256sum *", budget=budget
        )
        assert listing.exit_code == 0, listing.stderr
        on_disk = {
            line.split()[1].lstrip("*"): line.split()[0] for line in listing.stdout.splitlines()
        }
        assert on_disk == {
            f"{name}-{version}-py3-none-any.whl": sha
            for name, (version, sha) in BUILD_BACKEND_WHEELS.items()
        }

        built = await client.proof_build(ws, installed)  # network OFF
        assert built.built == ["pyyaml==5.3.1"] and built.failed == [], built
        assert built.image != installed.image

        proven = await client.proof_run(
            ws, built, entry="main:<module>", target="yaml.load", site=f"main.py:{site}"
        )
        assert proven.status == "proven", proven
        assert proven.recorded_call == f"yaml.load called from main.py:{site}"

        argv = [PYTHON, "-I", "-S", "-c", _PROBE]
        off = await executor.run(built.image, argv, network=False, timeout_s=60, budget=budget)
        assert off.exit_code == 0, off.stderr
        assert "uid 65534" in off.stdout
        assert "DNS-BLOCKED" in off.stdout and "TCP-BLOCKED" in off.stdout, off.stdout
        assert "OK" not in off.stdout
    finally:
        await client.close(ws)
        print(f"proof-build live cost (USD): {budget.usage_usd()}")
