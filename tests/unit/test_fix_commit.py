"""Unit tests for fix-commit symbol extraction (src/pir/fix_commit.py)."""

from pathlib import Path

import httpx
import pytest

from pir.fix_commit import changed_functions, commit_refs, fetch_diff, import_root
from pir.http import GITHUB_HOST
from pir.models.report import AdvisoryLink

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "diffs" / "sample_fix.diff"


def test_commit_refs_parses_valid_github_commit_urls():
    links = [
        AdvisoryLink(
            url="https://github.com/yaml/pyyaml/commit/a001f2782501ad2d24986959f0239a354675f9dc",
            source="osv",
            title=None,
        ),
        "https://github.com/aio-libs/aiohttp/commit/be846822b4b3a5a54b6163e794b42dc06fba54a3#diff-1234",
        "https://github.com/org/repo.git/commit/1234567890abcdef1234567890abcdef12345678/",
        "https://github.com/org/repo/commit/ABCDEF1234567890abcdef1234567890abcdef12.patch",
    ]
    refs = commit_refs(links)
    assert refs == [
        ("yaml", "pyyaml", "a001f2782501ad2d24986959f0239a354675f9dc"),
        ("aio-libs", "aiohttp", "be846822b4b3a5a54b6163e794b42dc06fba54a3"),
        ("org", "repo", "1234567890abcdef1234567890abcdef12345678"),
        ("org", "repo", "abcdef1234567890abcdef1234567890abcdef12"),
    ]


def test_commit_refs_deduplicates_preserving_order():
    links = [
        "https://github.com/yaml/pyyaml/commit/a001f2782501ad2d24986959f0239a354675f9dc",
        "https://github.com/yaml/pyyaml/commit/a001f2782501ad2d24986959f0239a354675f9dc#diff-1",
        "https://github.com/aio-libs/aiohttp/commit/be846822b4b3a5a54b6163e794b42dc06fba54a3",
    ]
    refs = commit_refs(links)
    assert len(refs) == 2
    assert refs[0] == ("yaml", "pyyaml", "a001f2782501ad2d24986959f0239a354675f9dc")
    assert refs[1] == ("aio-libs", "aiohttp", "be846822b4b3a5a54b6163e794b42dc06fba54a3")


def test_commit_refs_drops_non_commit_urls_and_invalid_inputs():
    links = [
        "https://github.com/yaml/pyyaml/pull/386",
        "https://github.com/yaml/pyyaml/issues/420",
        "https://github.com/yaml/pyyaml/tree/main",
        "https://github.com/advisories/GHSA-8q59-q68h-6hv4",
        "https://gitlab.com/org/repo/commit/a001f2782501ad2d24986959f0239a354675f9dc",
        "https://github.com/yaml/pyyaml/commit/not-a-hex-sha",
        "https://github.com/yaml/pyyaml/commit/123",  # too short
        "",
        None,
    ]
    assert commit_refs(links) == []


def test_changed_functions_from_sample_fixture():
    diff_text = FIXTURE_PATH.read_text(encoding="utf-8")
    symbols = changed_functions(diff_text, package_root="aiohttp")
    assert symbols == [
        "aiohttp.web_protocol.RequestHandler.connection_made",
        "aiohttp.web_protocol.process_headers",
    ]


def test_changed_functions_drops_test_files():
    diff_text = """diff --git a/tests/test_foo.py b/tests/test_foo.py
--- a/tests/test_foo.py
+++ b/tests/test_foo.py
@@ -10,3 +10,4 @@ def test_vuln():
+    pass
diff --git a/pkg/test_utils.py b/pkg/test_utils.py
--- a/pkg/test_utils.py
+++ b/pkg/test_utils.py
@@ -5,3 +5,4 @@ def test_helper():
+    pass
diff --git a/pkg/sub/foo_test.py b/pkg/sub/foo_test.py
--- a/pkg/sub/foo_test.py
+++ b/pkg/sub/foo_test.py
@@ -1,3 +1,4 @@
+def helper(): pass
"""
    assert changed_functions(diff_text, package_root="pkg") == []


def test_changed_functions_qualification_and_layouts():
    # flat layout
    diff_flat = """diff --git a/loader.py b/loader.py
--- a/loader.py
+++ b/loader.py
@@ -50,4 +50,5 @@ def load(stream):
-    pass
+    return 1
"""
    assert changed_functions(diff_flat, package_root="yaml") == ["yaml.loader.load"]

    # __init__.py layout
    diff_init = """diff --git a/yaml/__init__.py b/yaml/__init__.py
--- a/yaml/__init__.py
+++ b/yaml/__init__.py
@@ -10,4 +10,5 @@ def load(stream):
-    pass
+    return 1
"""
    assert changed_functions(diff_init, package_root="yaml") == ["yaml.load"]

    # src/ layout
    diff_src = """diff --git a/src/aiohttp/client.py b/src/aiohttp/client.py
--- a/src/aiohttp/client.py
+++ b/src/aiohttp/client.py
@@ -20,4 +20,5 @@ async def request(url):
-    pass
+    return None
"""
    assert changed_functions(diff_src, package_root="aiohttp") == ["aiohttp.client.request"]


def test_changed_functions_hunk_header_and_body_variations():
    diff_text = """diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -10,4 +10,4 @@ async def async_func(param):
-    old()
+    new()
@@ -30,5 +30,5 @@ class Service:
+    def handler(self):
+        return True
@@ -60,4 +60,5 @@
+def newly_added():
+    return 42
"""
    symbols = changed_functions(diff_text, package_root="pkg")
    assert symbols == [
        "pkg.mod.async_func",
        "pkg.mod.Service.handler",
        "pkg.mod.newly_added",
    ]


def test_changed_functions_caps_at_max_symbols():
    lines = ["diff --git a/pkg/mod.py b/pkg/mod.py", "--- a/pkg/mod.py", "+++ b/pkg/mod.py"]
    for i in range(15):
        lines.extend([f"@@ -{i * 10},2 +{i * 10},2 @@ def func_{i}():", "-    pass", "+    return"])
    symbols = changed_functions("\n".join(lines), package_root="pkg")
    assert len(symbols) == 10
    assert symbols[0] == "pkg.mod.func_0"
    assert symbols[9] == "pkg.mod.func_9"


def test_changed_functions_handles_empty_or_malformed_diff():
    assert changed_functions("") == []
    assert changed_functions("not a diff at all") == []
    assert changed_functions(None) == []  # type: ignore[arg-type]


def test_import_root_mappings():
    assert import_root("pyyaml") == "yaml"
    assert import_root("pillow") == "PIL"
    assert import_root("scikit-learn") == "sklearn"
    assert import_root("aiohttp") == "aiohttp"
    assert import_root("some-other-pkg") == "some_other_pkg"


@pytest.mark.asyncio
async def test_fetch_diff_disk_cache_hit(tmp_path):
    cache_dir = tmp_path / "diffs"
    cache_dir.mkdir(parents=True)
    sha = "a001f2782501ad2d24986959f0239a354675f9dc"
    (cache_dir / f"{sha}.diff").write_text("cached diff content", encoding="utf-8")

    # Client can be None because network should never be hit on cache hit
    result = await fetch_diff(None, "yaml", "pyyaml", sha, cache_dir=cache_dir)
    assert result == "cached diff content"


@pytest.mark.asyncio
async def test_fetch_diff_http_success_and_writes_cache(tmp_path):
    cache_dir = tmp_path / "diffs"
    sha = "be846822b4b3a5a54b6163e794b42dc06fba54a3"
    expected_url = f"https://{GITHUB_HOST}/repos/aio-libs/aiohttp/commits/{sha}"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == expected_url
        assert request.headers["Accept"] == "application/vnd.github.diff"
        return httpx.Response(200, text="fetched diff content")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_diff(client, "aio-libs", "aiohttp", sha, cache_dir=cache_dir)

    assert result == "fetched diff content"
    assert (cache_dir / f"{sha}.diff").read_text(encoding="utf-8") == "fetched diff content"


@pytest.mark.asyncio
async def test_fetch_diff_handles_http_errors_gracefully(tmp_path):
    cache_dir = tmp_path / "diffs"
    sha = "be846822b4b3a5a54b6163e794b42dc06fba54a3"

    def handler_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    transport = httpx.MockTransport(handler_404)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_diff(client, "aio-libs", "aiohttp", sha, cache_dir=cache_dir)
    assert result == ""

    # Invalid sha / owner / repo
    assert await fetch_diff(None, "../invalid", "repo", sha, cache_dir=cache_dir) == ""
    assert await fetch_diff(None, "owner", "repo", "not-a-sha", cache_dir=cache_dir) == ""
