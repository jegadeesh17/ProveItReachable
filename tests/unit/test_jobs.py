"""JobManager and Job (ARCHITECTURE 7.7): event log, scan context, eviction (finding 5)."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from fakes.deps import capture_json_logs, log_lines, make_harness, write_repo

from pir.jobs import JobManager, ScanNotFound
from pir.logs import scan_id_var
from pir.models.api import ProgressEvent
from pir.models.report import ScanReport
from pir.pipeline import run_scan

SCAN_ID = "a" * 32


def step_event(scan_id: str, seq: int, step="resolve", state="running", tools=None):
    return ProgressEvent(
        seq=seq,
        scan_id=scan_id,
        type="step",
        step=step,
        state=state,
        message=None,
        at=datetime(2026, 10, 26, 10, 0, seq, tzinfo=UTC),
        outcome=None,
        tools=tools or [],
    )


def done_event(scan_id: str, seq: int):
    return ProgressEvent(
        seq=seq,
        scan_id=scan_id,
        type="done",
        step=None,
        state=None,
        message=None,
        at=datetime(2026, 10, 26, 10, 0, seq, tzinfo=UTC),
        outcome="clean",
        tools=[],
    )


async def collect(stream) -> list[ProgressEvent]:
    return [event async for event in stream]


@pytest.fixture
def manager(frozen_clock) -> JobManager:
    return JobManager(frozen_clock, ttl_s=3600, max_jobs=2)


async def test_job_runs_binds_the_scan_id_first_and_completes(manager, report):
    seen: dict = {}

    async def runner(sink):
        seen["scan_id"] = scan_id_var.get()
        await sink.emit(step_event(SCAN_ID, 1))
        return report

    with capture_json_logs(logging.INFO) as buffer:
        job = manager.create("live", runner, scan_id=SCAN_ID)
        assert job.state == "queued" and manager.get(SCAN_ID) is job
        await job.task
        logging.getLogger("pir.test").info("after")

    assert seen["scan_id"] == SCAN_ID
    assert job.state == "completed" and job.report is report and job.finished_at is not None
    assert [e.seq for e in job.log] == [1]
    assert scan_id_var.get() is None  # the binding stayed inside the task
    assert all(line["scan_id"] in (None, SCAN_ID) for line in log_lines(buffer))


async def test_a_generated_scan_id_is_uuid_hex_and_a_duplicate_is_refused(manager, report):
    async def runner(sink):
        return report

    job = manager.create("replay", runner)
    assert len(job.scan_id) == 32 and job.mode == "replay"
    with pytest.raises(ValueError):
        manager.create("live", runner, scan_id=job.scan_id)
    await job.task


async def test_get_raises_scan_not_found(manager):
    with pytest.raises(ScanNotFound):
        manager.get("missing")


async def test_events_replay_after_seq_then_follow_live_events_until_finished(manager, report):
    release = asyncio.Event()

    async def runner(sink):
        await sink.emit(step_event(SCAN_ID, 1))
        await sink.emit(step_event(SCAN_ID, 2, state="done"))
        await release.wait()
        await sink.emit(done_event(SCAN_ID, 3))
        return report

    job = manager.create("live", runner, scan_id=SCAN_ID)
    for _ in range(5):
        await asyncio.sleep(0)
    reader = asyncio.create_task(collect(manager.events(SCAN_ID, after_seq=1)))
    await asyncio.sleep(0)
    assert not reader.done()  # still following the live job
    release.set()

    events = await asyncio.wait_for(reader, 2)

    assert [e.seq for e in events] == [2, 3]
    assert [e.seq for e in await collect(manager.events(SCAN_ID, 0))] == [1, 2, 3]
    assert [e.seq for e in await collect(manager.events(SCAN_ID, 3))] == []
    await job.task


async def test_a_runner_failure_ends_with_an_error_event_with_an_error_id(manager, caplog):
    async def runner(sink):
        await sink.emit(step_event(SCAN_ID, 1))
        raise RuntimeError("secret detail")

    job = manager.create("live", runner, scan_id=SCAN_ID)
    events = await asyncio.wait_for(collect(manager.events(SCAN_ID)), 2)

    assert job.state == "failed" and job.report is None
    last = events[-1]
    assert last.type == "error" and last.seq == 2
    assert last.message is not None and last.message.startswith("Something went wrong (error id ")
    assert "secret detail" not in last.message
    status = job.status()
    assert status.state == "failed" and status.outcome is None and status.finished_at is not None


async def test_status_folds_steps_from_events_and_uses_the_report_when_done(manager, report):
    release = asyncio.Event()

    async def runner(sink):
        await sink.emit(step_event(SCAN_ID, 1, "resolve", "running"))
        await sink.emit(step_event(SCAN_ID, 2, "resolve", "done", tools=["lockfile"]))
        await sink.emit(step_event(SCAN_ID, 3, "osv", "running"))
        await release.wait()
        return report

    job = manager.create("live", runner, scan_id=SCAN_ID)
    for _ in range(5):
        await asyncio.sleep(0)
    steps = {s.name: s for s in job.status().steps}

    assert job.status().state == "running"
    assert steps["resolve"].state == "done" and steps["resolve"].tools == ["lockfile"]
    assert steps["resolve"].started_at is not None and steps["resolve"].finished_at is not None
    assert steps["osv"].state == "running" and steps["osv"].finished_at is None
    assert steps["report"].state == "pending"
    release.set()
    await job.task
    assert job.status().steps == report.steps and job.status().outcome == report.outcome


# ---- eviction ------------------------------------------------------------------------------


async def test_running_jobs_are_never_evicted_but_finished_ones_beyond_max_jobs_are(
    manager, frozen_clock, report
):
    gate = asyncio.Event()

    async def blocked(sink):
        await gate.wait()
        return report

    async def quick(sink):
        return report

    running = [manager.create("live", blocked, scan_id=f"{i:032x}") for i in range(3)]
    finished = []
    for i in range(3, 7):
        job = manager.create("live", quick, scan_id=f"{i:032x}")
        await job.task
        frozen_clock.advance(1)
        finished.append(job)

    manager.get(running[0].scan_id)  # any access triggers eviction

    assert all(manager.get(j.scan_id) for j in running)  # all three still there
    for gone in finished[:2]:
        with pytest.raises(ScanNotFound):
            manager.get(gone.scan_id)
    for kept in finished[2:]:
        assert manager.get(kept.scan_id) is kept  # the 2 newest finished
    assert manager.running() == 3
    gate.set()
    await asyncio.gather(*(j.task for j in running))


async def test_finished_jobs_expire_after_the_ttl_and_running_ones_do_not(
    manager, frozen_clock, report
):
    gate = asyncio.Event()

    async def blocked(sink):
        await gate.wait()
        return report

    async def quick(sink):
        return report

    slow = manager.create("live", blocked, scan_id="b" * 32)
    fast = manager.create("live", quick, scan_id="c" * 32)
    await fast.task

    frozen_clock.advance(3599)
    assert manager.get(fast.scan_id) is fast
    frozen_clock.advance(2)

    with pytest.raises(ScanNotFound):
        manager.get(fast.scan_id)
    frozen_clock.advance(10_000)
    assert manager.get(slow.scan_id) is slow  # still running after any amount of time
    gate.set()
    await slow.task


async def test_shutdown_cancels_unfinished_jobs(manager):
    async def forever(sink):
        await asyncio.Event().wait()

    job = manager.create("live", forever, scan_id=SCAN_ID)
    await asyncio.sleep(0)

    await manager.shutdown()

    assert job.task is not None and job.task.done()
    assert job.state == "failed"
    assert job.log[-1].type == "error"


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> ScanReport:
    """A valid report from the real pipeline on fakes, built once for the module."""
    base = tmp_path_factory.mktemp("jobs_report")

    async def build() -> ScanReport:
        h = make_harness(base / "h")
        repo = write_repo(base / "repo")
        return await run_scan(h.deps, h.target(repo), await h.reserve(), scan_id=SCAN_ID)

    return asyncio.run(build())
