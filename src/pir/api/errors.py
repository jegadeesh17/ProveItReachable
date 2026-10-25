"""The `ErrorResponse` envelope, built only from the texts in `pir.messages` (ARCHITECTURE 8)."""

import math

from fastapi import Response

from pir import messages
from pir.github import (
    GitHubUnavailable,
    InvalidRepoUrl,
    NotPythonProject,
    RepoNotFound,
    RepoTooLarge,
)
from pir.models.api import ErrorBody, ErrorResponse

LIVE_SCANS_DISABLED_REASON = "Live scans are turned off on this server. Demo replays still work."


class ApiError(Exception):
    """A refusal with a section 8 code. `status` defaults to the code's table status."""

    def __init__(
        self,
        code: str,
        text: str | None = None,
        *,
        details: dict[str, str | int | float] | None = None,
        retry_after_s: int | None = None,
        status: int | None = None,
    ) -> None:
        self.code = code
        self.text = text if text is not None else messages.message(code)
        self.details = details or {}
        self.retry_after_s = retry_after_s
        self.status = status if status is not None else messages.status_for(code)
        super().__init__(code)


def minutes_for(seconds: int | None) -> int:
    """Whole minutes to wait, at least 1."""
    return max(1, math.ceil((seconds or 60) / 60))


def error_response(err: ApiError) -> Response:
    body = ErrorResponse(
        error=ErrorBody(
            code=err.code,  # type: ignore[arg-type]
            message=err.text,
            details=err.details,
            retry_after_s=err.retry_after_s,
        )
    )
    headers = {"Retry-After": str(err.retry_after_s)} if err.retry_after_s is not None else None
    return Response(
        content=body.model_dump_json(),
        status_code=err.status,
        media_type="application/json",
        headers=headers,
    )


def internal_error_response(error_id: str) -> Response:
    """500 with the error id and never a stack trace (AC-43)."""
    return error_response(ApiError("internal_error", messages.internal_error(error_id)))


def request_invalid(detail: str, *, status: int | None = None) -> ApiError:
    return ApiError("request_invalid", messages.request_invalid(detail), status=status)


def admission_error(exc: Exception) -> ApiError | None:
    """The refusal for a GitHub intake error, or None for any other exception."""
    if isinstance(exc, RepoNotFound):
        return ApiError("repo_not_found")
    if isinstance(exc, RepoTooLarge):
        return ApiError(
            "repo_too_large",
            messages.repo_too_large(
                max_mb=exc.max_mb, max_files=exc.max_files, mb=exc.mb, files=exc.files
            ),
            details={**exc.caps, **exc.actual},
        )
    if isinstance(exc, NotPythonProject):
        return ApiError("not_python_project", messages.not_python_project(exc.detail))
    if isinstance(exc, GitHubUnavailable):
        return ApiError("github_unavailable")
    if isinstance(exc, InvalidRepoUrl):
        return ApiError("invalid_url")
    return None
