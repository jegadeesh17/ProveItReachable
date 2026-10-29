"""The replay store, pacing, recorder and the `record` command.

Covers AC-46 (store), AC-48 (fidelity), AC-49 (pacing), the demo slug allowlist (finding 5) and the
rule that `pir.replay` imports nothing from the pipeline or a client module.
"""

import ast
import builtins
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fakes.deps import SHA, make_harness, write_repo
from fakes.langsmith import ShareableLangSmithClient

from pir.cli import main
from pir.github import RepoCoordinates, ValidatedTarget
from pir.models.api import DemoSummary
from pir.models.recording import RecordedEvent, ScanRecording
from pir.models.report import CallPathStep, Proof
from pir.replay import (
    MIN_GAP_S,
    DemoNotFound,
    Recorder,
    ReplayStore,
    replay_schedule,
    write_recording,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "replays"
DEMOS = ROOT / "demo" / "replays"
SLUG = "fixture-yaml"


def load_fixture() -> ScanRecording:
    return ScanRecording.model_validate_json((FIXTURES / f"{SLUG}.json").read_text("utf-8"))


def renamed(rec: ScanRecording, slug: str) -> ScanRecording:
    return rec.model_copy(update={"slug": slug, "title": f"Title {slug}"})


def stretched(rec: ScanRecording, total_ms: int) -> ScanRecording:
    """The same events spread evenly over `total_ms`."""
    n = len(rec.events)
    events = [
        RecordedEvent(offset_ms=round(total_ms * i / (n - 1)), event=e.event)
        for i, e in enumerate(rec.events)
    ]
    return rec.model_copy(update={"events": events})


def put(directory: Path, rec: ScanRecording) -> None:
    (directory / f"{rec.slug}.json").write_text(rec.model_dump_json(indent=2), encoding="utf-8")


class ListSink:
    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)


# ---- store: loading, listing, validation (AC-46) -------------------------------------------


def test_ac46_fixture_loads_and_lists_as_demo_summary():
    store = ReplayStore(FIXTURES)

    [demo] = store.list()

    rec = load_fixture()
    assert isinstance(demo, DemoSummary)
    assert demo.slug == SLUG
    assert demo.repo_url == rec.report.repo.url
    assert demo.commit_sha == rec.report.repo.commit_sha
    assert demo.recorded_at == rec.recorded_at
    assert demo.by_verdict == rec.report.summary.by_verdict
    assert store.get(SLUG) == rec


def test_ac46_list_follows_index_order_then_unindexed_sorted(tmp_path):
    rec = load_fixture()
    for slug in ("bravo", "alpha", "charlie", "delta"):
        put(tmp_path, renamed(rec, slug))
    (tmp_path / "index.json").write_text('["charlie", "ghost", "alpha", "alpha"]', "utf-8")

    store = ReplayStore(tmp_path)

    assert [d.slug for d in store.list()] == ["charlie", "alpha", "bravo", "delta"]


def test_invalid_recordings_are_skipped_and_logged(tmp_path, caplog):
    rec = load_fixture()
    put(tmp_path, renamed(rec, "good"))
    (tmp_path / "broken.json").write_text("{not json", "utf-8")
    (tmp_path / "wrong-shape.json").write_text('{"slug": "wrong-shape"}', "utf-8")
    put(tmp_path, renamed(rec, "other-name").model_copy())  # file name matches slug: fine
    (tmp_path / "mismatch.json").write_text(
        renamed(rec, "not-mismatch").model_dump_json(), encoding="utf-8"
    )

    with caplog.at_level(logging.ERROR, logger="pir.replay"):
        store = ReplayStore(tmp_path)

    assert [d.slug for d in store.list()] == ["good", "other-name"]
    skipped = [r.getMessage() for r in caplog.records if "skipping invalid" in r.getMessage()]
    assert len(skipped) == 3
    assert any("broken.json" in m for m in skipped)


def test_missing_directory_and_bad_index_give_a_usable_store(tmp_path):
    assert ReplayStore(tmp_path / "nowhere").list() == []
    put(tmp_path, renamed(load_fixture(), "only"))
    (tmp_path / "index.json").write_text('{"not": "a list"}', "utf-8")
    assert [d.slug for d in ReplayStore(tmp_path).list()] == ["only"]


# ---- get: the slug allowlist (finding 5) ---------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "../x",
        "..",
        "../../etc/passwd",
        "a/b",
        "a\\b",
        "Fixture-Yaml",
        "fixture_yaml",
        "fixture-yaml\n",
        "",
        "x" * 41,
        "fixture-yaml.json",
        "fixture yaml",
        "nonexistent",
        "ünïcode",
    ],
)
def test_get_rejects_non_allowlisted_slugs_without_touching_the_filesystem(slug, monkeypatch):
    store = ReplayStore(FIXTURES)

    def forbidden(*_a, **_k):
        raise AssertionError("filesystem access during get")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)

    with pytest.raises(DemoNotFound):
        store.get(slug)
    assert store.get(SLUG).slug == SLUG  # a loaded slug still works with no file access


# ---- pacing (AC-49) ------------------------------------------------------------------------


def test_ac49_schedule_scales_long_recordings_to_the_cap():
    rec = stretched(load_fixture(), 600_000)  # a ten minute scan

    times = replay_schedule(rec, 20)

    assert len(times) == len(rec.events)
    assert times == sorted(times)
    assert times[-1] == pytest.approx(20)
    assert all(b - a >= MIN_GAP_S - 1e-9 for a, b in zip(times, times[1:], strict=False))


def test_ac49_longest_recording_replays_well_under_180_seconds():
    longest = stretched(load_fixture(), 24 * 3600 * 1000)  # a day, far past any real scan

    assert replay_schedule(longest, 20)[-1] < 180
    assert replay_schedule(longest, 20)[-1] == pytest.approx(20)


def test_short_recordings_are_not_slowed_and_gaps_are_at_least_300_ms():
    rec = load_fixture()
    tight = rec.model_copy(
        update={
            "events": [
                RecordedEvent(offset_ms=e.offset_ms // 1000, event=e.event) for e in rec.events
            ]
        }
    )

    times = replay_schedule(tight, 20)

    assert all(b - a >= 0.3 - 1e-9 for a, b in zip(times, times[1:], strict=False))
    assert times[0] == pytest.approx(tight.events[0].offset_ms / 1000)


def test_schedule_handles_empty_and_zero_duration():
    rec = load_fixture()
    assert replay_schedule(rec.model_copy(update={"events": []}), 20) == []
    flat = rec.model_copy(
        update={"events": [RecordedEvent(offset_ms=0, event=e.event) for e in rec.events]}
    )
    assert replay_schedule(flat, 20) == pytest.approx(
        [i * MIN_GAP_S for i in range(len(rec.events))]
    )


async def test_ac48_play_emits_recorded_events_in_order_and_returns_the_report():
    rec = stretched(load_fixture(), 100_000)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    sink = ListSink()
    report = await ReplayStore(FIXTURES, sleep=fake_sleep).play(rec, sink, max_seconds=20)

    assert report == rec.report
    assert report.model_dump_json() == rec.report.model_dump_json()
    assert sink.events == [e.event for e in rec.events]
    assert sum(sleeps) == pytest.approx(replay_schedule(rec, 20)[-1])
    assert all(s > 0 for s in sleeps)


async def test_play_with_an_empty_recording_returns_the_report():
    rec = load_fixture().model_copy(update={"events": []})
    sink = ListSink()
    assert await ReplayStore(FIXTURES).play(rec, sink, max_seconds=20) == rec.report
    assert sink.events == []


# ---- recorder ------------------------------------------------------------------------------


async def test_recorder_forwards_and_records_offsets_and_builds_a_valid_recording():
    base = load_fixture()
    ticks = iter([10.0, 10.0, 10.5, 12.25])
    inner = ListSink()
    recorder = Recorder(inner, clock=lambda: next(ticks))
    first, second, third = (e.event for e in base.events[:3])

    for event in (first, second, third):
        await recorder.emit(event)

    assert inner.events == [first, second, third]
    assert [e.offset_ms for e in recorder.events] == [0, 500, 2250]
    when = datetime(2026, 10, 26, tzinfo=UTC)
    rec = recorder.to_recording("my-demo", "Title", "Description", base.report, recorded_at=when)
    assert ScanRecording.model_validate_json(rec.model_dump_json()) == rec
    assert (rec.slug, rec.model, rec.recorded_at) == ("my-demo", base.report.model, when)
    assert [e.event for e in rec.events] == [first, second, third]


async def test_recorder_without_an_inner_sink_and_a_bad_slug():
    base = load_fixture()
    recorder = Recorder(clock=iter([0.0, 1.0]).__next__)
    await recorder.emit(base.events[0].event)
    assert len(recorder.events) == 1
    with pytest.raises(ValueError):
        recorder.to_recording("Bad Slug!", "t", "d", base.report)


def test_write_recording_writes_file_and_appends_to_the_index(tmp_path):
    rec = load_fixture()
    directory = tmp_path / "replays"

    write_recording(directory, renamed(rec, "first"))
    write_recording(directory, renamed(rec, "second"))
    write_recording(directory, renamed(rec, "first"))  # re-recording keeps one index entry

    assert json.loads((directory / "index.json").read_text("utf-8")) == ["first", "second"]
    assert [d.slug for d in ReplayStore(directory).list()] == ["first", "second"]
    assert not list(directory.glob("*.tmp"))
    assert b"\r" not in (directory / "first.json").read_bytes()


def test_recording_with_proof_round_trips_through_write_recording_and_replay_store(tmp_path):
    base = load_fixture()
    proof = Proof(
        status="proven",
        reason="call reached yaml.full_load in sandbox",
        ran_at=datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC),
        entry="app.py:load_config",
        target="yaml.full_load",
        recorded_call="yaml.full_load(text)",
    )
    findings = [
        f.model_copy(update={"proof": proof}) if i == 0 else f
        for i, f in enumerate(base.report.findings)
    ]
    report_with_proof = base.report.model_copy(update={"findings": findings})
    rec_with_proof = base.model_copy(update={"slug": "proven-demo", "report": report_with_proof})

    directory = tmp_path / "replays"
    write_recording(directory, rec_with_proof)

    store = ReplayStore(directory)
    loaded = store.get("proven-demo")
    assert loaded.report.findings[0].proof == proof
    assert loaded == rec_with_proof


# ---- committed recordings and imports ------------------------------------------------------


def test_every_committed_recording_validates_against_scan_recording():
    paths = [p for p in DEMOS.glob("*.json") if p.name != "index.json"]
    paths += [p for p in FIXTURES.glob("*.json") if p.name != "index.json"]
    assert paths, "the fake-built fixture recording is missing"
    for path in paths:
        rec = ScanRecording.model_validate_json(path.read_bytes())
        assert rec.slug == path.stem
        assert b"\x00" not in path.read_bytes()


def test_committed_indexes_list_only_existing_recordings():
    for directory in (DEMOS, FIXTURES):
        slugs = json.loads((directory / "index.json").read_text("utf-8"))
        assert isinstance(slugs, list) and len(slugs) == len(set(slugs))
        for slug in slugs:
            assert (directory / f"{slug}.json").is_file(), slug
        assert [d.slug for d in ReplayStore(directory).list()] == slugs


FORBIDDEN = (
    "pir.pipeline",
    "pir.http",
    "pir.llm",
    "pir.github",
    "pir.osv",
    "pir.research",
    "pir.agent_client",
    "pir.executor",
    "pir.reasoning",
    "pir.spend",
    "pir.deps",
    "pir.api",
    "pir.jobs",
)


def test_pir_replay_imports_nothing_from_the_pipeline_or_client_modules():
    tree = ast.parse((ROOT / "src" / "pir" / "replay.py").read_text("utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    pir_modules = {m for m in imported if m == "pir" or m.startswith("pir.")}
    assert pir_modules <= {"pir.models.api", "pir.models.recording", "pir.models.report"}
    assert not [m for m in imported if m.startswith(FORBIDDEN)]
    assert not imported & {"httpx", "requests", "openai", "tavily", "langsmith"}


@pytest.mark.slow
def test_importing_pir_replay_loads_no_pipeline_or_client_module():
    code = (
        "import sys, pir.replay\n"
        f"bad = [m for m in sys.modules if m.startswith({FORBIDDEN!r})]\n"
        "print(bad)\nsys.exit(1 if bad else 0)"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "SYSTEMROOT": "C:\\Windows"},
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


# ---- the record command (fakes only; no live scan) -----------------------------------------

VALIDATED = ValidatedTarget(
    coords=RepoCoordinates(owner="octo", name="demo"),
    default_branch="main",
    commit_sha=SHA,
    files_total=3,
    bytes_total=1000,
    py_files=1,
    manifests=["requirements.txt"],
)
RECORD_ARGS = ["--slug", "octo-demo", "--title", "Octo demo", "--description", "A demo."]


@pytest.fixture
def record_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "NEBIUS_API_KEY": "cli-nebius-key",
        "NEBIUS_PROJECT_ID": "cli-project",
        "TAVILY_API_KEY": "cli-tavily-key",
        "DATA_DIR": str(tmp_path / "data"),
        "SPEND_LEDGER_PATH": str(tmp_path / "ledger.json"),
        "REPLAY_DIR": str(tmp_path / "out"),
    }.items():
        monkeypatch.setenv(name, value)
    h = make_harness(tmp_path / "h")

    async def validate(coords, ref):
        return VALIDATED

    h.deps.validate = validate
    monkeypatch.setattr("pir.cli.build_deps", lambda settings: h.deps)
    repo = write_repo(tmp_path / "repo")

    from pir import cli

    real = cli.run_scan

    async def fixture_scan(deps, target, budget, **kwargs):
        return await real(deps, h.target(repo), budget, **kwargs)

    monkeypatch.setattr("pir.cli.run_scan", fixture_scan)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield h, tmp_path / "out"
    root.handlers[:] = handlers
    root.setLevel(level)


def test_record_writes_a_valid_recording_and_the_index(record_env, capsys):
    _h, out = record_env

    code = main(["record", "https://github.com/octo/demo", "--ref", SHA, *RECORD_ARGS])

    assert code == 0
    rec = ScanRecording.model_validate_json((out / "octo-demo.json").read_text("utf-8"))
    assert (rec.slug, rec.title, rec.description) == ("octo-demo", "Octo demo", "A demo.")
    assert [e.event.type for e in rec.events][-1] == "done"
    assert [e.event.seq for e in rec.events] == list(range(1, len(rec.events) + 1))
    assert [e.offset_ms for e in rec.events] == sorted(e.offset_ms for e in rec.events)
    assert rec.report.repo.commit_sha == SHA
    assert json.loads((out / "index.json").read_text("utf-8")) == ["octo-demo"]
    assert "spend nebius: used $" in capsys.readouterr().err  # spend is printed


def test_adr45_record_never_stores_a_public_trace_link(record_env):
    h, out = record_env
    fake = ShareableLangSmithClient()
    h.deps.langsmith = fake
    h.deps.settings = h.deps.settings.model_copy(
        update={"langsmith_tracing": True, "langsmith_share_traces": True}
    )

    code = main(["record", "https://github.com/octo/demo", "--ref", SHA, *RECORD_ARGS])

    assert code == 0
    assert fake.shared == []  # the run was never published
    rec = ScanRecording.model_validate_json((out / "octo-demo.json").read_text("utf-8"))
    assert rec.report.usage is not None
    assert rec.report.usage.trace_url is None
    assert '"trace_url":null' in (out / "octo-demo.json").read_text("utf-8").replace(" ", "")


def test_record_with_proof_sets_proof_on_findings(record_env, monkeypatch, capsys):
    _h, out = record_env
    from pir import cli

    real_run_scan = cli.run_scan

    async def scan_with_call_path(*args, **kwargs):
        report = await real_run_scan(*args, **kwargs)
        findings = [
            f.model_copy(
                update={
                    "verdict": "likely reachable",
                    "reason_code": None,
                    "reason": None,
                    "call_path": [
                        CallPathStep(
                            file="app.py", line=4, symbol="load_config", edge="heuristic_call"
                        ),
                        CallPathStep(
                            file="app.py", line=5, symbol="yaml.full_load", edge="vulnerable_call"
                        ),
                    ],
                }
            )
            for f in report.findings
        ]
        by_verdict = {
            "likely reachable": sum(1 for f in findings if f.verdict == "likely reachable"),
            "no reachable path detected": sum(
                1 for f in findings if f.verdict == "no reachable path detected"
            ),
            "uncertain": sum(1 for f in findings if f.verdict == "uncertain"),
        }
        summary = report.summary.model_copy(update={"by_verdict": by_verdict})
        return report.model_copy(update={"findings": findings, "summary": summary})

    monkeypatch.setattr("pir.cli.run_scan", scan_with_call_path)

    expected_proof = Proof(
        status="proven",
        reason="call reached yaml.full_load in sandbox",
        ran_at=datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC),
        entry="load_config",
        target="yaml.full_load",
        recorded_call="yaml.full_load(text)",
    )

    async def fake_run_proof(deps, target, budget, report, finding, entry):
        assert entry == "load_config"
        return expected_proof

    monkeypatch.setattr("pir.cli.run_proof", fake_run_proof)

    code = main(["record", "https://github.com/octo/demo", "--ref", SHA, "--proof", *RECORD_ARGS])

    assert code == 0
    rec = ScanRecording.model_validate_json((out / "octo-demo.json").read_text("utf-8"))
    assert len(rec.report.findings) > 0
    assert rec.report.findings[0].proof == expected_proof
    err = capsys.readouterr().err
    assert "Proof for " in err
    assert "proven" in err


def test_record_refuses_a_scan_the_sandbox_did_not_serve(record_env, monkeypatch, capsys):
    _h, out = record_env
    from pir import cli

    real = cli.run_scan

    async def selfcheck_failed(*args, **kwargs):
        report = await real(*args, **kwargs)
        steps = [
            s.model_copy(update={"state": "failed"}) if s.name == "resolve" else s
            for s in report.steps
        ]
        return report.model_copy(update={"steps": steps})

    monkeypatch.setattr("pir.cli.run_scan", selfcheck_failed)

    code = main(["record", "https://github.com/octo/demo", *RECORD_ARGS])

    assert code == 1
    assert "resolve step did not finish" in capsys.readouterr().err
    assert not out.exists() or not list(out.iterdir())


@pytest.mark.parametrize(
    "argv",
    [
        [
            "record",
            "https://github.com/octo/demo",
            "--slug",
            "Bad_Slug",
            "--title",
            "t",
            "--description",
            "d",
        ],
        [
            "record",
            "https://github.com/octo/demo",
            "--slug",
            "x" * 41,
            "--title",
            "t",
            "--description",
            "d",
        ],
        [
            "record",
            "https://github.com/octo/demo",
            "--slug",
            "ok",
            "--title",
            " ",
            "--description",
            "d",
        ],
        ["record", "https://example.com/octo/demo", *RECORD_ARGS],
        ["record", "https://github.com/octo/demo", "--ref", "a//b", *RECORD_ARGS],
    ],
)
def test_record_rejects_bad_arguments_before_any_scan(record_env, monkeypatch, argv):
    def refuse(settings):  # pragma: no cover - must not be reached
        raise AssertionError("build_deps was called")

    monkeypatch.setattr("pir.cli.build_deps", refuse)

    assert main(argv) == 2
