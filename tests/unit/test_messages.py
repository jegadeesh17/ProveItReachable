"""Exact text of every error code in ARCHITECTURE section 8."""

import pytest

from pir import messages

EXPECTED = {
    "invalid_url": (
        400,
        "Enter a public GitHub repository URL like https://github.com/owner/repo.",
    ),
    "repo_not_found": (404, "Repository not found or not public."),
    "repo_too_large": (
        413,
        "Repository is over the size limit of {max_mb} MB or {max_files} files "
        "(it has {mb} MB in {files} files).",
    ),
    "not_python_project": (422, "Not a Python project: {detail}."),
    "rate_limited": (
        429,
        "Rate limited: you can start another live scan in {minutes} min. Demo replays still work.",
    ),
    "server_busy": (
        429,
        "The scanner is busy; try again in a minute. Demo replays still work.",
    ),
    "too_many_requests": (
        429,
        "Too many requests right now; try again in {minutes} min.",
    ),
    "live_scans_paused": (
        503,
        "Live scans are paused because the spend limit was reached. Demo replays still work.",
    ),
    "github_unavailable": (502, "GitHub did not respond; try again later."),
    "scan_not_found": (404, "That scan does not exist or has expired."),
    "report_not_ready": (
        409,
        "The report is not ready yet; it appears when the scan finishes.",
    ),
    "demo_not_found": (404, "That demo does not exist."),
    "finding_not_found": (404, "That finding does not exist in this scan."),
    "request_invalid": (422, "The request is not valid: {detail}."),
    "internal_error": (500, "Something went wrong (error id {error_id})."),
}


def test_every_architecture_section_8_code_is_present_and_nothing_else():
    assert set(messages.ERRORS) == set(EXPECTED)


@pytest.mark.parametrize("code", sorted(EXPECTED))
def test_code_status_and_template_are_exact(code):
    status, template = EXPECTED[code]
    assert messages.ERRORS[code].status == status
    assert messages.ERRORS[code].template == template
    assert messages.status_for(code) == status


def test_message_without_fields_is_the_template():
    assert messages.message("repo_not_found") == "Repository not found or not public."


def test_format_helpers_fill_named_fields():
    assert messages.repo_too_large(max_mb=50, max_files=5000, mb=61, files=7000) == (
        "Repository is over the size limit of 50 MB or 5000 files (it has 61 MB in 7000 files)."
    )
    assert messages.not_python_project("no .py files") == "Not a Python project: no .py files."
    assert messages.rate_limited(12) == (
        "Rate limited: you can start another live scan in 12 min. Demo replays still work."
    )
    assert messages.too_many_requests(3) == "Too many requests right now; try again in 3 min."
    assert messages.request_invalid("url") == "The request is not valid: url."
    assert messages.internal_error("abc123") == "Something went wrong (error id abc123)."


def test_message_does_not_reinterpret_braces_in_field_values():
    assert messages.request_invalid("{url}") == "The request is not valid: {url}."


def test_unknown_code_and_missing_field_raise():
    with pytest.raises(KeyError):
        messages.message("no_such_code")
    with pytest.raises(KeyError):
        messages.message("rate_limited")
    with pytest.raises(KeyError):
        messages.status_for("no_such_code")
