"""Error codes, HTTP statuses and exact user-facing messages (ARCHITECTURE section 8).

The UI shows these messages verbatim. Templates take named fields only; callers never
interpolate user input into a template itself.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    status: int
    template: str


_SPECS = (
    ErrorSpec(
        "invalid_url",
        400,
        "Enter a public GitHub repository URL like https://github.com/owner/repo.",
    ),
    ErrorSpec("repo_not_found", 404, "Repository not found or not public."),
    ErrorSpec(
        "repo_too_large",
        413,
        "Repository is over the size limit of {max_mb} MB or {max_files} files "
        "(it has {mb} MB in {files} files).",
    ),
    ErrorSpec("not_python_project", 422, "Not a Python project: {detail}."),
    ErrorSpec(
        "rate_limited",
        429,
        "Rate limited: you can start another live scan in {minutes} min. Demo replays still work.",
    ),
    ErrorSpec(
        "server_busy",
        429,
        "The scanner is busy; try again in a minute. Demo replays still work.",
    ),
    ErrorSpec(
        "too_many_requests",
        429,
        "Too many requests right now; try again in {minutes} min.",
    ),
    ErrorSpec(
        "live_scans_paused",
        503,
        "Live scans are paused because the spend limit was reached. Demo replays still work.",
    ),
    ErrorSpec("github_unavailable", 502, "GitHub did not respond; try again later."),
    ErrorSpec("scan_not_found", 404, "That scan does not exist or has expired."),
    ErrorSpec(
        "report_not_ready",
        409,
        "The report is not ready yet; it appears when the scan finishes.",
    ),
    ErrorSpec("demo_not_found", 404, "That demo does not exist."),
    ErrorSpec("finding_not_found", 404, "That finding does not exist in this scan."),
    ErrorSpec("request_invalid", 422, "The request is not valid: {detail}."),
    ErrorSpec("internal_error", 500, "Something went wrong (error id {error_id})."),
)

ERRORS: dict[str, ErrorSpec] = {spec.code: spec for spec in _SPECS}


def status_for(code: str) -> int:
    """HTTP status for an error code. Raises KeyError for an unknown code."""
    return ERRORS[code].status


def message(code: str, **fields: object) -> str:
    """Format the message for `code`. Raises KeyError for an unknown code or a missing field."""
    return ERRORS[code].template.format(**fields)


def repo_too_large(*, max_mb: int, max_files: int, mb: float, files: int) -> str:
    return message("repo_too_large", max_mb=max_mb, max_files=max_files, mb=mb, files=files)


def not_python_project(detail: str) -> str:
    return message("not_python_project", detail=detail)


def rate_limited(minutes: int) -> str:
    return message("rate_limited", minutes=minutes)


def too_many_requests(minutes: int) -> str:
    return message("too_many_requests", minutes=minutes)


def request_invalid(detail: str) -> str:
    return message("request_invalid", detail=detail)


def internal_error(error_id: str) -> str:
    return message("internal_error", error_id=error_id)
