"""Agent entry point: `python3.13 -I -S agent.pyz <command> --args-b64 <base64 JSON>`.

`proof-install` and `proof-run` (M2-TASK-09) belong to the call-chain proof; `proof-run` leaves
the process itself after printing its one result line (see `proof.py`).
"""

import os
import sys

from . import fetch, index, proof, protocol, resolve, selfcheck

HANDLERS: dict[str, protocol.Handler] = {
    "fetch": fetch.fetch,
    "unpack": fetch.unpack,
    "index": index.index_command,
    "resolve": resolve.resolve_command,
    "selfcheck": selfcheck.selfcheck_command,
    "proof-install": proof.proof_install_command,
    "proof-run": proof.proof_run_command,
}


def main(argv: list[str] | None = None) -> int:
    """Run one command and return its exit code (0 ok, 2 bad input, 3 limit, other failure)."""
    return protocol.run_command(sys.argv[1:] if argv is None else argv, HANDLERS)


def run() -> int:
    os.chdir("/")  # first action: the working directory is never the repo (section 7.2)
    return main()


if __name__ == "__main__":
    sys.exit(run())
