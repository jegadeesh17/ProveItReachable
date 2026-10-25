"""Agent wire protocol (ARCHITECTURE section 7.2). Standard library only.

The agent prints exactly one stdout line, `PIR-RESULT:<base64(gzip(JSON))>`; logs go to stderr.
Exit codes: 0 ok, 2 bad input, 3 limit exceeded, anything else is a failure.
"""

import argparse
import base64
import gzip
import json
import sys
import zlib
from collections.abc import Callable
from typing import Any, TextIO

PROTOCOL_VERSION = 1
RESULT_PREFIX = "PIR-RESULT:"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BAD_INPUT = 2
EXIT_LIMIT = 3

MAX_ARGS_B64_BYTES = 64 * 1024
MAX_RESULT_B64_BYTES = 10 * 1024 * 1024
MAX_RESULT_JSON_BYTES = 50 * 1024 * 1024


class AgentError(Exception):
    """A failure that maps to exit code 1 (the default for anything unexpected)."""

    exit_code = EXIT_FAILURE


class BadInput(AgentError):
    """Arguments or input the agent refuses (exit 2)."""

    exit_code = EXIT_BAD_INPUT


class LimitExceeded(AgentError):
    """A size, count or download cap was hit (exit 3)."""

    exit_code = EXIT_LIMIT


class UnsafeArchive(AgentError):
    """The tarball holds a member the extraction rules refuse (exit 1)."""


Handler = Callable[[dict[str, Any]], dict[str, Any]]


def log(message: str, stream: TextIO | None = None) -> None:
    """Write one diagnostic line to stderr (stdout is reserved for the result line)."""
    (stream or sys.stderr).write(f"pir-agent: {message}\n")


def parse_args_b64(value: str) -> dict[str, Any]:
    """Decode `--args-b64`: base64 of a JSON object. Raises BadInput on any malformed value."""
    if not isinstance(value, str) or not value or len(value) > MAX_ARGS_B64_BYTES:
        raise BadInput("--args-b64 is empty or too long")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise BadInput(f"--args-b64 is not base64 of UTF-8 JSON: {type(exc).__name__}") from exc
    if not isinstance(parsed, dict):
        raise BadInput("--args-b64 must decode to a JSON object")
    return parsed


def encode_result(payload: Any) -> str:
    """The single stdout line for a result. Deterministic: sorted keys, fixed gzip header time."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    packed = gzip.compress(raw, compresslevel=6, mtime=0)
    return RESULT_PREFIX + base64.b64encode(packed).decode("ascii")


def decode_result(line: str) -> Any:
    """Inverse of `encode_result`, with the host-side caps (10 MiB base64, 50 MiB decompressed)."""
    line = line.strip()
    if not line.startswith(RESULT_PREFIX):
        raise ValueError("not a result line")
    body = line[len(RESULT_PREFIX) :]
    if len(body) > MAX_RESULT_B64_BYTES:
        raise ValueError("result line over the base64 cap")
    decompressor = zlib.decompressobj(wbits=31)
    raw = decompressor.decompress(base64.b64decode(body, validate=True), MAX_RESULT_JSON_BYTES)
    if decompressor.unconsumed_tail:
        raise ValueError("result over the decompressed cap")
    return json.loads(raw.decode("utf-8"))


def emit_result(payload: Any, stdout: TextIO) -> None:
    line = encode_result(payload)
    stdout.write(line + "\n")
    stdout.flush()


def run_command(
    argv: list[str],
    handlers: dict[str, Handler],
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Parse `<command> --args-b64 <b64>`, run the handler, print the result line, return the code.

    Never raises: every failure becomes an exit code and a stderr line.
    """
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = argparse.ArgumentParser(prog="agent", add_help=False, exit_on_error=False)
    parser.add_argument("command")
    parser.add_argument("--args-b64", dest="args_b64", required=True)
    try:
        namespace = parser.parse_args(argv)
        if namespace.command not in handlers:
            raise BadInput(f"unknown command {namespace.command!r}")
        args = parse_args_b64(namespace.args_b64)
        result = handlers[namespace.command](args)
        emit_result(result, out)
    except argparse.ArgumentError as exc:
        log(f"bad arguments: {exc}", err)
        return EXIT_BAD_INPUT
    except SystemExit:
        log("bad arguments", err)
        return EXIT_BAD_INPUT
    except AgentError as exc:
        log(f"{type(exc).__name__}: {exc}", err)
        return exc.exit_code
    except Exception as exc:  # any other failure is exit 1 with the cause on stderr
        log(f"failure: {type(exc).__name__}: {exc}", err)
        return EXIT_FAILURE
    return EXIT_OK


def require_int(args: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BadInput(f"{key} must be an integer from {minimum} to {maximum}")
    return value


def require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise BadInput(f"{key} must be a non-empty string")
    return value
