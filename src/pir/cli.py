"""Command line entry point: `python -m pir <command>` (ARCHITECTURE section 21)."""

import argparse
import sys

from pydantic import ValidationError

from pir.config import load_settings, missing_names, required_for


def _check_config(command: str) -> int:
    """Name missing or invalid variables. Never prints a value."""
    try:
        settings = load_settings()
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        print("Invalid configuration: " + (", ".join(names) or "see variable rules"))
        return 1
    missing = missing_names(settings, required_for(settings, command))
    if missing:
        print(f"Missing required environment variables for {command}: " + ", ".join(missing))
        return 1
    print(f"Configuration for {command} is complete.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pir", description="ProveItReachable")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check-config", help="name missing variables; never prints a value")
    check.add_argument("--for", dest="target", choices=["scan", "eval", "serve"], default="scan")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check-config":
        return _check_config(args.target)
    parser.print_help(sys.stderr)
    return 2
