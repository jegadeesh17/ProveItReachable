"""Sandbox agent `selfcheck` (ADR-0033): the base-image fingerprint fields, on temp trees."""

import hashlib
import os
import subprocess

import pytest

from pir.sandbox_agent import __main__ as agent_main
from pir.sandbox_agent import protocol, selfcheck

RESULT_KEYS = {"python_version", "uv_version", "files", "stdlib_tree_sha256"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_line(relative: str, entry: str) -> bytes:
    return f"{relative}\t{entry}\n".encode()


def make_tree(root, files: dict[str, bytes]) -> None:
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def symlink(link, target: str) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")


class FakeUv:
    """Stands in for subprocess.run for `uv --version` and records the call."""

    def __init__(self, stdout="uv 0.9.30 (abc 2026-01-01)\n", returncode=0, raises=None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


# ---- files -----------------------------------------------------------------------------------


def test_a_file_is_its_lowercase_sha256_and_a_missing_file_is_missing(tmp_path):
    present = tmp_path / "python3.13"
    present.write_bytes(b"binary")
    assert selfcheck.hash_file(str(present)) == sha(b"binary")
    assert selfcheck.hash_file(str(tmp_path / "uvx")) == "missing"
    assert selfcheck.hash_file(str(tmp_path)) == "missing"  # a directory is not a file


def test_a_link_to_a_file_hashes_the_content_it_points_at(tmp_path):
    (tmp_path / "real").write_bytes(b"libc")
    symlink(tmp_path / "libc.so.6", "real")
    assert selfcheck.hash_file(str(tmp_path / "libc.so.6")) == sha(b"libc")
    symlink(tmp_path / "dangling", "nowhere")
    assert selfcheck.hash_file(str(tmp_path / "dangling")) == "missing"


def test_the_fingerprinted_files_are_the_six_the_architecture_names():
    assert selfcheck.FINGERPRINTED_FILES == (
        "/usr/local/bin/python3.13",
        "/usr/local/lib/libpython3.13.so.1.0",
        "/usr/local/bin/uv",
        "/usr/local/bin/uvx",
        "/lib64/ld-linux-x86-64.so.2",
        "/lib/x86_64-linux-gnu/libc.so.6",
    )
    assert selfcheck.STDLIB_ROOT == "/usr/local/lib/python3.13"


# ---- the stdlib tree hash --------------------------------------------------------------------


def test_the_tree_hash_is_sha256_over_sorted_path_and_entry_lines(tmp_path):
    make_tree(tmp_path, {"b.py": b"B", "a/z.py": b"Z", "a/y.py": b"Y"})
    expected = hashlib.sha256(
        tree_line("a/y.py", sha(b"Y"))
        + tree_line("a/z.py", sha(b"Z"))
        + tree_line("b.py", sha(b"B"))
    ).hexdigest()
    assert selfcheck.stdlib_tree_sha256(str(tmp_path)) == expected


def test_the_tree_hash_is_deterministic_and_independent_of_creation_order(tmp_path):
    files = {"x/one.py": b"1", "x/two.py": b"2", "three.py": b"3", "x/y/four.py": b"4"}
    first, second = tmp_path / "first", tmp_path / "second"
    make_tree(first, files)
    make_tree(second, dict(reversed(list(files.items()))))
    result = selfcheck.stdlib_tree_sha256(str(first))
    assert result == selfcheck.stdlib_tree_sha256(str(first))
    assert result == selfcheck.stdlib_tree_sha256(str(second))
    assert len(result) == 64 and result == result.lower()


def test_the_tree_hash_changes_with_content_name_or_extra_file(tmp_path):
    make_tree(tmp_path / "base", {"a.py": b"1"})
    make_tree(tmp_path / "content", {"a.py": b"2"})
    make_tree(tmp_path / "name", {"b.py": b"1"})
    make_tree(tmp_path / "extra", {"a.py": b"1", "c.py": b""})
    hashes = {
        selfcheck.stdlib_tree_sha256(str(tmp_path / name))
        for name in ("base", "content", "name", "extra")
    }
    assert len(hashes) == 4


def test_a_symlink_hashes_as_its_link_target_not_its_content(tmp_path):
    make_tree(tmp_path / "a", {"real.py": b"R"})
    make_tree(tmp_path / "b", {"real.py": b"R"})
    symlink(tmp_path / "a" / "alias.py", "real.py")
    symlink(tmp_path / "b" / "alias.py", "other.py")  # same name, different target
    expected = hashlib.sha256(
        tree_line("alias.py", "link:real.py") + tree_line("real.py", sha(b"R"))
    ).hexdigest()
    assert selfcheck.stdlib_tree_sha256(str(tmp_path / "a")) == expected
    assert selfcheck.stdlib_tree_sha256(str(tmp_path / "a")) != selfcheck.stdlib_tree_sha256(
        str(tmp_path / "b")
    )


def test_a_symlinked_directory_is_one_link_entry_and_is_not_followed(tmp_path):
    make_tree(tmp_path / "elsewhere", {"secret.py": b"S"})
    root = tmp_path / "root"
    root.mkdir()
    symlink(root / "pkg", str(tmp_path / "elsewhere"))
    target = os.readlink(root / "pkg")  # the platform's own spelling of the target
    expected = hashlib.sha256(tree_line("pkg", f"link:{target}")).hexdigest()
    assert selfcheck.stdlib_tree_sha256(str(root)) == expected


def test_directories_alone_add_nothing_and_a_missing_root_hashes_the_empty_tree(tmp_path):
    (tmp_path / "root" / "empty").mkdir(parents=True)
    empty = hashlib.sha256(b"").hexdigest()
    assert selfcheck.stdlib_tree_sha256(str(tmp_path / "root")) == empty
    assert selfcheck.stdlib_tree_sha256(str(tmp_path / "absent")) == empty


# ---- uv --version ----------------------------------------------------------------------------


def test_uv_version_is_stripped_stdout_spawned_with_the_fixed_environment(monkeypatch):
    monkeypatch.setenv("UV_INDEX_URL", "https://evil.invalid/simple")
    runner = FakeUv("uv 0.9.30\n")
    assert selfcheck.uv_version(runner) == "uv 0.9.30"
    ((argv, kwargs),) = runner.calls
    assert argv == ["/usr/local/bin/uv", "--version"]
    assert kwargs["env"] == selfcheck.UV_ENV and "UV_INDEX_URL" not in kwargs["env"]
    assert kwargs["cwd"] == "/"


@pytest.mark.parametrize(
    "runner",
    [FakeUv(returncode=2), FakeUv(raises=FileNotFoundError("uv")), FakeUv(raises=OSError("x"))],
)
def test_an_unrunnable_uv_gives_a_value_that_cannot_match_a_fingerprint(runner):
    assert selfcheck.uv_version(runner) == "unreadable"


# ---- the command -----------------------------------------------------------------------------


def test_compute_selfcheck_has_exactly_the_four_fields(tmp_path):
    (tmp_path / "bin").mkdir()
    present = tmp_path / "bin" / "python3.13"
    present.write_bytes(b"py")
    absent = tmp_path / "bin" / "uvx"
    stdlib = tmp_path / "lib"
    make_tree(stdlib, {"os.py": b"os"})
    result = selfcheck.compute_selfcheck(
        files=(str(present), str(absent)),
        stdlib_root=str(stdlib),
        runner=FakeUv(),
        uv_bin="/x/uv",
    )
    assert set(result) == RESULT_KEYS
    assert result["uv_version"] == "uv 0.9.30 (abc 2026-01-01)"
    assert result["files"] == {str(present): sha(b"py"), str(absent): "missing"}
    assert result["stdlib_tree_sha256"] == selfcheck.stdlib_tree_sha256(str(stdlib))
    assert result["python_version"].count(".") == 2


def test_the_command_is_registered_takes_no_arguments_and_prints_one_result_line(
    capsys, monkeypatch
):
    assert agent_main.HANDLERS["selfcheck"] is selfcheck.selfcheck_command
    monkeypatch.setattr(selfcheck, "FINGERPRINTED_FILES", ())
    monkeypatch.setattr(selfcheck, "STDLIB_ROOT", "/no/such/dir")
    monkeypatch.setattr(selfcheck, "uv_version", lambda *a, **k: "uv 9.9.9")
    assert agent_main.main(["selfcheck", "--args-b64", "e30="]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    decoded = protocol.decode_result(out)
    assert set(decoded) == RESULT_KEYS and decoded["uv_version"] == "uv 9.9.9"


def test_the_command_refuses_arguments():
    with pytest.raises(protocol.BadInput):
        selfcheck.selfcheck_command({"unexpected": 1})
    assert agent_main.main(["selfcheck", "--args-b64", "eyJhIjoxfQ=="]) == protocol.EXIT_BAD_INPUT
