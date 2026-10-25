"""GitHub intake (ARCHITECTURE sections 7.3 and 12): URL parsing and repository validation.

`parse_repo_url` is pure and runs before any I/O. `validate_repo` talks only to `api.github.com`
through `pir.http.make_client`; user input fills only the `{owner}/{repo}` and ref path segments.
"""

import fnmatch
import re
from urllib.parse import urljoin, urlsplit

import httpx

from pir.config import Settings, load_settings
from pir.http import GITHUB_HOST, GITHUB_MAX_BYTES, MIB, make_client
from pir.models import SHA_RE, Strict
from pir.models.api import check_ref

MAX_URL_CHARS = 300
_URL_CHARS_RE = re.compile(r"[A-Za-z0-9._:/-]+")
_GITHUB_WEB_HOSTS = ("github.com", "www.github.com")
_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_MANIFEST_NAMES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements*.txt",
    "Pipfile",
    "Pipfile.lock",
    "uv.lock",
    "poetry.lock",
)
_MAX_REDIRECTS = 3


class InvalidRepoUrl(ValueError):
    """The URL (or ref) is not an accepted public GitHub repository reference."""


class RepoNotFound(Exception):
    """The repository or ref does not exist or is not public."""


class RepoTooLarge(Exception):
    """The tree is over the file or size cap, or GitHub truncated it."""

    def __init__(self, max_mb: int, max_files: int, mb: float, files: int) -> None:
        super().__init__(f"repository over {max_mb} MB or {max_files} files")
        self.max_mb = max_mb
        self.max_files = max_files
        self.mb = mb
        self.files = files

    @property
    def caps(self) -> dict[str, int]:
        return {"max_mb": self.max_mb, "max_files": self.max_files}

    @property
    def actual(self) -> dict[str, float | int]:
        return {"mb": self.mb, "files": self.files}


class NotPythonProject(Exception):
    """No `.py` files, or no manifest at the repository root. `detail` is the user message part."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class GitHubUnavailable(Exception):
    """Timeout, 5xx, 403/429 rate limit, unexpected response or a refused redirect."""


class RepoCoordinates(Strict):
    owner: str
    name: str
    ref: str | None = None  # from /tree/{ref}; already passed the ScanRequest ref rule


class ValidatedTarget(Strict):
    coords: RepoCoordinates
    default_branch: str
    commit_sha: str
    files_total: int
    bytes_total: int
    py_files: int
    manifests: list[str]


def parse_repo_url(url: str) -> RepoCoordinates:
    """Accept only https://github.com/{owner}/{repo}[.git][/] or .../tree/{ref}. No I/O."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        raise InvalidRepoUrl("url is empty or too long")
    if not _URL_CHARS_RE.fullmatch(url):
        raise InvalidRepoUrl("url has unexpected characters")
    scheme, sep, rest = url.partition("://")
    if not sep or scheme.lower() != "https":
        raise InvalidRepoUrl("url must use https")
    host, slash, path = rest.partition("/")
    if host.lower() not in _GITHUB_WEB_HOSTS or not slash:
        raise InvalidRepoUrl("url must be a github.com repository")
    segments = path.split("/")
    ref: str | None = None
    if len(segments) >= 3 and segments[2] == "tree":
        try:
            ref = check_ref("/".join(segments[3:]))
        except ValueError as exc:
            raise InvalidRepoUrl("ref is not valid") from exc
        segments = segments[:2]
    elif len(segments) == 3 and segments[2] == "":
        segments = segments[:2]
    if len(segments) != 2:
        raise InvalidRepoUrl("url must be exactly /owner/repo")
    owner, name = segments
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if not _OWNER_RE.fullmatch(owner):
        raise InvalidRepoUrl("owner is not valid")
    if not _NAME_RE.fullmatch(name) or name in (".", ".."):
        raise InvalidRepoUrl("repository name is not valid")
    return RepoCoordinates(owner=owner, name=name, ref=ref)


def _is_github_api_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() == GITHUB_HOST
        and port in (None, 443)
        and "@" not in parts.netloc
    )


async def _get_json(
    client: httpx.AsyncClient, url: str, *, unprocessable_is_missing: bool = False
) -> dict:
    """GET `url` as a JSON object. A 301 is followed manually, only within api.github.com."""
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:  # timeouts, connection errors, byte cap
            raise GitHubUnavailable(f"GitHub request failed: {type(exc).__name__}") from exc
        status = response.status_code
        if status == 301:
            target = urljoin(url, response.headers.get("location", ""))
            if not _is_github_api_url(target):
                raise GitHubUnavailable("GitHub redirected to a host that is not allowed")
            url = target
            continue
        if status == 200:
            try:
                data = response.json()
            except ValueError as exc:
                raise GitHubUnavailable("GitHub returned invalid JSON") from exc
            if not isinstance(data, dict):
                raise GitHubUnavailable("GitHub returned an unexpected response")
            return data
        if status == 404 or (unprocessable_is_missing and status == 422):
            raise RepoNotFound("repository or ref not found")
        raise GitHubUnavailable(f"GitHub returned HTTP {status}")
    raise GitHubUnavailable("too many GitHub redirects")


def _is_manifest(path: str) -> bool:
    return "/" not in path and any(fnmatch.fnmatchcase(path, pat) for pat in _MANIFEST_NAMES)


async def validate_repo(
    coords: RepoCoordinates,
    ref: str | None,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ValidatedTarget:
    """Resolve the ref to a commit and check the tree against the caps and the Python rule."""
    settings = settings or load_settings()
    chosen = ref if ref is not None else coords.ref
    if chosen is not None:
        try:
            chosen = check_ref(chosen)
        except ValueError as exc:
            raise InvalidRepoUrl("ref is not valid") from exc
    if not _OWNER_RE.fullmatch(coords.owner) or not _NAME_RE.fullmatch(coords.name):
        raise InvalidRepoUrl("owner or repository name is not valid")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ProveItReachable",
    }
    if settings.github_token is not None and settings.github_token.get_secret_value():
        headers["Authorization"] = f"Bearer {settings.github_token.get_secret_value()}"
    base = f"https://{GITHUB_HOST}/repos/{coords.owner}/{coords.name}"
    async with make_client(
        {GITHUB_HOST},
        GITHUB_MAX_BYTES,
        settings.timeout_github_s,
        headers=headers,
        transport=transport,
    ) as client:
        repo = await _get_json(client, base)
        default_branch = repo.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise GitHubUnavailable("GitHub returned an unexpected repository response")
        if repo.get("private") is True:
            raise RepoNotFound("repository is not public")
        commit = await _get_json(
            client, f"{base}/commits/{chosen or default_branch}", unprocessable_is_missing=True
        )
        sha = commit.get("sha")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise GitHubUnavailable("GitHub returned an unexpected commit response")
        tree = await _get_json(client, f"{base}/git/trees/{sha}?recursive=1")
    entries = tree.get("tree")
    if not isinstance(entries, list):
        raise GitHubUnavailable("GitHub returned an unexpected tree response")

    files_total = 0
    bytes_total = 0
    py_files = 0
    manifests: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            continue
        path, size = entry.get("path"), entry.get("size", 0)
        if not isinstance(path, str) or not isinstance(size, int) or isinstance(size, bool):
            raise GitHubUnavailable("GitHub returned an unexpected tree entry")
        files_total += 1
        bytes_total += max(size, 0)
        if path.endswith(".py"):
            py_files += 1
        if _is_manifest(path):
            manifests.append(path)

    if (
        tree.get("truncated") is True
        or files_total > settings.max_repo_files
        or bytes_total > settings.max_repo_mb * MIB
    ):
        raise RepoTooLarge(
            settings.max_repo_mb,
            settings.max_repo_files,
            round(bytes_total / MIB, 1),
            files_total,
        )
    if py_files == 0:
        raise NotPythonProject("no .py files")
    if not manifests:
        raise NotPythonProject("no dependency manifest at the repository root")
    return ValidatedTarget(
        coords=RepoCoordinates(owner=coords.owner, name=coords.name, ref=chosen),
        default_branch=default_branch,
        commit_sha=sha,
        files_total=files_total,
        bytes_total=bytes_total,
        py_files=py_files,
        manifests=sorted(manifests),
    )
