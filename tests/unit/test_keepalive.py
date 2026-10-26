"""Keepalive check: evaluate, main and the stdlib-only rule (AC-77, ARCHITECTURE 19.3).

Nothing here touches the network: `main` is given an https URL only where `_get` is stubbed.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pir import keepalive

ROOT = Path(__file__).resolve().parents[2]
KEEPALIVE = ROOT / "src" / "pir" / "keepalive.py"
HEALTHY_POOLS = {"nebius": 0.9, "tavily": 0.5}


def health(pools=HEALTHY_POOLS, status: str = "ok", **extra) -> bytes:
    body = {
        "status": status,
        "version": "0.1.0",
        "live_scans": "enabled",
        "budget_remaining": pools,
    }
    body.update(extra)
    return json.dumps(body).encode()


def test_ac77_all_pools_at_or_above_threshold_exit_0():
    code, message = keepalive.evaluate(200, health())
    assert code == 0
    assert message.startswith("OK")


def test_ac77_pool_exactly_at_threshold_is_not_an_alert():
    code, _ = keepalive.evaluate(200, health({"nebius": 0.25, "tavily": 1.0}))
    assert code == 0


def test_ac77_pool_below_threshold_exit_1_names_it():
    code, message = keepalive.evaluate(200, health({"nebius": 0.18, "tavily": 0.9}))
    assert code == 1
    assert message == "Budget alert: nebius at 0.18 (below 0.25)"


def test_ac77_every_low_pool_is_named():
    code, message = keepalive.evaluate(200, health({"nebius": 0.1, "tavily": 0.2}))
    assert code == 1
    assert "Budget alert: nebius at 0.1 (below 0.25)" in message
    assert "Budget alert: tavily at 0.2 (below 0.25)" in message


def test_ac77_threshold_can_be_passed():
    code, _ = keepalive.evaluate(200, health({"nebius": 0.4}), min_fraction=0.5)
    assert code == 1


@pytest.mark.parametrize("status", [199, 201, 301, 404, 500, 503])
def test_ac77_non_200_exit_2(status: int):
    code, message = keepalive.evaluate(status, health())
    assert code == 2
    assert f"HTTP {status}" in message


@pytest.mark.parametrize(
    "body",
    [b"", b"<html>sleeping</html>", b"{not json", b"\xff\xfe", b"[1, 2]", b'"ok"'],
    ids=["empty", "html", "truncated", "bad-utf8", "list", "string"],
)
def test_ac77_invalid_json_exit_2(body: bytes):
    code, message = keepalive.evaluate(200, body)
    assert code == 2
    assert "Health check failed" in message


def test_ac77_status_not_ok_exit_2():
    code, message = keepalive.evaluate(200, health(status="degraded"))
    assert code == 2
    assert "status is not ok" in message


def test_ac77_missing_status_exit_2():
    body = json.dumps({"budget_remaining": {"nebius": 0.9}}).encode()
    assert keepalive.evaluate(200, body)[0] == 2


def test_ac77_budget_null_exit_2():
    code, message = keepalive.evaluate(200, health(pools=None))
    assert code == 2
    assert "budget_remaining is null or missing" in message


def test_ac77_budget_missing_exit_2():
    body = json.dumps({"status": "ok"}).encode()
    assert keepalive.evaluate(200, body)[0] == 2


@pytest.mark.parametrize(
    "pools",
    [{}, {"nebius": None}, {"nebius": "0.9"}, {"nebius": True}, {"nebius": 1.5}, {"nebius": -0.1}],
    ids=["empty", "null", "string", "bool", "above-one", "negative"],
)
def test_ac77_invalid_fraction_exit_2(pools: dict):
    assert keepalive.evaluate(200, health(pools=pools))[0] == 2


def test_ac77_health_check_failure_never_reports_a_pool_alert():
    # A failed check is exit 2 even when the body would otherwise name a low pool.
    code, _ = keepalive.evaluate(503, health({"nebius": 0.01}))
    assert code == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/api/health",
        "ftp://example.com/api/health",
        "https:///api/health",
        "api/health",
        "",
    ],
    ids=["http", "ftp", "no-host", "relative", "empty"],
)
def test_ac77_main_rejects_non_https_url_without_fetching(
    monkeypatch: pytest.MonkeyPatch, url: str
):
    def no_fetch(_url: str):
        raise AssertionError("a rejected URL must not be fetched")

    monkeypatch.setattr(keepalive, "_get", no_fetch)
    assert keepalive.main([url]) == 2


def test_ac77_main_accepts_https_and_returns_evaluate_result(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(keepalive, "_get", lambda url: (200, health({"nebius": 0.18})))
    assert keepalive.main(["https://example.com/api/health"]) == 1


def test_ac77_main_exit_0_when_healthy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(keepalive, "_get", lambda url: (200, health()))
    assert keepalive.main(["https://example.com/api/health"]) == 0


def test_ac77_main_non_200_exit_2(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(keepalive, "_get", lambda url: (503, b""))
    assert keepalive.main(["https://example.com/api/health"]) == 2


def test_ac77_main_retries_network_errors_three_tries_20s_apart(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    calls: list[str] = []
    sleeps: list[float] = []

    def down(url: str):
        calls.append(url)
        raise TimeoutError("timed out")

    monkeypatch.setattr(keepalive, "_get", down)
    monkeypatch.setattr(keepalive.time, "sleep", sleeps.append)
    assert keepalive.main(["https://example.com/api/health"]) == 2
    assert len(calls) == 3
    assert sleeps == [20.0, 20.0]
    assert "no response after 3 tries (TimeoutError)" in capsys.readouterr().err


def test_ac77_main_succeeds_on_a_later_try(monkeypatch: pytest.MonkeyPatch):
    answers = iter([OSError("refused"), (200, health())])
    sleeps: list[float] = []

    def flaky(url: str):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(keepalive, "_get", flaky)
    monkeypatch.setattr(keepalive.time, "sleep", sleeps.append)
    assert keepalive.main(["https://example.com/api/health"]) == 0
    assert sleeps == [20.0]


def test_ac77_main_rejects_bad_min_fraction():
    with pytest.raises(SystemExit) as excinfo:
        keepalive.main(["https://example.com/api/health", "--min-fraction", "1.5"])
    assert excinfo.value.code == 2


def test_ac77_module_imports_only_the_standard_library():
    tree = ast.parse(KEEPALIVE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports: the file runs as a script"
            imported.add(node.module.split(".")[0])
    assert imported, "the module should import something"
    assert imported <= set(sys.stdlib_module_names)
    assert "pir" not in imported


def test_ac77_script_runs_from_its_folder_without_shadowing_stdlib():
    # src/pir/http.py must not shadow the standard library `http` package (python -I).
    result = subprocess.run(
        [sys.executable, "-I", str(KEEPALIVE), "http://example.invalid/api/health"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "must start with https://" in result.stderr
