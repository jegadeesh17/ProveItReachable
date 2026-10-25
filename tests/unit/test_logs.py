"""AC-73 to AC-75 (unit part): JSON lines, scan context, stage timer, redaction."""

import asyncio
import io
import json
import logging

import pytest

from pir import logs
from pir.config import Settings

SECRET = 'sk-live-ab"cd\\ef-0123456789'


@pytest.fixture(autouse=True)
def isolated_logging():
    logs.clear_secrets()
    token = logs.bind_scan(None)
    yield
    logs.scan_id_var.reset(token)
    logs.clear_secrets()


@pytest.fixture
def capture():
    """A logger writing JSON lines to a buffer through the production formatter and filter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logs.JsonFormatter())
    handler.addFilter(logs.ScanContextFilter())
    logger = logging.getLogger("pir.test_capture")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    stage_logger = logging.getLogger("pir.stage")
    stage_logger.handlers = [handler]
    stage_logger.propagate = False
    stage_logger.setLevel(logging.INFO)
    yield logger, stream
    logger.handlers = []
    stage_logger.handlers = []
    stage_logger.propagate = True


def lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_ac73_every_line_is_one_json_object_with_the_required_keys(capture):
    logger, stream = capture
    logger.info("plain message")
    logger.warning("with %s args", "two")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed", extra={"event": "scan_failed", "error_id": "e1"})
    records = lines(stream)
    assert len(records) == 3
    for record in records:
        for key in ("ts", "level", "logger", "event", "msg", "scan_id"):
            assert key in record
    assert records[0]["event"] == "log"
    assert records[0]["scan_id"] is None
    assert records[1]["msg"] == "with two args"
    assert records[2]["event"] == "scan_failed" and records[2]["error_id"] == "e1"
    assert "ValueError: boom" in records[2]["exc"]
    assert records[0]["ts"].endswith("+00:00")


def test_ac73_multiline_message_stays_on_one_line(capture):
    logger, stream = capture
    logger.info("first\nsecond")
    assert len(stream.getvalue().splitlines()) == 1
    assert lines(stream)[0]["msg"] == "first\nsecond"


def test_ac74_scan_id_is_carried_into_tasks_and_threads(capture):
    logger, stream = capture
    logs.bind_scan("scan-42")

    async def in_task():
        logger.info("from task")

    def in_thread():
        logger.info("from thread")

    async def run():
        await asyncio.gather(asyncio.create_task(in_task()), asyncio.to_thread(in_thread))

    asyncio.run(run())
    logger.info("from caller")
    records = lines(stream)
    assert [r["scan_id"] for r in records] == ["scan-42"] * 3
    assert {r["msg"] for r in records} == {"from task", "from thread", "from caller"}


async def test_ac74_stage_timer_logs_stage_end_ok(capture):
    _, stream = capture
    logs.bind_scan("scan-7")
    async with logs.stage_timer("fetch"):
        await asyncio.sleep(0)
    (record,) = lines(stream)
    assert record["event"] == "stage_end"
    assert record["stage"] == "fetch"
    assert record["outcome"] == "ok"
    assert isinstance(record["duration_ms"], int) and record["duration_ms"] >= 0
    assert record["scan_id"] == "scan-7"


@pytest.mark.parametrize(
    ("raised", "outcome"),
    [
        (RuntimeError("x"), "failed"),
        (TimeoutError(), "timeout"),
        (asyncio.TimeoutError(), "timeout"),
    ],
)
async def test_ac74_stage_timer_outcome_on_exception(capture, raised, outcome):
    _, stream = capture
    with pytest.raises(type(raised)):
        async with logs.stage_timer("resolve"):
            raise raised
    (record,) = lines(stream)
    assert record["outcome"] == outcome
    assert record["duration_ms"] >= 0


async def test_ac74_stage_timer_can_report_skipped(capture):
    _, stream = capture
    async with logs.stage_timer("research") as stage:
        stage.outcome = "skipped"
    assert lines(stream)[0]["outcome"] == "skipped"


def test_ac75_redact_replaces_raw_and_json_escaped_secrets():
    escaped = json.dumps(SECRET)[1:-1]
    assert escaped != SECRET
    text = f"raw {SECRET} and escaped {escaped} end"
    out = logs.redact(text, secrets=[SECRET])
    assert out == "raw [REDACTED] and escaped [REDACTED] end"


def test_ac75_redact_uses_registered_secrets_and_ignores_empty_ones():
    logs.register_secrets(["", "tok-registered-1"])
    assert logs.redact("a tok-registered-1 b") == "a [REDACTED] b"
    assert logs.redact("nothing here") == "nothing here"


def test_ac75_redact_removes_authorization_and_bearer_values():
    out = logs.redact("headers Authorization: Bearer abc.def-123 sent")
    assert "abc.def-123" not in out and "[REDACTED]" in out
    assert "abc" not in logs.redact("Authorization: Basic YWJjOmRlZg==").replace("[REDACTED]", "")
    assert "tok123" not in logs.redact("got Bearer tok123, retrying")
    assert "tok456" not in logs.redact("{'Authorization': 'Bearer tok456'}")
    assert "tok789" not in logs.redact('{"authorization": "Bearer tok789"}')
    assert "tok789" not in logs.redact('"{\\"Authorization\\": \\"Bearer tok789\\"}"')


def test_ac75_redacted_json_line_still_parses(capture):
    logger, stream = capture
    logs.register_secrets([SECRET])
    logger.info("key %s header Authorization: Bearer zzz999", SECRET)
    try:
        raise ValueError(f"leaked {SECRET}")
    except ValueError:
        logger.exception("failed")
    out = stream.getvalue()
    assert SECRET not in out and json.dumps(SECRET)[1:-1] not in out and "zzz999" not in out
    records = lines(stream)
    assert "[REDACTED]" in records[0]["msg"]
    assert "[REDACTED]" in records[1]["exc"]


def test_ac75_configure_logging_registers_settings_secrets_and_quiets_httpx(capsys):
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    settings = Settings(_env_file=None, nebius_api_key="sk-cfg-secret-777", log_level="debug")
    try:
        logs.configure_logging(settings)
        logs.configure_logging(settings)  # idempotent
        ours = [h for h in root.handlers if getattr(h, "_pir_json_handler", False)]
        assert len(ours) == 1
        assert root.level == logging.DEBUG
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        logging.getLogger("pir.cfg").info("token sk-cfg-secret-777 used")
    finally:
        root.handlers = before
        root.setLevel(level)
    err = capsys.readouterr().err
    assert "sk-cfg-secret-777" not in err
    record = json.loads(err.strip().splitlines()[-1])
    assert record["msg"] == "token [REDACTED] used"


def test_secrets_from_settings_reads_only_secret_fields():
    s = Settings(_env_file=None, hf_token="hf-1", github_token="gh-1", nebius_project_id="proj")
    assert sorted(logs.secrets_from_settings(s)) == ["gh-1", "hf-1"]
