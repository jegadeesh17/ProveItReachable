"""The `scan` command (AC-31, AC-32, section 8 admission messages, spend printed to stderr)."""

import json
import logging
from pathlib import Path

import pytest
from fakes.deps import make_harness, write_repo

from pir import messages
from pir.cli import local_commit_sha, main
from pir.github import GitHubUnavailable, NotPythonProject, RepoNotFound, RepoTooLarge
from pir.models.report import ScanReport

SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "report.schema.json"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "repos"


@pytest.fixture(autouse=True)
def cli_env(tmp_path, monkeypatch):
    """No real .env and no real keys: run from an empty directory with fake keys in the env."""
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "NEBIUS_API_KEY": "cli-nebius-key",
        "NEBIUS_PROJECT_ID": "cli-project",
        "TAVILY_API_KEY": "cli-tavily-key",
        "DATA_DIR": str(tmp_path / "data"),
        "SPEND_LEDGER_PATH": str(tmp_path / "ledger.json"),
    }.items():
        monkeypatch.setenv(name, value)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def use_harness(monkeypatch, tmp_path, **kwargs):
    h = make_harness(tmp_path / "h", **kwargs)
    monkeypatch.setattr("pir.cli.build_deps", lambda settings: h.deps)
    return h


def forbid_deps(monkeypatch):
    def refuse(settings):  # pragma: no cover - must not be reached
        raise AssertionError("build_deps was called")

    monkeypatch.setattr("pir.cli.build_deps", refuse)


def test_ac32_fake_local_scan_writes_json_that_validates(tmp_path, monkeypatch, capsys):
    use_harness(monkeypatch, tmp_path)
    repo = write_repo(tmp_path / "repo")
    out = tmp_path / "report.json"

    code = main(["scan", "--executor", "fake", "--local", str(repo), "--out", str(out)])

    assert code == 0
    text = out.read_text(encoding="utf-8")
    data = json.loads(text)
    report = ScanReport.model_validate_json(text)  # the strict model is the published schema
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert set(schema["required"]) <= set(data)
    assert data["repo"]["url"].startswith("https://github.com/local/")
    assert data["repo"]["commit_sha"] == local_commit_sha(repo)
    assert [d["resolved"] for d in data["dependencies"]] == [True]
    assert [s["name"] for s in data["steps"]] == [
        "resolve",
        "osv",
        "research",
        "analysis",
        "reasoning",
        "report",
    ]
    assert [f["verdict"] for f in data["findings"]] == ["likely reachable"]
    assert report.outcome == "findings"
    assert capsys.readouterr().out == ""  # the report went to the file


def test_ac32_report_goes_to_stdout_without_out_and_spend_to_stderr(tmp_path, monkeypatch, capsys):
    use_harness(monkeypatch, tmp_path)
    repo = write_repo(tmp_path / "repo")

    assert main(["scan", "--executor", "fake", "--local", str(repo)]) == 0

    captured = capsys.readouterr()
    ScanReport.model_validate_json(captured.out)
    assert "spend nebius: used $" in captured.err and "remaining $" in captured.err
    assert "spend tavily: used $" in captured.err


def test_ac32_local_scan_with_a_url_uses_it_as_the_repo_identity(tmp_path, monkeypatch, capsys):
    use_harness(monkeypatch, tmp_path)
    repo = write_repo(tmp_path / "repo")
    url = "https://github.com/octocat/hello-world"

    assert main(["scan", url, "--executor", "fake", "--local", str(repo)]) == 0

    assert json.loads(capsys.readouterr().out)["repo"]["url"] == url


def test_ac32_invalid_url_exits_2_with_the_exact_message_and_makes_no_request(monkeypatch, capsys):
    forbid_deps(monkeypatch)  # no clients are built, so no request can be made
    code = main(["scan", "http://github.com/owner/repo"])
    assert code == 2
    assert capsys.readouterr().err == messages.message("invalid_url") + "\n"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.com/o/r",
        "https://user@github.com/o/r",
        "https://github.com:8443/o/r",
        "https://127.0.0.1/o/r",
        "https://api.github.com/repos/o/r",
        "not a url",
    ],
)
def test_ac32_other_hostile_urls_are_refused_before_anything_else(monkeypatch, capsys, url):
    forbid_deps(monkeypatch)
    assert main(["scan", url]) == 2
    assert capsys.readouterr().err == messages.message("invalid_url") + "\n"


def test_an_invalid_ref_exits_2(monkeypatch, capsys):
    forbid_deps(monkeypatch)
    assert main(["scan", "https://github.com/o/r", "--ref", "a/../b"]) == 2
    assert "not valid" in capsys.readouterr().err


def test_fake_executor_needs_a_local_fixture(monkeypatch, capsys):
    forbid_deps(monkeypatch)
    assert main(["scan", "https://github.com/o/r", "--executor", "fake"]) == 2
    assert "--local" in capsys.readouterr().err


def test_scan_needs_a_url_or_a_local_path(monkeypatch, capsys):
    forbid_deps(monkeypatch)
    assert main(["scan"]) == 2
    assert "--local" in capsys.readouterr().err


def test_local_path_must_be_a_directory(tmp_path, monkeypatch, capsys):
    forbid_deps(monkeypatch)
    assert main(["scan", "--executor", "fake", "--local", str(tmp_path / "missing")]) == 2
    assert "directory" in capsys.readouterr().err


def test_format_md_is_not_accepted_yet(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["scan", "https://github.com/o/r", "--format", "md"])
    assert caught.value.code == 2


def test_ac31_missing_nebius_key_fails_naming_the_variable(monkeypatch, capsys):
    forbid_deps(monkeypatch)
    monkeypatch.delenv("NEBIUS_API_KEY")
    code = main(["scan", "https://github.com/octocat/hello-world"])
    err = capsys.readouterr().err
    assert code == 1
    assert "Missing required environment variable: NEBIUS_API_KEY" in err
    assert "cli-tavily-key" not in err and "cli-project" not in err


# ---- admission errors from GitHub intake (section 8 messages) ----------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RepoNotFound("gone"), "Repository not found or not public."),
        (
            RepoTooLarge(50, 5000, 61.5, 7000),
            messages.repo_too_large(max_mb=50, max_files=5000, mb=61.5, files=7000),
        ),
        (NotPythonProject("no .py files"), "Not a Python project: no .py files."),
        (GitHubUnavailable("503"), "GitHub did not respond; try again later."),
    ],
)
def test_admission_errors_exit_2_with_the_section_8_message(
    tmp_path, monkeypatch, capsys, error, expected
):
    h = use_harness(monkeypatch, tmp_path)

    async def refuse(coords, ref):
        raise error

    h.deps.validate = refuse
    code = main(["scan", "https://github.com/octocat/hello-world"])

    assert code == 2
    assert capsys.readouterr().err == expected + "\n"
    assert h.osv_transport.requests == [] and h.executor.calls == []  # nothing started


def test_a_spent_allowance_refuses_the_scan_like_a_paused_service(tmp_path, monkeypatch, capsys):
    h = use_harness(monkeypatch, tmp_path)
    state = h.ledger.state
    local = state.spenders["local"]
    drained = local.model_copy(
        update={
            "pools": {
                p: ps.model_copy(update={"spent_usd": local.allowance_usd[p]})
                for p, ps in local.pools.items()
            }
        }
    )
    h.ledger.state = state.model_copy(update={"spenders": {"local": drained}})
    repo = write_repo(tmp_path / "repo")

    code = main(["scan", "--executor", "fake", "--local", str(repo)])

    assert code == 2
    assert capsys.readouterr().err == messages.message("live_scans_paused") + "\n"


def test_an_unexpected_failure_building_the_report_prints_an_error_id(
    tmp_path, monkeypatch, capsys
):
    use_harness(monkeypatch, tmp_path)
    repo = write_repo(tmp_path / "repo")

    async def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr("pir.cli.run_scan", broken)
    code = main(["scan", "--executor", "fake", "--local", str(repo)])

    assert code == 1
    err = capsys.readouterr().err
    message_line = next(ln for ln in err.splitlines() if ln.startswith("Something went wrong"))
    assert message_line.startswith("Something went wrong (error id ")
    assert "bug" not in message_line


def test_local_commit_sha_is_stable_and_follows_the_content(tmp_path):
    repo = write_repo(tmp_path / "repo")
    first = local_commit_sha(repo)
    assert len(first) == 40 and first == local_commit_sha(repo)
    (repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    assert local_commit_sha(repo) != first


# ---- dependency wiring -------------------------------------------------------------------


def test_build_deps_wires_the_real_components_by_executor_setting(tmp_path):
    from pir.config import ConfigError, Settings
    from pir.deps import build_deps
    from pir.executor.contree import ContreeExecutor
    from pir.executor.fake import FakeExecutor

    keys = {
        "nebius_api_key": "k1",
        "nebius_project_id": "p",
        "tavily_api_key": "k2",
        "spend_ledger_path": str(tmp_path / "ledger.json"),
        "data_dir": str(tmp_path / "data"),
    }
    fake = build_deps(Settings(_env_file=None, executor="fake", **keys))
    assert isinstance(fake.executor, FakeExecutor) and fake.langsmith is None
    real = build_deps(Settings(_env_file=None, executor="sandbox", **keys))
    assert isinstance(real.executor, ContreeExecutor)
    assert real.llm.model == "nvidia/nemotron-3-super-120b-a12b"
    with pytest.raises(ConfigError, match="hf_dataset"):
        build_deps(Settings(_env_file=None, spend_ledger_backend="hf_dataset", **keys))
    with pytest.raises(ConfigError, match="TAVILY_API_KEY"):
        build_deps(Settings(_env_file=None, **{**keys, "tavily_api_key": None}))
