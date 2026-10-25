"""The uv-canary repository tree (ARCHITECTURE section 22, security review round 2).

Built as a dict of paths, contents and modes because `.venv/` is gitignored and Windows drops
exec bits. Every executable thing in it writes `/tmp/pir-canary-ran` and prints `PIR-CANARY`; the
agent must never run, import or read any of them while resolving. `requirements.in` is the compile
candidate (the pyproject declares its dependencies dynamic) and holds every option and
non-registry line the sanitizer must drop.
"""

import os
from pathlib import Path
from typing import NamedTuple

CANARY_MARKER = "/tmp/pir-canary-ran"
CANARY_OUTPUT = "PIR-CANARY"

REQUIREMENTS_IN = """\
--extra-index-url https://canary.invalid/simple
--find-links https://canary.invalid/wheels
-e .
-r ../outside.txt
-c constraints.txt
pkg @ https://canary.invalid/p.whl
git+https://canary.invalid/r.git#egg=gitpkg
./local
requests>=2.31
PyYAML[extra] ; python_version > '3.8'
"""

SANITIZED_LINES = ["requests>=2.31", "PyYAML[extra] ; python_version > '3.8'"]
DROPPED_OPTIONS = ["--extra-index-url", "--find-links", "-e", "-c"]
NON_REGISTRY_NAMES = ["gitpkg", "pkg"]


class Entry(NamedTuple):
    content: str
    mode: int = 0o644
    link_to: str | None = None  # in-tree symlink target, relative to the link's directory


_SCRIPT = f"#!/bin/sh\ntouch {CANARY_MARKER}\necho {CANARY_OUTPUT}\n"


def uv_canary_tree() -> dict[str, Entry]:
    return {
        ".venv/pyvenv.cfg": Entry("home = /usr/local/bin\ninclude-system-site-packages = false\n"),
        ".venv/bin/python": Entry(_SCRIPT, 0o755),
        ".venv/bin/python3": Entry("", 0o777, link_to="python"),
        ".venv/lib/python3.13/site-packages/canary.pth": Entry(
            f'import os; os.system("touch {CANARY_MARKER}")\n'
        ),
        ".python-version": Entry("3.12\n"),
        "uv.toml": Entry('index-url = "https://canary.invalid/simple"\n'),
        "pyproject.toml": Entry(
            '[project]\nname = "canary"\ndynamic = ["version", "dependencies"]\n\n'
            '[tool.uv]\nindex-url = "https://canary.invalid/simple"\n\n'
            '[tool.uv.sources]\nrequests = { path = "./local" }\n'
        ),
        "requirements.in": Entry(REQUIREMENTS_IN),
    }


def write_tree(root: Path, tree: dict[str, Entry]) -> None:
    """Create the tree under `root`. Raises OSError where the platform refuses symlinks."""
    for relative, entry in tree.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.link_to is not None:
            os.symlink(entry.link_to, target)
            continue
        target.write_text(entry.content, encoding="utf-8", newline="\n")
        target.chmod(entry.mode)
