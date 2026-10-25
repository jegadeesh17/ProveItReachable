"""GitHub intake (AC-02 to AC-06): URL parsing, ref rules, repo -> commit -> tree validation."""

import httpx
import pytest
from fakes.http import RecordingTransport, fixture_response, json_response

from pir.config import Settings
from pir.github import (
    GitHubUnavailable,
    InvalidRepoUrl,
    NotPythonProject,
    RepoCoordinates,
    RepoNotFound,
    RepoTooLarge,
    parse_repo_url,
    validate_repo,
)

API = "https://api.github.com/repos/octo/sample"
SHA = "0123456789abcdef0123456789abcdef01234567"
COORDS = RepoCoordinates(owner="octo", name="sample")


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def _routes(tree: str = "tree_ok.json", **extra):
    routes = {
        f"GET {API}": fixture_response(200, "github", "repo.json"),
        f"GET {API}/commits/main": fixture_response(200, "github", "commit.json"),
        f"GET {API}/git/trees/{SHA}": fixture_response(200, "github", tree),
    }
    routes.update(extra)
    return RecordingTransport(routes)


# ---- parse_repo_url (AC-02, AC-06) -----------------------------------------------------


@pytest.mark.parametrize(
    ("url", "owner", "name", "ref"),
    [
        ("https://github.com/octo/sample", "octo", "sample", None),
        ("https://www.github.com/octo/sample", "octo", "sample", None),
        ("https://github.com/octo/sample/", "octo", "sample", None),
        ("https://github.com/octo/sample.git", "octo", "sample", None),
        ("https://GitHub.com/octo/sample", "octo", "sample", None),
        ("https://github.com/octo/my.repo_x-1", "octo", "my.repo_x-1", None),
        ("https://github.com/octo/sample/tree/main", "octo", "sample", "main"),
        ("https://github.com/octo/sample/tree/feature/x-1", "octo", "sample", "feature/x-1"),
        ("https://github.com/octo/sample/tree/v1.2.3", "octo", "sample", "v1.2.3"),
        ("https://github.com/octo/tree", "octo", "tree", None),
    ],
)
def test_ac02_accepted_forms(url, owner, name, ref):
    coords = parse_repo_url(url)
    assert (coords.owner, coords.name, coords.ref) == (owner, name, ref)


REJECTED = [
    "",
    "github.com/octo/sample",
    "http://github.com/octo/sample",
    "ftp://github.com/octo/sample",
    "https://github.com.evil.com/octo/sample",
    "https://evilgithub.com/octo/sample",
    "https://user@github.com/octo/sample",
    "https://user:pw@github.com/octo/sample",
    "https://github.com:8443/octo/sample",
    "https://github.com:443/octo/sample",
    "https://127.0.0.1/octo/sample",
    "https://[::1]/octo/sample",
    "https://localhost/octo/sample",
    "https://api.github.com/repos/octo/sample",
    "https://raw.githubusercontent.com/octo/sample",
    "https://github.com/octo/sample?tab=readme",
    "https://github.com/octo/sample#readme",
    "https://github.com/octo/sam%70le",
    "https://github.com/octo/sample/%2e%2e",
    "https://github.com/octo/sämple",
    "https://github.com/octo/sample/issues",
    "https://github.com/octo/sample/blob/main/x.py",
    "https://github.com/octo/sample/extra/segments",
    "https://github.com/octo",
    "https://github.com/",
    "https://github.com",
    "https://github.com//sample",
    "https://github.com/octo//sample",
    "https://github.com/octo/sample//",
    "https://github.com/octo/.",
    "https://github.com/octo/..",
    "https://github.com/octo/.git",
    "https://github.com/-octo/sample",
    "https://github.com/" + "a" * 40 + "/sample",
    "https://github.com/octo/" + "a" * 101,
    "https://github.com/octo/sample/" + "a" * 300,
    "https://github.com/octo/sample\\x",
    "https://github.com/octo/sam ple",
    "https://github.com/octo/sample\n",
    "https://github.com/octo/sample/tree",
    "https://github.com/octo/sample/tree/",
]


@pytest.mark.parametrize("url", REJECTED)
def test_ac02_ac06_rejected_urls_make_no_request(url):
    transport = _routes()
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url(url)
    assert transport.requests == []


def test_ac02_non_string_input_is_rejected():
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("ref", [".", "a/./b", "%2e%2e", "main/", "a/../b", "a//b", ".hidden"])
def test_ac06_bad_refs_rejected_in_url(ref):
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url(f"https://github.com/octo/sample/tree/{ref}")


def test_ac06_41_hex_ref_rejected_in_url_and_40_hex_accepted():
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url("https://github.com/octo/sample/tree/" + "a" * 41)
    assert parse_repo_url("https://github.com/octo/sample/tree/" + "a" * 40).ref == "a" * 40


@pytest.mark.parametrize("ref", [".", "a/./b", "%2e%2e", "main/", "a" * 41, "../x"])
async def test_ac06_bad_ref_rejected_before_any_io(ref):
    transport = _routes()
    with pytest.raises(InvalidRepoUrl):
        await validate_repo(COORDS, ref, settings=_settings(), transport=transport)
    assert transport.requests == []


# ---- validate_repo happy path and caps ------------------------------------------------


async def test_ac05_happy_path_reads_repo_commit_tree():
    transport = _routes()
    target = await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    assert transport.urls == [
        f"GET {API}",
        f"GET {API}/commits/main",
        f"GET {API}/git/trees/{SHA}?recursive=1",
    ]
    assert target.commit_sha == SHA
    assert target.default_branch == "main"
    assert target.files_total == 4  # blobs only: the tree and the submodule are not files
    assert target.bytes_total == 400 + 80 + 1200 + 800
    assert target.py_files == 2
    assert target.manifests == ["pyproject.toml", "requirements.txt"]
    assert target.coords.ref is None


async def test_ac06_explicit_ref_is_resolved_to_a_sha():
    transport = _routes(
        **{f"GET {API}/commits/feature/x": fixture_response(200, "github", "commit.json")}
    )
    target = await validate_repo(COORDS, "feature/x", settings=_settings(), transport=transport)
    assert f"GET {API}/commits/feature/x" in transport.urls
    assert target.coords.ref == "feature/x"
    assert target.commit_sha == SHA


async def test_ac06_ref_from_tree_url_is_used_when_no_ref_argument():
    coords = parse_repo_url("https://github.com/octo/sample/tree/dev")
    transport = _routes(
        **{f"GET {API}/commits/dev": fixture_response(200, "github", "commit.json")}
    )
    await validate_repo(coords, None, settings=_settings(), transport=transport)
    assert f"GET {API}/commits/dev" in transport.urls


async def test_ac03_missing_repo_is_repo_not_found():
    transport = RecordingTransport(
        {f"GET {API}": fixture_response(404, "github", "not_found.json")}
    )
    with pytest.raises(RepoNotFound):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    assert len(transport.requests) == 1


async def test_ac03_private_repo_is_repo_not_found():
    transport = RecordingTransport(
        {f"GET {API}": fixture_response(200, "github", "repo_private.json")}
    )
    with pytest.raises(RepoNotFound):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)


@pytest.mark.parametrize("status", [404, 422])
async def test_ac03_unknown_ref_is_repo_not_found(status):
    transport = _routes(**{f"GET {API}/commits/main": json_response(status, {"message": "x"})})
    with pytest.raises(RepoNotFound):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)


async def test_ac04_truncated_tree_is_too_large_with_caps_and_actuals():
    transport = _routes("tree_truncated.json")
    with pytest.raises(RepoTooLarge) as caught:
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    assert caught.value.caps == {"max_mb": 50, "max_files": 5000}
    assert caught.value.actual == {"mb": 0.0, "files": 2}


async def test_ac04_oversized_tree_is_too_large_with_caps_and_actuals():
    transport = _routes("tree_too_big.json")
    with pytest.raises(RepoTooLarge) as caught:
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    assert (caught.value.max_mb, caught.value.max_files) == (50, 5000)
    assert caught.value.mb == 60.0
    assert caught.value.files == 3


async def test_ac04_file_count_cap_uses_settings():
    transport = _routes()
    with pytest.raises(RepoTooLarge) as caught:
        await validate_repo(COORDS, None, settings=_settings(max_repo_files=3), transport=transport)
    assert (caught.value.max_files, caught.value.files) == (3, 4)


async def test_ac05_no_py_files_is_not_python_project():
    with pytest.raises(NotPythonProject) as caught:
        await validate_repo(
            COORDS, None, settings=_settings(), transport=_routes("tree_no_py.json")
        )
    assert caught.value.detail == "no .py files"


async def test_ac05_no_root_manifest_is_not_python_project():
    with pytest.raises(NotPythonProject) as caught:
        await validate_repo(
            COORDS, None, settings=_settings(), transport=_routes("tree_no_manifest.json")
        )
    assert caught.value.detail == "no dependency manifest at the repository root"


async def test_ac05_setup_py_only_project_is_accepted():
    target = await validate_repo(
        COORDS, None, settings=_settings(), transport=_routes("tree_setup_py_only.json")
    )
    assert target.manifests == ["setup.py"]
    assert target.py_files == 2


# ---- failures, redirects, token -------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 403, 429, 401])
async def test_ac03_github_errors_are_unavailable(status):
    transport = RecordingTransport({f"GET {API}": json_response(status, {"message": "x"})})
    with pytest.raises(GitHubUnavailable):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")], ids=["timeout", "connect"]
)
async def test_ac03_timeout_and_connection_errors_are_unavailable(error):
    transport = RecordingTransport({f"GET {API}": error})
    with pytest.raises(GitHubUnavailable):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)


async def test_ac03_rate_limited_tree_step_is_unavailable():
    transport = _routes(**{f"GET {API}/git/trees/{SHA}": json_response(403, {"message": "limit"})})
    with pytest.raises(GitHubUnavailable):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)


async def test_ac03_malformed_responses_are_unavailable():
    for payload in ({"private": False}, ["not", "an", "object"]):
        transport = RecordingTransport({f"GET {API}": json_response(200, payload)})
        with pytest.raises(GitHubUnavailable):
            await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    transport = _routes(**{f"GET {API}/commits/main": json_response(200, {"sha": "nothex"})})
    with pytest.raises(GitHubUnavailable):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    transport = RecordingTransport({f"GET {API}": httpx.Response(200, content=b"<html>")})
    with pytest.raises(GitHubUnavailable):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)


async def test_ac06_301_to_a_foreign_host_raises_without_following():
    transport = RecordingTransport(
        {f"GET {API}": httpx.Response(301, headers={"location": "https://evil.example.com/repos"})}
    )
    with pytest.raises(GitHubUnavailable):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    assert transport.urls == [f"GET {API}"]


@pytest.mark.parametrize(
    "location",
    [
        "http://api.github.com/repositories/1",
        "https://api.github.com.evil.com/repositories/1",
        "https://user@api.github.com/repositories/1",
        "https://api.github.com:8443/repositories/1",
        "https://github.com/octo/sample",
    ],
)
async def test_ac06_301_to_lookalike_hosts_is_refused(location):
    transport = RecordingTransport(
        {f"GET {API}": httpx.Response(301, headers={"location": location})}
    )
    with pytest.raises(GitHubUnavailable):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    assert len(transport.requests) == 1


async def test_ac06_301_within_api_github_com_is_followed():
    moved = "https://api.github.com/repositories/42"
    transport = _routes(
        **{
            f"GET {API}": httpx.Response(301, headers={"location": moved}),
            f"GET {moved}": fixture_response(200, "github", "repo.json"),
        }
    )
    target = await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    assert target.commit_sha == SHA
    assert transport.urls[:2] == [f"GET {API}", f"GET {moved}"]


async def test_ac06_endless_301_loop_is_bounded():
    moved = "https://api.github.com/repositories/42"
    transport = RecordingTransport(
        {
            f"GET {API}": httpx.Response(301, headers={"location": moved}),
            f"GET {moved}": httpx.Response(301, headers={"location": moved}),
        }
    )
    with pytest.raises(GitHubUnavailable):
        await validate_repo(COORDS, None, settings=_settings(), transport=transport)
    assert len(transport.requests) <= 5


async def test_ac06_token_is_sent_only_to_api_github_com():
    transport = _routes()
    await validate_repo(
        COORDS, None, settings=_settings(github_token="ghp_SENTINEL"), transport=transport
    )
    assert {r.url.host for r in transport.requests} == {"api.github.com"}
    assert all(r.headers["authorization"] == "Bearer ghp_SENTINEL" for r in transport.requests)


async def test_ac06_no_authorization_header_without_a_token():
    transport = _routes()
    await validate_repo(COORDS, None, settings=_settings(github_token=None), transport=transport)
    assert all("authorization" not in r.headers for r in transport.requests)
