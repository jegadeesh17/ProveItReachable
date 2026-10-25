"""FakeHfApi: an in-memory stand-in for the `huggingface_hub.HfApi` calls the HF ledger makes.

It models one dataset repo as a chain of commits. `create_commit(parent_commit=...)` raises an
`HfHubHTTPError` with status 412 when the parent is not the head (what the Hub does), and, like the
real client, returns the head without committing when every file is unchanged. No network.
"""

import hashlib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
from huggingface_hub.errors import HfHubHTTPError, RemoteEntryNotFoundError, RepositoryNotFoundError


def http_error(cls: type[HfHubHTTPError], status: int, message: str = "error") -> HfHubHTTPError:
    """An HfHubHTTPError (or subclass) carrying a real response with `status`."""
    request = httpx2.Request("POST", "https://huggingface.co/api/datasets/user/pir-spend-ledger")
    return cls(message, response=httpx2.Response(status, request=request))


class FakeHfApi:
    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        *,
        enforce_parent: bool = True,
    ) -> None:
        self.enforce_parent = enforce_parent
        self.history: list[tuple[str, dict[str, bytes]]] = []
        self._counter = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.before_commit: Callable[[FakeHfApi], None] | None = None  # a racing writer
        self.fail: dict[str, list[Exception]] = {}  # method name -> exceptions to raise, in order
        self._push(dict(files or {"README.md": b"# ledger\n"}))

    # -- test helpers -------------------------------------------------------------------------
    @property
    def head(self) -> str:
        return self.history[-1][0]

    @property
    def files(self) -> dict[str, bytes]:
        return self.history[-1][1]

    def external_commit(self, filename: str, content: bytes) -> str:
        """Another writer commits to the head (advances it without going through this client)."""
        return self._push({**self.files, filename: content})

    def count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)

    def _push(self, files: dict[str, bytes]) -> str:
        self._counter += 1
        sha = hashlib.sha1(f"commit-{self._counter}".encode(), usedforsecurity=False).hexdigest()
        self.history.append((sha, files))
        return sha

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))
        queue = self.fail.get(method)
        if queue:
            raise queue.pop(0)

    # -- the HfApi surface the ledger uses ----------------------------------------------------
    def repo_info(
        self, repo_id: str, *, repo_type: str | None = None, token: str | None = None, **_: Any
    ) -> SimpleNamespace:
        self._record("repo_info", repo_id=repo_id, repo_type=repo_type, token=token)
        if repo_type != "dataset":
            raise http_error(RepositoryNotFoundError, 404)
        return SimpleNamespace(sha=self.head)

    def hf_hub_download(
        self,
        repo_id: str,
        filename: str,
        *,
        repo_type: str | None = None,
        revision: str | None = None,
        cache_dir: str | Path | None = None,
        token: str | None = None,
        **_: Any,
    ) -> str:
        self._record(
            "hf_hub_download",
            repo_id=repo_id,
            filename=filename,
            repo_type=repo_type,
            revision=revision,
            token=token,
        )
        commits = dict(self.history)
        if revision not in commits:
            raise http_error(RepositoryNotFoundError, 404)
        if filename not in commits[revision]:
            raise http_error(RemoteEntryNotFoundError, 404)
        assert cache_dir is not None
        target = Path(cache_dir) / filename
        target.write_bytes(commits[revision][filename])
        return str(target)

    def create_commit(
        self,
        repo_id: str,
        operations: list[Any],
        *,
        commit_message: str,
        repo_type: str | None = None,
        parent_commit: str | None = None,
        token: str | None = None,
        **_: Any,
    ) -> SimpleNamespace:
        self._record(
            "create_commit",
            repo_id=repo_id,
            commit_message=commit_message,
            repo_type=repo_type,
            parent_commit=parent_commit,
            token=token,
            paths=[op.path_in_repo for op in operations],
        )
        if self.before_commit is not None:
            hook, self.before_commit = self.before_commit, None
            hook(self)
        if self.enforce_parent and parent_commit is not None and parent_commit != self.head:
            raise http_error(HfHubHTTPError, 412, "Precondition Failed")
        new = dict(self.files)
        for op in operations:
            data = op.path_or_fileobj
            assert isinstance(data, bytes), "the ledger commits bytes"
            new[op.path_in_repo] = data
        if new == self.files:  # the real client skips an empty commit and returns the head
            return SimpleNamespace(oid=self.head)
        return SimpleNamespace(oid=self._push(new))
