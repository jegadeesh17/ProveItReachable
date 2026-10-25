"""AC-29 / NFR-02: repo code never runs on the host. AC-30: analysis goes through Executor.run.

The canary fixture (`tests/fixtures/repos/canary/`) writes a marker file when its `__init__.py` is
imported or its `setup.py` is run. It goes through the whole pipeline with `FakeExecutor` while
every way of running it on the host raises; the marker must never appear.
"""

import asyncio
import importlib
import os
import runpy
import subprocess
from pathlib import Path

import pytest
from fakes.deps import make_harness

from pir.agent_client import LocalFixtureTarget
from pir.pipeline import run_scan

CANARY = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "canary"
SHA = "c" * 40


class HostExecution(AssertionError):
    """Something tried to run code on the host."""


@pytest.fixture
def host_guard(monkeypatch):
    """Make every way of running repo code on the host raise, and record the attempts."""
    attempts: list[str] = []

    def refuse(name: str):
        def guard(*args, **kwargs):
            attempts.append(name)
            raise HostExecution(f"{name} called during a scan")

        return guard

    real_import_module = importlib.import_module

    def import_module(name, package=None):
        if "canary" in str(name) or "fixtures" in str(name):
            attempts.append(f"import_module({name})")
            raise HostExecution(f"import_module({name}) on a fixture path")
        return real_import_module(name, package)

    monkeypatch.setattr(subprocess, "Popen", refuse("subprocess.Popen"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse("create_subprocess_exec"))
    monkeypatch.setattr(asyncio, "create_subprocess_shell", refuse("create_subprocess_shell"))
    monkeypatch.setattr(os, "system", refuse("os.system"))
    monkeypatch.setattr(runpy, "run_path", refuse("runpy.run_path"))
    monkeypatch.setattr(importlib, "import_module", import_module)
    return attempts


def test_the_canary_fixture_does_fire_when_its_code_is_run(tmp_path, monkeypatch):
    """Control: the marker mechanism works, so 'no marker' below means something."""
    marker = tmp_path / "control-marker"
    monkeypatch.setenv("PIR_CANARY_MARKER", str(marker))
    runpy.run_path(str(CANARY / "__init__.py"), run_name="__control__")
    assert marker.exists()


async def test_ac29_canary_repo_runs_through_the_pipeline_without_firing(
    tmp_path, monkeypatch, host_guard
):
    marker = tmp_path / "canary-marker"
    monkeypatch.setenv("PIR_CANARY_MARKER", str(marker))
    h = make_harness(tmp_path)
    budget = await h.reserve()
    target = LocalFixtureTarget(path=str(CANARY), commit_sha=SHA)

    report = await run_scan(h.deps, target, budget)

    assert host_guard == []  # nothing tried to execute anything on the host
    assert not marker.exists()
    assert not (CANARY / "CANARY_RAN").exists()
    assert not any(c.exists() for c in CANARY.glob("**/__pycache__"))  # never even imported
    # the scan itself worked on the canary: dependencies resolved, finding reached
    assert [d.name for d in report.dependencies] == ["pyyaml"]
    assert [f.verdict for f in report.findings] == ["likely reachable"]


async def test_ac30_the_canary_is_analysed_through_executor_run(tmp_path, host_guard):
    h = make_harness(tmp_path)
    budget = await h.reserve()
    await run_scan(h.deps, LocalFixtureTarget(path=str(CANARY), commit_sha=SHA), budget)
    commands = [c.command for c in h.executor.calls]
    assert any(" unpack " in c for c in commands)
    assert any(" resolve " in c for c in commands)
    assert any(" index " in c for c in commands)
