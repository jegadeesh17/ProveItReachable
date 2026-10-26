"""`fetch` and `unpack`: download a pinned GitHub tarball and extract it safely (ARCHITECTURE 7.2).

Standard library only. The repo is only ever data here: nothing from it is imported or run.
Extraction uses `tarfile` with `filter="data"` plus explicit refusals (absolute paths, `..`,
devices, links leaving the destination) and caps on members, uncompressed bytes and the
decompressed stream.
"""

import fnmatch
import gzip
import os
import posixpath
import re
import stat
import tarfile
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from typing import IO, Any

from .protocol import AgentError, BadInput, LimitExceeded, UnsafeArchive, log, require_int

REPO_ROOT = "/work/repo"
TARBALL_PATH = "/opt/pir/src.tar.gz"
CODELOAD_HOST = "codeload.github.com"
PYPI_JSON_PREFIX = "https://pypi.org/pypi/"  # ADR-0041: proof-install's source-package download
PYPI_FILES_PREFIX = "https://files.pythonhosted.org/packages/"
CHUNK = 64 * 1024
CONNECT_TIMEOUT_S = 30
MAX_PATH_CHARS = 1024

OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
MANIFEST_PATTERNS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements*.txt",
    "Pipfile",
    "Pipfile.lock",
    "uv.lock",
    "poetry.lock",
)

Opener = Callable[[str], IO[bytes]]


def check_sha(sha: object) -> str:
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise BadInput("sha must be 40 lowercase hex characters")
    return sha


def build_url(owner: object, repo: object, sha: object) -> str:
    """The only URL the agent downloads, built from validated parts."""
    if not isinstance(owner, str) or not OWNER_RE.fullmatch(owner):
        raise BadInput("owner is not a valid GitHub owner name")
    if not isinstance(repo, str) or not NAME_RE.fullmatch(repo) or repo in (".", ".."):
        raise BadInput("repo is not a valid GitHub repository name")
    return f"https://{CODELOAD_HOST}/{owner}/{repo}/tar.gz/{check_sha(sha)}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AgentError(f"unexpected redirect ({code})")


def open_https(url: str) -> IO[bytes]:
    """Open `url` over HTTPS with no redirects. Only codeload URLs from `build_url` reach here."""
    if not url.startswith(f"https://{CODELOAD_HOST}/"):
        raise BadInput("refusing to fetch a URL outside codeload.github.com")
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler())
    try:
        return opener.open(url, timeout=CONNECT_TIMEOUT_S)  # scheme and host checked above
    except urllib.error.HTTPError as exc:
        raise AgentError(f"download failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AgentError(f"download failed: {exc.reason}") from exc


def open_pypi(url: str) -> IO[bytes]:
    """Open a PyPI JSON page or a `files.pythonhosted.org` file over HTTPS with no redirects.

    The same opener as `open_https` (30 s per request); only these two URL prefixes are allowed.
    """
    if not url.startswith((PYPI_JSON_PREFIX, PYPI_FILES_PREFIX)):
        raise BadInput("refusing to fetch a URL outside pypi.org and files.pythonhosted.org")
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler())
    try:
        return opener.open(url, timeout=CONNECT_TIMEOUT_S)  # scheme and host checked above
    except urllib.error.HTTPError as exc:
        raise AgentError(f"download failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AgentError(f"download failed: {exc.reason}") from exc


class CappedReader:
    """A read-only file object that raises LimitExceeded once more than `limit` bytes were read."""

    def __init__(self, raw: IO[bytes], limit: int, what: str) -> None:
        self._raw = raw
        self._limit = limit
        self._what = what
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = CHUNK
        data = self._raw.read(size)
        self.total += len(data)
        if self.total > self._limit:
            raise LimitExceeded(f"{self._what} over {self._limit} bytes")
        return data


def _is_manifest(name: str) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in MANIFEST_PATTERNS)


def _strip_member_name(name: str, strip: int) -> str | None:
    """Validate an archive path and drop `strip` leading components; None means "skip it"."""
    if not name or len(name) > MAX_PATH_CHARS or "\x00" in name or "\\" in name:
        raise UnsafeArchive("member name is empty, too long or has NUL or backslash")
    if name.startswith("/"):
        raise UnsafeArchive(f"absolute path in archive: {name!r}")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise UnsafeArchive(f"'..' in archive path: {name!r}")
    if len(parts) <= strip:
        return None
    return "/".join(parts[strip:])


def _check_link(member: tarfile.TarInfo, new_name: str) -> None:
    """A symlink or hardlink must stay inside the destination (in-tree links are allowed)."""
    target = member.linkname
    if not target or "\x00" in target or "\\" in target or target.startswith("/"):
        raise UnsafeArchive(f"link {member.name!r} has an absolute or malformed target")
    if member.issym():
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(new_name), target))
    else:  # hardlink targets are archive paths, with the same stripped prefix
        resolved = posixpath.normpath(target)
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise UnsafeArchive(f"link {member.name!r} leaves the destination")


def extract_stream(
    stream: IO[bytes], dest: str, max_bytes: int, max_files: int, strip: int = 1
) -> None:
    """Safely extract a gzip tarball read as a stream into `dest`.

    Raises UnsafeArchive for refused members and LimitExceeded for the caps: more than
    `max_files` files (or directories), declared sizes over `max_bytes`, or a decompressed
    stream over `max_bytes` plus header overhead (a decompression bomb).
    """
    stream_cap = max_bytes + 4096 * max_files + 1024 * 1024
    capped = CappedReader(
        gzip.GzipFile(fileobj=stream, mode="rb"), stream_cap, "decompressed archive"
    )
    os.makedirs(dest, exist_ok=True)
    files = dirs = declared = 0
    try:
        tar = tarfile.open(fileobj=capped, mode="r|")
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise AgentError(f"not a readable gzip tarball: {exc}") from exc
    try:
        for member in tar:
            new_name = _strip_member_name(member.name, strip)
            if new_name is None:
                if not member.isdir():
                    raise UnsafeArchive(f"member {member.name!r} is outside the top directory")
                continue
            if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
                raise UnsafeArchive(f"device, fifo or special member: {member.name!r}")
            if member.issym() or member.islnk():
                if member.islnk():
                    link_name = _strip_member_name(member.linkname, strip)
                    if link_name is None:
                        raise UnsafeArchive(f"hardlink {member.name!r} targets the top directory")
                    member.linkname = link_name
                _check_link(member, new_name)
            if member.isdir():
                dirs += 1
                if dirs > max_files:
                    raise LimitExceeded(f"more than {max_files} directories")
            else:
                files += 1
                if files > max_files:
                    raise LimitExceeded(f"more than {max_files} files")
                if member.isreg():
                    declared += member.size
                    if declared > max_bytes:
                        raise LimitExceeded(f"uncompressed size over {max_bytes} bytes")
            member.name = new_name
            try:
                tar.extract(member, path=dest, filter="data")
            except tarfile.FilterError as exc:
                raise UnsafeArchive(f"refused by the data filter: {exc}") from exc
    except (tarfile.TarError, EOFError, zlib.error) as exc:
        raise AgentError(f"corrupt tarball: {exc}") from exc
    finally:
        tar.close()


def summarize_tree(root: str, sha: str) -> dict[str, Any]:
    """`FetchResult{commit_sha, files_total, bytes_total, py_files, manifests}` for `root`."""
    files_total = bytes_total = py_files = 0
    manifests: list[str] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            path = os.path.join(current, filename)
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode):
                continue
            files_total += 1
            bytes_total += info.st_size
            if filename.endswith(".py"):
                py_files += 1
            if current == root and _is_manifest(filename):
                manifests.append(filename)
    return {
        "commit_sha": sha,
        "files_total": files_total,
        "bytes_total": bytes_total,
        "py_files": py_files,
        "manifests": sorted(manifests),
    }


def fetch(
    args: dict[str, Any], repo_root: str = REPO_ROOT, opener: Opener = open_https
) -> dict[str, Any]:
    """Download `{owner}/{repo}@{sha}` from codeload (byte-capped) into `repo_root`."""
    max_bytes = require_int(args, "max_bytes", 1, 1 << 31)
    max_files = require_int(args, "max_files", 1, 1_000_000)
    sha = check_sha(args.get("sha"))
    url = build_url(args.get("owner"), args.get("repo"), sha)
    log(f"fetching {url}")
    response = opener(url)
    try:
        download = CappedReader(response, max_bytes, "download")
        extract_stream(download, repo_root, max_bytes, max_files, strip=1)
    finally:
        response.close()
    return summarize_tree(repo_root, sha)


def unpack(
    args: dict[str, Any], repo_root: str = REPO_ROOT, tarball: str = TARBALL_PATH
) -> dict[str, Any]:
    """Extract the local fixture tarball (no network) into `repo_root`, same rules as `fetch`."""
    max_bytes = require_int(args, "max_bytes", 1, 1 << 31)
    max_files = require_int(args, "max_files", 1, 1_000_000)
    sha = check_sha(args.get("sha"))
    strip = require_int(args, "strip_components", 0, 1) if "strip_components" in args else 1
    try:
        handle = open(tarball, "rb")
    except OSError as exc:
        raise AgentError(f"cannot open tarball: {exc}") from exc
    try:
        extract_stream(
            CappedReader(handle, max_bytes, "tarball"), repo_root, max_bytes, max_files, strip
        )
    finally:
        handle.close()
    return summarize_tree(repo_root, sha)
