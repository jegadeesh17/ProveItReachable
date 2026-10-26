"""Keepalive and spend alert (AC-77, ARCHITECTURE section 19.3).

Standard library only, and nothing is imported from `pir`: the keepalive workflow runs this
file as a script without installing the package. Exit codes: 0 when the health check passes
and every spend pool is at or above the threshold; 1 when a pool is below it (each such pool is
named); 2 when the health check failed.
"""

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

EXIT_OK = 0
EXIT_BUDGET_ALERT = 1
EXIT_HEALTH_FAILED = 2
DEFAULT_MIN_FRACTION = 0.25
TRIES = 3
RETRY_DELAY_S = 20.0
REQUEST_TIMEOUT_S = 30.0


def evaluate(
    status: int, body: bytes, min_fraction: float = DEFAULT_MIN_FRACTION
) -> tuple[int, str]:
    """Judge one health response; returns (exit code, message). Pure: no I/O."""
    if status != 200:
        return EXIT_HEALTH_FAILED, f"Health check failed: HTTP {status}"
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return EXIT_HEALTH_FAILED, "Health check failed: response is not valid JSON"
    if not isinstance(data, dict):
        return EXIT_HEALTH_FAILED, "Health check failed: response is not a JSON object"
    if data.get("status") != "ok":
        return EXIT_HEALTH_FAILED, "Health check failed: status is not ok"
    budget = data.get("budget_remaining")
    if not isinstance(budget, dict) or not budget:
        return EXIT_HEALTH_FAILED, "Health check failed: budget_remaining is null or missing"
    below: list[str] = []
    for pool in sorted(budget):
        value = budget[pool]
        is_number = isinstance(value, int | float) and not isinstance(value, bool)
        if not is_number or not 0 <= value <= 1:
            return (
                EXIT_HEALTH_FAILED,
                f"Health check failed: budget_remaining.{pool} is not a fraction",
            )
        if value < min_fraction:
            below.append(f"Budget alert: {pool} at {value:g} (below {min_fraction:g})")
    if below:
        return EXIT_BUDGET_ALERT, "\n".join(below)
    return EXIT_OK, f"OK: every spend pool is at or above {min_fraction:g}"


def is_https_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(parts.netloc)


def _get(url: str) -> tuple[int, bytes]:
    """One GET. Any HTTP answer returns (status, body); no answer raises an OSError."""
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_S) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def check(url: str, min_fraction: float = DEFAULT_MIN_FRACTION) -> tuple[int, str]:
    """Fetch the health URL, retrying a missing answer (a sleeping Space may be waking up)."""
    for attempt in range(1, TRIES + 1):
        try:
            status, body = _get(url)
        except (OSError, http.client.HTTPException) as exc:
            if attempt == TRIES:
                detail = f"no response after {TRIES} tries ({type(exc).__name__})"
                return EXIT_HEALTH_FAILED, f"Health check failed: {detail}"
            time.sleep(RETRY_DELAY_S)
            continue
        return evaluate(status, body, min_fraction)
    raise AssertionError("unreachable: the loop returns on every path")


def _fraction(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check /api/health and its spend pools.")
    parser.add_argument("health_url", help="https:// URL of the /api/health endpoint")
    parser.add_argument(
        "--min-fraction",
        type=_fraction,
        default=DEFAULT_MIN_FRACTION,
        help="alert when a pool's remaining fraction is below this (default 0.25)",
    )
    args = parser.parse_args(argv)
    if not is_https_url(args.health_url):
        sys.stderr.write("Health check failed: the URL must start with https://\n")
        return EXIT_HEALTH_FAILED
    code, message = check(args.health_url, args.min_fraction)
    stream = sys.stdout if code == EXIT_OK else sys.stderr
    stream.write(message + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
