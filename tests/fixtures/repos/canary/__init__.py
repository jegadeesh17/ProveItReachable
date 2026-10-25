"""Canary: importing this package on the host would write the marker file (AC-29)."""

import os

_MARKER = os.environ.get("PIR_CANARY_MARKER") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "CANARY_RAN"
)
with open(_MARKER, "a", encoding="utf-8") as handle:
    handle.write("imported __init__.py\n")
