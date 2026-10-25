"""Canary: running this setup script on the host would write the marker file (AC-29)."""

import os

_MARKER = os.environ.get("PIR_CANARY_MARKER") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "CANARY_RAN"
)
with open(_MARKER, "a", encoding="utf-8") as handle:
    handle.write("ran setup.py\n")

from setuptools import setup  # noqa: E402

setup(name="canary", version="0.0.1")
