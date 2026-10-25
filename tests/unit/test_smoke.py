"""Scaffold smoke tests: package import, dependency pins, ruff config, conftest behavior."""

import re
import socket
import subprocess
import sys
import tomllib
from datetime import timedelta
from pathlib import Path

import pytest

import pir

ROOT = Path(__file__).resolve().parents[2]


def test_nfr07_package_imports_with_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", pir.__version__)


def test_nfr07_every_dependency_is_pinned_with_double_equals():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = project["dependencies"] + project["optional-dependencies"]["dev"]
    assert requirements, "no dependencies found"
    for requirement in requirements:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][A-Za-z0-9.]*", requirement), requirement


def test_nfr07_console_script_points_at_cli_main():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"]["pir"] == "pir.cli:main"


def test_nfr07_ruff_flags_langsmith_list_runs(tmp_path):
    snippet = tmp_path / "snippet.py"
    snippet.write_text(
        "from langsmith import Client\n\nlist_runs = Client.list_runs\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(ROOT / "pyproject.toml"),
            str(snippet),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "TID251" in result.stdout
    assert "use client.runs.query()" in result.stdout


def test_nfr07_ruff_bans_print_in_src_but_not_in_cli():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lint = config["tool"]["ruff"]["lint"]
    assert "T20" in lint["select"]
    assert "T20" in lint["per-file-ignores"]["src/pir/cli.py"]
    assert "src/pir/*.py" not in lint["per-file-ignores"]


def test_conftest_blocks_non_loopback_connect():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="may not connect"):
            sock.connect(("192.0.2.1", 9))
        with pytest.raises(RuntimeError, match="may not connect"):
            sock.connect_ex(("example.com", 80))
    finally:
        sock.close()


def test_conftest_allows_loopback_connect():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(server.getsockname())
    finally:
        client.close()
        server.close()


@pytest.mark.live
def test_conftest_live_test_body_is_never_reached_without_flag():
    pytest.fail("a live test must be skipped unless PIR_LIVE_TESTS=1")


def test_conftest_live_marked_test_is_skipped_without_flag(request, monkeypatch):
    import os

    if os.environ.get("PIR_LIVE_TESTS") == "1":
        pytest.skip("live tests enabled")
    live_items = [
        item
        for item in request.session.items
        if item.name == "test_conftest_live_test_body_is_never_reached_without_flag"
    ]
    if not live_items:
        pytest.skip("live sentinel deselected in this run")
    assert live_items[0].get_closest_marker("skip") is not None


def test_conftest_frozen_clock_only_moves_when_advanced(frozen_clock):
    start = frozen_clock.now()
    assert frozen_clock.now() == start
    frozen_clock.advance(90)
    assert frozen_clock.now() - start == timedelta(seconds=90)
    assert frozen_clock.monotonic() == 90
