"""FakeExecutor: in-process unpack / resolve --lockfile-only / index, never a subprocess.

AC-29 / NFR-02 (executor part): a guard that makes every process-spawning call raise stays quiet
across a full unpack -> resolve -> index -> discard cycle. AC-30: the index step is recorded.
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from pir.agent_client import (
    AGENT_PATH,
    TARBALL_PATH,
    FetchResult,
    ResolveResult,
    _agent_argv,
    build_agent_pyz,
    build_fixture_tarball,
    parse_result,
)
from pir.executor.base import ExecResult, ImageRef
from pir.executor.fake import EXIT_UNSUPPORTED, FakeExecutor
from pir.models.index import AnalysisIndex

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "repos"
SHA = "c" * 40
LIMITS = {"max_bytes": 50 * 1024 * 1024, "max_files": 5000}


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every way to start a process raises; the list shows which one was tried."""
    tried: list[str] = []

    def refuse(name: str):
        def raiser(*args, **kwargs):
            tried.append(name)
            raise AssertionError(f"{name} must not be called")

        return raiser

    monkeypatch.setattr(subprocess, "Popen", refuse("subprocess.Popen"))
    monkeypatch.setattr(subprocess, "run", refuse("subprocess.run"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse("create_subprocess_exec"))
    monkeypatch.setattr(asyncio, "create_subprocess_shell", refuse("create_subprocess_shell"))
    monkeypatch.setattr(os, "system", refuse("os.system"))
    return tried


async def _prepared(fake: FakeExecutor, fixture: str) -> ImageRef:
    files = {
        AGENT_PATH: build_agent_pyz(),
        TARBALL_PATH: build_fixture_tarball(str(FIXTURES / fixture)),
    }
    return await fake.prepare(files)


async def _unpack(fake: FakeExecutor, base: ImageRef, **kw) -> ExecResult:
    argv = _agent_argv("unpack", {"sha": SHA, **LIMITS})
    return await fake.run(base, argv, network=False, timeout_s=60, save=True, **kw)


async def test_ac29_full_cycle_never_starts_a_process(no_subprocess):
    fake = FakeExecutor()
    base = await _prepared(fake, "aliases")
    unpacked = await _unpack(fake, base)
    assert unpacked.exit_code == 0 and unpacked.image is not None
    a = unpacked.image
    resolved = await fake.run(
        a,
        _agent_argv("resolve", {"mode": "lockfile-only", "python_version": "3.12"}),
        network=False,
        timeout_s=60,
    )
    indexed = await fake.run(
        a,
        _agent_argv("index", {"targets": ["yaml"], "sha": SHA}),
        network=False,
        timeout_s=60,
    )
    compile_ = await fake.run(
        a,
        _agent_argv("resolve", {"mode": "compile", "python_version": "3.12"}),
        network=True,
        timeout_s=60,
    )
    await fake.discard(a)
    await fake.discard(base)
    assert (resolved.exit_code, indexed.exit_code) == (0, 0)
    assert compile_.exit_code == EXIT_UNSUPPORTED
    assert no_subprocess == []


async def test_unpack_resolve_and_index_results_parse_into_the_strict_models():
    fake = FakeExecutor()
    unpacked = await _unpack(fake, await _prepared(fake, "aliases"))
    fetched = FetchResult.model_validate(parse_result("unpack", unpacked))
    assert fetched.commit_sha == SHA and fetched.py_files >= 3 and fetched.manifests == []
    resolved = await fake.run(
        unpacked.image,
        _agent_argv("resolve", {"mode": "lockfile-only", "python_version": "3.12"}),
        network=False,
        timeout_s=60,
    )
    result = ResolveResult.model_validate(parse_result("resolve", resolved))
    assert result.method == "failed" and result.needs_compile is False
    indexed = await fake.run(
        unpacked.image,
        _agent_argv("index", {"targets": ["yaml"], "sha": SHA}),
        network=False,
        timeout_s=60,
    )
    index = AnalysisIndex.model_validate(parse_result("index", indexed))
    assert index.commit_sha == SHA and index.stats["files_parsed"] >= 3


async def test_lockfile_fixture_resolves_from_the_lockfile():
    fake = FakeExecutor()
    unpacked = await _unpack(fake, await _prepared(fake, "locks/uv"))
    resolved = await fake.run(
        unpacked.image,
        _agent_argv("resolve", {"mode": "lockfile-only", "python_version": "3.12"}),
        network=False,
        timeout_s=60,
    )
    result = ResolveResult.model_validate(parse_result("resolve", resolved))
    assert result.method == "lockfile" and result.lockfile == "uv.lock"
    assert any(d.name == "urllib3" and d.resolved for d in result.dependencies)


async def test_ac30_every_run_is_recorded_with_network_and_command():
    fake = FakeExecutor()
    unpacked = await _unpack(fake, await _prepared(fake, "aliases"))
    await fake.run(
        unpacked.image,
        _agent_argv("index", {"targets": ["yaml"], "sha": SHA}),
        network=False,
        timeout_s=120,
    )
    assert len(fake.calls) == 2
    index_call = fake.calls[-1]
    assert index_call.network is False and index_call.timeout_s == 120 and index_call.save is False
    assert index_call.argv[3:5] == (AGENT_PATH, "index")
    assert fake.calls[0].save is True and fake.calls[0].argv[4] == "unpack"


async def test_runs_cost_nothing_and_are_never_timed_out():
    fake = FakeExecutor()
    unpacked = await _unpack(fake, await _prepared(fake, "aliases"))
    assert unpacked.cost_usd == 0.0
    assert (unpacked.timed_out, unpacked.truncated) == (False, False)
    assert unpacked.image is not None and unpacked.image.kind == "fake"


async def test_an_unsaved_run_leaves_the_source_image_untouched():
    fake = FakeExecutor()
    base = await _prepared(fake, "aliases")
    result = await fake.run(
        base,
        _agent_argv("unpack", {"sha": SHA, **LIMITS}),
        network=False,
        timeout_s=60,
        save=False,
    )
    assert result.exit_code == 0 and result.image is None
    assert not os.path.exists(os.path.join(base.ref, "work", "repo"))


async def test_the_saved_image_is_a_separate_copy_and_a_failed_run_saves_nothing():
    fake = FakeExecutor()
    base = await _prepared(fake, "aliases")
    saved = (await _unpack(fake, base)).image
    assert saved is not None and saved.ref != base.ref
    assert os.path.isdir(os.path.join(saved.ref, "work", "repo"))
    bad = await fake.run(
        base,
        _agent_argv("unpack", {"sha": "not-a-sha", **LIMITS}),
        network=False,
        timeout_s=60,
        save=True,
    )
    assert bad.exit_code == 2 and bad.image is None  # the agent's bad-input exit code


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (_agent_argv("fetch", {"owner": "o", "repo": "r", "sha": SHA, **LIMITS}), "fetch"),
        (_agent_argv("resolve", {"mode": "compile"}), "resolve --compile"),
        (["/bin/echo", "hi"], None),
        ([*_agent_argv("index", {})[:5], "--args-b64"], None),
    ],
)
async def test_other_commands_exit_99_and_fetch_is_refused(argv, key, no_subprocess):
    fake = FakeExecutor()
    base = await _prepared(fake, "aliases")
    result = await fake.run(base, argv, network=True, timeout_s=10)
    assert result.exit_code == EXIT_UNSUPPORTED == 99
    assert result.image is None and result.stdout == ""
    assert no_subprocess == []


async def test_a_recorded_result_is_returned_for_an_unhandled_command():
    recorded = ExecResult(
        exit_code=0,
        stdout="PIR-RESULT:x",
        stderr="",
        timed_out=False,
        truncated=False,
        duration_s=0.1,
        cost_usd=0.0,
        image=None,
    )
    fake = FakeExecutor({"resolve --compile": recorded})
    base = await _prepared(fake, "aliases")
    argv = _agent_argv("resolve", {"mode": "compile"})
    assert await fake.run(base, argv, network=True, timeout_s=10) is recorded
    other = await fake.run(base, _agent_argv("fetch", {}), network=True, timeout_s=10)
    assert other.exit_code == 99


async def test_images_from_elsewhere_are_refused():
    fake = FakeExecutor()
    for image in (ImageRef(kind="sandbox", ref="x"), ImageRef(kind="fake", ref="/nonexistent")):
        with pytest.raises(ValueError, match="own fake images"):
            await fake.run(image, ["x"], network=False, timeout_s=1)


@pytest.mark.parametrize("path", ["opt/pir/a", "/opt/../etc/x"])
async def test_prepare_refuses_relative_and_dotdot_paths(path):
    with pytest.raises(ValueError):
        await FakeExecutor().prepare({path: b"x"})


async def test_discard_removes_the_directory_and_never_raises():
    fake = FakeExecutor()
    image = await _prepared(fake, "aliases")
    assert os.path.isdir(image.ref)
    await fake.discard(image)
    assert not os.path.exists(image.ref)
    await fake.discard(image)  # twice
    await fake.discard(ImageRef(kind="fake", ref="/nonexistent"))
    await fake.discard(ImageRef(kind="sandbox", ref="x"))
