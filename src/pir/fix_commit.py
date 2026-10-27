"""Fix-commit symbol extraction (ARCHITECTURE section 7.4 fallback).

When Tavily and LLM research identify no vulnerable symbols for an advisory, OSV fix-commit
references provide deterministic candidate symbols by inspecting the patch diff.
"""

import contextlib
import logging
import os
import re
from collections.abc import Iterable
from pathlib import Path

import httpx

from pir.config import load_settings
from pir.http import GITHUB_HOST, GITHUB_MAX_BYTES, make_client
from pir.models.report import AdvisoryLink

logger = logging.getLogger(__name__)

_MAX_SYMBOLS = 10
_SYMBOL_MAX = 200
_SYMBOL_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", re.ASCII)
_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_COMMIT_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})/commit/([0-9a-fA-F]{7,40})(?:\.diff|\.patch)?(?:[/?#]|$)"
)
_HUNK_HEADER_RE = re.compile(r"^@@\s+-[0-9]+(?:,[0-9]+)?\s+\+[0-9]+(?:,[0-9]+)?\s+@@(.*)$")
_CLASS_HEADING_RE = re.compile(r"\bclass\s+([A-Za-z_]\w*)")
_DEF_HEADING_RE = re.compile(r"\b(?:async\s+def|def)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
_CLASS_LINE_RE = re.compile(r"^[ +-]*(?:class)\s+([A-Za-z_]\w*)")
_DEF_LINE_RE = re.compile(r"^[ +-]*(?:async\s+def|def)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")

IMPORT_NAMES = {
    "pyyaml": "yaml",
    "pillow": "PIL",
    "scikit-learn": "sklearn",
    "beautifulsoup4": "bs4",
    "opencv-python": "cv2",
    "python-dateutil": "dateutil",
}


def import_root(package: str) -> str:
    """PEP 503 package name to its import root name."""
    normalized = re.sub(r"[-_.]+", "-", package).lower()
    return IMPORT_NAMES.get(normalized, normalized.replace("-", "_"))


def commit_refs(links: Iterable[AdvisoryLink | str]) -> list[tuple[str, str, str]]:
    """Extract unique (owner, repo, sha) triples from GitHub commit URLs in advisory links.

    Preserves order of appearance; deduplicates identical triples.
    """
    refs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in links:
        url = (
            item.url
            if isinstance(item, AdvisoryLink)
            else (item if isinstance(item, str) else None)
        )
        if not url:
            continue
        m = _COMMIT_URL_RE.match(url)
        if not m:
            continue
        owner, repo, sha = m.group(1), m.group(2), m.group(3).lower()
        if repo.endswith(".git"):
            repo = repo[:-4]
        if repo in (".", "..") or not _OWNER_RE.fullmatch(owner) or not _NAME_RE.fullmatch(repo):
            continue
        ref = (owner, repo, sha)
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def _is_test_file(path: str) -> bool:
    """Drop test files and fixtures so test helper changes are not reported as vulnerable."""
    normalized = path.replace("\\", "/").lower()
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return False
    filename = parts[-1]
    for segment in parts[:-1]:
        if segment in ("test", "tests", "testing"):
            return True
    if filename.startswith("test_") or filename.endswith("_test.py") or filename == "conftest.py":
        return True
    return False


def _compute_module_prefix(path: str, package_root: str = "") -> str:
    """Derive dotted module prefix from file path and package root."""
    normalized = path.replace("\\", "/").strip("/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return package_root or ""

    if parts[-1] == "__init__":
        parts = parts[:-1]

    if not parts:
        return package_root or ""

    if package_root:
        root_norm = package_root.replace("-", "_").lower()
        match_idx = -1
        for idx, part in enumerate(parts):
            if part.replace("-", "_").lower() == root_norm:
                match_idx = idx
                break
        if match_idx >= 0:
            parts = parts[match_idx:]
            parts[0] = package_root
        else:
            if parts and parts[0] in ("src", "lib"):
                parts = parts[1:]
            parts = [package_root] + parts
    else:
        if parts and parts[0] in ("src", "lib"):
            parts = parts[1:]

    return ".".join(parts)


def changed_functions(diff_text: str, package_root: str = "") -> list[str]:
    """Extract candidate vulnerable symbols from a git diff.

    Reads Python (.py) files only, drops test files, extracts symbols from hunk headers and
    def/class lines, and qualifies them with package_root.
    Returns up to 10 unique symbols.
    """
    if not isinstance(diff_text, str) or not diff_text.strip():
        return []

    symbols: list[str] = []

    def _record(module_prefix: str, cls_name: str | None, func_name: str | None) -> None:
        if func_name:
            if cls_name and "." not in func_name:
                target = f"{cls_name}.{func_name}"
            else:
                target = func_name
        elif cls_name:
            target = cls_name
        else:
            return

        if module_prefix:
            if target == module_prefix or target.startswith(f"{module_prefix}."):
                sym = target
            else:
                sym = f"{module_prefix}.{target}"
        else:
            sym = target

        if (
            len(symbols) < _MAX_SYMBOLS
            and sym not in symbols
            and _SYMBOL_RE.fullmatch(sym)
            and len(sym) <= _SYMBOL_MAX
        ):
            symbols.append(sym)

    current_file: str | None = None
    is_valid_py = False
    module_prefix = ""
    current_class: str | None = None
    current_func: str | None = None
    heading_class: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"^diff --git\s+a/(.+?)\s+b/(.+?)$", line)
            current_file = m.group(2) if m else None
            is_valid_py = bool(
                current_file and current_file.endswith(".py") and not _is_test_file(current_file)
            )
            module_prefix = (
                _compute_module_prefix(current_file, package_root)
                if is_valid_py and current_file
                else ""
            )
            current_class = None
            current_func = None
            heading_class = None
            continue
        elif line.startswith("+++ "):
            target = line[4:].strip()
            if target.startswith("b/"):
                target = target[2:]
            if target != "/dev/null":
                current_file = target
                is_valid_py = current_file.endswith(".py") and not _is_test_file(current_file)
                module_prefix = (
                    _compute_module_prefix(current_file, package_root) if is_valid_py else ""
                )
            continue
        elif line.startswith("--- "):
            if not current_file or current_file == "/dev/null":
                target = line[4:].strip()
                if target.startswith("a/"):
                    target = target[2:]
                if target != "/dev/null":
                    current_file = target
                    is_valid_py = current_file.endswith(".py") and not _is_test_file(current_file)
                    module_prefix = (
                        _compute_module_prefix(current_file, package_root) if is_valid_py else ""
                    )
            continue

        if not is_valid_py:
            continue

        if line.startswith("@@"):
            m = _HUNK_HEADER_RE.match(line)
            heading = m.group(1).strip() if m else ""
            cm = _CLASS_HEADING_RE.search(heading)
            heading_class = cm.group(1) if cm else None
            dm = _DEF_HEADING_RE.search(heading)
            heading_func = dm.group(1) if dm else None

            current_class = heading_class
            current_func = heading_func
            continue

        if m := _CLASS_LINE_RE.match(line):
            current_class = m.group(1)
            current_func = None
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                _record(module_prefix, current_class, None)
        elif m := _DEF_LINE_RE.match(line):
            func_name = m.group(1)
            code_part = line[1:]
            indent = len(code_part) - len(code_part.lstrip(" "))
            if indent == 0 and not heading_class:
                current_class = None
            current_func = func_name
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                _record(module_prefix, current_class, current_func)
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            if current_func or current_class:
                _record(module_prefix, current_class, current_func)

        if len(symbols) >= _MAX_SYMBOLS:
            break

    return symbols[:_MAX_SYMBOLS]


async def fetch_diff(
    client: httpx.AsyncClient | None,
    owner: str,
    repo: str,
    sha: str,
    cache_dir: Path | str | None = None,
    *,
    timeout_s: float | None = None,
    token: str | None = None,
) -> str:
    """Fetch the commit diff from the GitHub API, cached on disk per sha.

    Returns the diff text, or "" on any error (never raises).
    """
    if not _OWNER_RE.fullmatch(owner) or not _NAME_RE.fullmatch(repo) or repo in (".", ".."):
        return ""
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
        return ""

    if cache_dir is None:
        try:
            settings = load_settings()
            cache_dir = Path(settings.data_dir) / "cache" / "diffs"
        except Exception:
            cache_dir = Path("data/cache/diffs")

    cache_path = Path(cache_dir) / f"{sha.lower()}.diff"
    try:
        if cache_path.is_file():
            return cache_path.read_text(encoding="utf-8")
    except OSError:
        pass

    headers = {
        "Accept": "application/vnd.github.diff",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ProveItReachable",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://{GITHUB_HOST}/repos/{owner}/{repo}/commits/{sha}"
    text = ""
    try:
        if client is not None:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                text = response.text
        else:
            settings = load_settings()
            timeout = timeout_s or settings.timeout_github_s
            auth_token = token or (
                settings.github_token.get_secret_value() if settings.github_token else None
            )
            if auth_token and "Authorization" not in headers:
                headers["Authorization"] = f"Bearer {auth_token}"
            async with make_client({GITHUB_HOST}, GITHUB_MAX_BYTES, timeout, headers=headers) as c:
                response = await c.get(url)
                if response.status_code == 200:
                    text = response.text
    except Exception as exc:
        logger.warning(
            "failed to fetch commit diff for %s/%s/%s: %s",
            owner,
            repo,
            sha,
            exc,
            extra={"service": "fix_commit"},
        )
        return ""

    if text:
        tmp = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, cache_path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()

    return text
