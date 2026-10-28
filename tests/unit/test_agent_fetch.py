"""Sandbox agent fetch and unpack with safe tar extraction ."""

import gzip
import io
import os
import tarfile
from pathlib import Path

import pytest

from pir.sandbox_agent import fetch as agent_fetch
from pir.sandbox_agent.protocol import AgentError, BadInput, LimitExceeded, UnsafeArchive

SHA = "0123456789abcdef0123456789abcdef01234567"
TOP = f"repo-{SHA}"
ARGS = {"owner": "octo", "repo": "demo", "sha": SHA, "max_bytes": 1_000_000, "max_files": 100}


def _file(name: str, data: bytes = b"x = 1\n") -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    return info, data


def _dir(name: str) -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info, None


def _other(name: str, kind: bytes, linkname: str = "") -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    info.mode = 0o644
    return info, None


def _tarball(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def _good_members() -> list[tuple[tarfile.TarInfo, bytes | None]]:
    return [
        _dir(TOP),
        _file(f"{TOP}/pyproject.toml", b"[project]\nname='demo'\n"),
        _file(f"{TOP}/requirements-dev.txt", b"pytest\n"),
        _dir(f"{TOP}/src"),
        _file(f"{TOP}/src/app.py", b"import yaml\n"),
        _file(f"{TOP}/src/util.py", b"y = 2\n"),
        _file(f"{TOP}/docs/readme.md", b"hi\n"),
    ]


def _extract(members, dest: Path, max_bytes: int = 1_000_000, max_files: int = 100) -> None:
    agent_fetch.extract_stream(io.BytesIO(_tarball(members)), str(dest), max_bytes, max_files)


def _opener(payload: bytes, seen: list[str] | None = None):
    def open_url(url: str):
        if seen is not None:
            seen.append(url)
        return io.BytesIO(payload)

    return open_url


def test_build_url_uses_only_codeload_with_validated_parts():  # fetch URL
    url = agent_fetch.build_url("octo", "demo.py_x-1", SHA)
    assert url == f"https://codeload.github.com/octo/demo.py_x-1/tar.gz/{SHA}"


@pytest.mark.parametrize(
    ("owner", "repo", "sha"),
    [
        ("octo/evil", "demo", SHA),
        ("octo", "demo/../x", SHA),
        ("octo", "..", SHA),
        ("octo", "demo", SHA.upper()),
        ("octo", "demo", SHA[:39]),
        ("octo", "demo", SHA + "0"),
        ("octo", "demo", "main"),
        ("oc to", "demo", SHA),
        ("-octo", "demo", SHA),
        ("octo", "de mo", SHA),
        ("octo", "demo?x=1", SHA),
        ("octo@evil.com", "demo", SHA),
        ("", "demo", SHA),
        (None, "demo", SHA),
        ("octo", "demo", None),
    ],
)
def test_build_url_rejects_unvalidated_parts(owner, repo, sha):
    with pytest.raises(BadInput):
        agent_fetch.build_url(owner, repo, sha)


def test_open_https_refuses_other_hosts_before_any_io():
    for url in (
        "https://github.com/octo/demo/archive/x.tar.gz",
        "http://codeload.github.com/octo/demo/tar.gz/" + SHA,
        "https://codeload.github.com.evil.com/x",
        "file:///etc/passwd",
    ):
        with pytest.raises(BadInput):
            agent_fetch.open_https(url)


def test_fetch_extracts_into_repo_root_and_reports_fetch_result(tmp_path):  # fetch
    seen: list[str] = []
    result = agent_fetch.fetch(
        ARGS, repo_root=str(tmp_path / "repo"), opener=_opener(_tarball(_good_members()), seen)
    )
    assert seen == [f"https://codeload.github.com/octo/demo/tar.gz/{SHA}"]
    sizes = [b"[project]\nname='demo'\n", b"pytest\n", b"import yaml\n", b"y = 2\n", b"hi\n"]
    assert result == {
        "commit_sha": SHA,
        "files_total": 5,
        "bytes_total": sum(len(s) for s in sizes),
        "py_files": 2,
        "manifests": ["pyproject.toml", "requirements-dev.txt"],
    }
    assert (tmp_path / "repo" / "src" / "app.py").read_text() == "import yaml\n"
    assert not (tmp_path / "repo" / TOP).exists()


def test_fetch_download_over_the_byte_cap_is_a_limit(tmp_path):  # download cap
    payload = gzip.compress(os.urandom(5000))
    args = {**ARGS, "max_bytes": 1000}
    with pytest.raises(LimitExceeded):
        agent_fetch.fetch(args, repo_root=str(tmp_path / "repo"), opener=_opener(payload))


@pytest.mark.parametrize(
    "bad", [{"max_bytes": 0}, {"max_files": "5"}, {"sha": "abc"}, {"owner": "a/b"}]
)
def test_fetch_validates_its_arguments(tmp_path, bad):
    with pytest.raises(BadInput):
        agent_fetch.fetch({**ARGS, **bad}, repo_root=str(tmp_path / "r"), opener=_opener(b""))
    assert not (tmp_path / "r").exists()


def test_fetch_of_a_garbage_body_is_a_failure_not_a_crash(tmp_path):
    with pytest.raises(AgentError):
        agent_fetch.fetch(
            ARGS, repo_root=str(tmp_path / "repo"), opener=_opener(b"this is not gzip at all")
        )


def test_unpack_reads_the_local_tarball(tmp_path):  # unpack
    tarball = tmp_path / "src.tar.gz"
    tarball.write_bytes(_tarball(_good_members()))
    args = {"sha": SHA, "max_bytes": 1_000_000, "max_files": 100}
    result = agent_fetch.unpack(args, repo_root=str(tmp_path / "repo"), tarball=str(tarball))
    assert result["py_files"] == 2 and result["files_total"] == 5
    assert (tmp_path / "repo" / "pyproject.toml").is_file()


def test_unpack_without_a_top_directory_when_strip_is_zero(tmp_path):
    members = [_file("setup.py", b"print(1)\n"), _file("pkg/mod.py")]
    tarball = tmp_path / "src.tar.gz"
    tarball.write_bytes(_tarball(members))
    args = {"sha": SHA, "max_bytes": 10_000, "max_files": 10, "strip_components": 0}
    result = agent_fetch.unpack(args, repo_root=str(tmp_path / "repo"), tarball=str(tarball))
    assert result["manifests"] == ["setup.py"]
    assert (tmp_path / "repo" / "pkg" / "mod.py").is_file()


def test_unpack_missing_tarball_is_a_failure(tmp_path):
    args = {"sha": SHA, "max_bytes": 10_000, "max_files": 10}
    with pytest.raises(AgentError):
        agent_fetch.unpack(args, repo_root=str(tmp_path / "r"), tarball=str(tmp_path / "none.tgz"))


# --- refusals: each hostile tar shape is a separate test (security review findings 1 and 6) ---


def test_tar_symlink_leaving_the_destination_is_refused(tmp_path):  # SEC-1/6
    members = [_dir(TOP), _other(f"{TOP}/evil", tarfile.SYMTYPE, "../../outside")]
    with pytest.raises(UnsafeArchive):
        _extract(members, tmp_path / "repo")


def test_tar_symlink_with_an_absolute_target_is_refused(tmp_path):  # SEC-1/6
    members = [_dir(TOP), _other(f"{TOP}/passwd", tarfile.SYMTYPE, "/etc/passwd")]
    with pytest.raises(UnsafeArchive):
        _extract(members, tmp_path / "repo")


def test_tar_symlink_chain_escape_through_a_subdirectory_is_refused(tmp_path):  # SEC-1/6
    members = [_dir(TOP), _other(f"{TOP}/a/b/link", tarfile.SYMTYPE, "../../../../x")]
    with pytest.raises(UnsafeArchive):
        _extract(members, tmp_path / "repo")


def test_tar_hardlink_leaving_the_destination_is_refused(tmp_path):  # SEC-1/6
    members = [_dir(TOP), _other(f"{TOP}/evil", tarfile.LNKTYPE, f"{TOP}/../../outside")]
    with pytest.raises(UnsafeArchive):
        _extract(members, tmp_path / "repo")


def test_tar_hardlink_with_an_absolute_target_is_refused(tmp_path):  # SEC-1/6
    members = [_dir(TOP), _other(f"{TOP}/evil", tarfile.LNKTYPE, "/etc/passwd")]
    with pytest.raises(UnsafeArchive):
        _extract(members, tmp_path / "repo")


def test_tar_absolute_member_path_is_refused(tmp_path):  # SEC-1/6
    outside = Path("/tmp/pir-abs-target.txt")
    with pytest.raises(UnsafeArchive):
        _extract([_dir(TOP), _file(outside.as_posix())], tmp_path / "repo")
    with pytest.raises(UnsafeArchive):
        _extract([_dir(TOP), _file("/etc/cron.d/x")], tmp_path / "repo")
    assert not outside.exists()


def test_tar_dotdot_member_path_is_refused(tmp_path):  # SEC-1/6
    with pytest.raises(UnsafeArchive):
        _extract([_dir(TOP), _file(f"{TOP}/../../escaped.py")], tmp_path / "repo")
    assert not (tmp_path / "escaped.py").exists()
    assert not (tmp_path.parent / "escaped.py").exists()


def test_tar_device_members_are_refused(tmp_path):  # SEC-1/6
    for index, kind in enumerate((tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE)):
        with pytest.raises(UnsafeArchive):
            _extract([_dir(TOP), _other(f"{TOP}/dev0", kind)], tmp_path / f"repo{index}")


def test_tar_member_count_over_max_repo_files_is_a_limit(tmp_path):  # SEC-1/6
    members = [_dir(TOP)] + [_file(f"{TOP}/f{i}.py", b"") for i in range(11)]
    with pytest.raises(LimitExceeded):
        _extract(members, tmp_path / "repo", max_files=10)
    _extract(members[:11], tmp_path / "ok", max_files=10)  # exactly 10 files is fine


def test_tar_directory_count_over_the_cap_is_a_limit(tmp_path):  # SEC-1/6
    members = [_dir(TOP)] + [_dir(f"{TOP}/d{i}") for i in range(11)]
    with pytest.raises(LimitExceeded):
        _extract(members, tmp_path / "repo", max_files=10)


def test_tar_decompression_bomb_under_the_download_cap_is_a_limit(tmp_path):  # SEC-1/6
    members = [_dir(TOP), _file(f"{TOP}/zeros.bin", b"\0" * 200_000)]
    payload = _tarball(members)
    max_bytes = 50_000
    assert len(payload) < max_bytes  # passes the download cap, fails the size cap
    with pytest.raises(LimitExceeded):
        agent_fetch.fetch(
            {**ARGS, "max_bytes": max_bytes},
            repo_root=str(tmp_path / "repo"),
            opener=_opener(payload),
        )


def test_decompressed_stream_cap_stops_padding_bombs(tmp_path):  # SEC-1/6
    reader = agent_fetch.CappedReader(io.BytesIO(b"\0" * 1000), 100, "decompressed archive")
    with pytest.raises(LimitExceeded):
        while reader.read(64):
            pass
    longname = tarfile.TarInfo("././@LongLink")
    longname.type = tarfile.GNUTYPE_LONGNAME
    longname.size = 5_000_000  # a header that makes the reader consume far more than it declares
    members = [(longname, b"a" * 5_000_000)]
    with pytest.raises(LimitExceeded):
        _extract(members, tmp_path / "repo", max_bytes=1000, max_files=10)


def test_tar_member_outside_the_top_directory_is_refused(tmp_path):
    with pytest.raises(UnsafeArchive):
        _extract([_file("loose.py")], tmp_path / "repo")


def test_in_tree_symlink_targets_are_allowed_by_the_link_check():
    def link(name: str, target: str) -> tarfile.TarInfo:
        return _other(name, tarfile.SYMTYPE, target)[0]

    agent_fetch._check_link(link("pkg/alias.py", "real.py"), "pkg/alias.py")
    agent_fetch._check_link(link("pkg/alias.py", "../other/real.py"), "pkg/alias.py")
    with pytest.raises(UnsafeArchive):
        agent_fetch._check_link(link("alias.py", "../real.py"), "alias.py")


def test_in_tree_symlink_is_extracted_where_the_platform_allows_it(tmp_path):
    probe = tmp_path / "probe"
    try:
        os.symlink("nothing", probe)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    link = _other(f"{TOP}/alias.py", tarfile.SYMTYPE, "real.py")
    members = [_dir(TOP), _file(f"{TOP}/real.py"), link]
    _extract(members, tmp_path / "repo")
    assert (tmp_path / "repo" / "alias.py").is_symlink()
    result = agent_fetch.summarize_tree(str(tmp_path / "repo"), SHA)
    assert result["files_total"] == 1  # the link is not counted as a file
