"""Demo replays (ARCHITECTURE sections 7.7 and 14, ADR-0016).

`ReplayStore` loads and validates every recording in a directory once, at construction, and serves
them from a dict. `Recorder` is an `EventSink` that records what a live scan emits. This module
imports nothing from the pipeline or any client module (a fast test checks it): a replay makes no
network call and touches no spend meter.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from pir.models.api import DemoSummary, ProgressEvent
from pir.models.recording import RecordedEvent, ScanRecording
from pir.models.report import ScanReport

logger = logging.getLogger("pir.replay")

INDEX_FILE = "index.json"
MIN_GAP_S = 0.3
_SLUG_RE = re.compile(r"[a-z0-9-]{1,40}")
_MAX_FILE_BYTES = 5_000_000

Sleep = Callable[[float], Awaitable[None]]


class EventSink(Protocol):
    async def emit(self, event: ProgressEvent) -> None: ...


class DemoNotFound(KeyError):
    """No loaded recording has that slug."""


def replay_schedule(rec: ScanRecording, max_seconds: float) -> list[float]:
    """Seconds after the start at which each recorded event is emitted.

    Offsets are scaled by `min(1, max_seconds / recorded duration)` and kept at least
    `MIN_GAP_S` apart, so the last one is the time the replay takes.
    """
    duration_s = max((e.offset_ms for e in rec.events), default=0) / 1000
    factor = 1.0 if duration_s <= 0 else min(1.0, max(0.0, max_seconds) / duration_s)
    times: list[float] = []
    for recorded in rec.events:
        at = recorded.offset_ms / 1000 * factor
        if times:
            at = max(at, times[-1] + MIN_GAP_S)
        times.append(at)
    return times


class ReplayStore:
    """Every valid `<slug>.json` of a directory, listed in `index.json` order."""

    def __init__(self, directory: Path, *, sleep: Sleep = asyncio.sleep) -> None:
        self._sleep = sleep
        self._recordings: dict[str, ScanRecording] = {}
        self._order: list[str] = []
        self._load(Path(directory))

    def _load(self, directory: Path) -> None:
        if not directory.is_dir():
            logger.warning("replay directory not found", extra={"event": "replay_dir_missing"})
            return
        for path in sorted(directory.glob("*.json")):
            if path.name == INDEX_FILE:
                continue
            rec = self._read(path)
            if rec is not None:
                self._recordings[rec.slug] = rec
        indexed = self._read_index(directory / INDEX_FILE)
        self._order = [s for s in indexed if s in self._recordings]
        for slug in indexed:
            if slug not in self._recordings:
                logger.warning("indexed demo has no valid recording: %s", slug)
        self._order += sorted(set(self._recordings) - set(self._order))

    @staticmethod
    def _read(path: Path) -> ScanRecording | None:
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                raise ValueError("file is too large")
            rec = ScanRecording.model_validate_json(path.read_bytes())
            if rec.slug != path.stem:
                raise ValueError("slug does not match the file name")
        except (OSError, ValueError, ValidationError) as exc:
            logger.error(
                "skipping invalid recording %s: %s",
                path.name,
                type(exc).__name__,
                extra={"event": "replay_invalid"},
            )
            return None
        return rec

    @staticmethod
    def _read_index(path: Path) -> list[str]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logger.error("unreadable demo index: %s", type(exc).__name__)
            return []
        if not isinstance(data, list) or not all(isinstance(s, str) for s in data):
            logger.error("demo index is not a list of slugs")
            return []
        return list(dict.fromkeys(data))

    def list(self) -> list[DemoSummary]:
        return [summary(self._recordings[slug]) for slug in self._order]

    def get(self, slug: str) -> ScanRecording:
        """A dict lookup after the allowlist check; never a path join, so no file access."""
        if not isinstance(slug, str) or _SLUG_RE.fullmatch(slug) is None:
            raise DemoNotFound(slug)
        try:
            return self._recordings[slug]
        except KeyError:
            raise DemoNotFound(slug) from None

    async def play(
        self,
        rec: ScanRecording,
        emit: EventSink,
        *,
        max_seconds: float,
        sleep: Sleep | None = None,
    ) -> ScanReport:
        """Emit the recorded events at the scaled pacing, then return the recorded report."""
        wait = sleep or self._sleep
        elapsed = 0.0
        for recorded, at in zip(rec.events, replay_schedule(rec, max_seconds), strict=True):
            if at > elapsed:
                await wait(at - elapsed)
                elapsed = at
            await emit.emit(recorded.event)
        return rec.report


def summary(rec: ScanRecording) -> DemoSummary:
    return DemoSummary(
        slug=rec.slug,
        title=rec.title,
        description=rec.description,
        repo_url=rec.report.repo.url,
        commit_sha=rec.report.repo.commit_sha,
        recorded_at=rec.recorded_at,
        by_verdict=dict(rec.report.summary.by_verdict),
    )


class Recorder:
    """An `EventSink` that keeps every event with its offset and passes it to the wrapped sink."""

    def __init__(
        self,
        inner: EventSink | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._clock = clock
        self._start = clock()
        self.events: list[RecordedEvent] = []

    async def emit(self, event: ProgressEvent) -> None:
        offset_ms = max(0, round((self._clock() - self._start) * 1000))
        self.events.append(RecordedEvent(offset_ms=offset_ms, event=event))
        if self._inner is not None:
            await self._inner.emit(event)

    def to_recording(
        self,
        slug: str,
        title: str,
        description: str,
        report: ScanReport,
        *,
        recorded_at: datetime | None = None,
    ) -> ScanRecording:
        return ScanRecording(
            schema_version="1.0",
            slug=slug,
            title=title,
            description=description,
            recorded_at=recorded_at or datetime.now(UTC),
            model=report.model,
            events=list(self.events),
            report=report,
        )


def write_recording(directory: Path, rec: ScanRecording) -> Path:
    """Write `<slug>.json` and add the slug to `index.json` (appended if new), each atomically.

    The recording is validated again from its own JSON first, so nothing invalid is committed.
    """
    text = rec.model_dump_json(indent=2) + "\n"
    ScanRecording.model_validate_json(text)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{rec.slug}.json"
    _atomic_write(target, text)
    index_path = directory / INDEX_FILE
    slugs = ReplayStore._read_index(index_path)
    if rec.slug not in slugs:
        slugs.append(rec.slug)
    _atomic_write(index_path, json.dumps(slugs, indent=2) + "\n")
    return target


def _atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)
