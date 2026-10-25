"""Command line entry point: `python -m pir <command>` (ARCHITECTURE section 21)."""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import re
import sys
import uuid
from pathlib import Path

from pydantic import ValidationError

from pir import messages
from pir.agent_client import LocalFixtureTarget
from pir.config import ConfigError, Settings, load_settings, missing_names, require, required_for
from pir.deps import build_deps
from pir.github import (
    GitHubUnavailable,
    InvalidRepoUrl,
    NotPythonProject,
    RepoCoordinates,
    RepoNotFound,
    RepoTooLarge,
    ValidatedTarget,
    parse_repo_url,
)
from pir.logs import bind_scan, configure_logging, scan_id_var
from pir.markdown import render_markdown
from pir.models.api import check_ref
from pir.models.report import ScanReport
from pir.pipeline import github_repo_ref, local_repo_ref, run_scan
from pir.replay import Recorder, summary, write_recording
from pir.spend import (
    HEADROOM_MIN_USD,
    CapsRefused,
    CasProbeFailed,
    HfDatasetSpendLedger,
    LedgerConflict,
    LedgerUnavailable,
    Refused,
    SystemClock,
    plan_set_caps,
    print_spend_summary,
    spend_summary_lines,
)

logger = logging.getLogger("pir.cli")

_SLUG_RE = re.compile(r"[a-z0-9-]{1,40}")


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


def report_schema_json() -> str:
    """The report JSON Schema as published in schemas/report.schema.json."""
    return json.dumps(ScanReport.model_json_schema(), indent=2, sort_keys=True) + "\n"


def local_commit_sha(path: Path) -> str:
    """A stable 40-hex id for a local fixture: a hash of its relative paths and file bytes."""
    digest = hashlib.sha1(usedforsecurity=False)
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode("utf-8") + b"\0")
        digest.update(file.read_bytes() + b"\0")
    return digest.hexdigest()


def _refusal_message(refused: Refused) -> str:
    if refused.reason == "daily_limit":
        return messages.rate_limited(max(1, math.ceil((refused.retry_after_s or 60) / 60)))
    return messages.message("live_scans_paused")


def _admission_message(exc: Exception) -> str:
    """The section 8 message for an intake error."""
    if isinstance(exc, RepoNotFound):
        return messages.message("repo_not_found")
    if isinstance(exc, RepoTooLarge):
        return messages.repo_too_large(
            max_mb=exc.max_mb, max_files=exc.max_files, mb=exc.mb, files=exc.files
        )
    if isinstance(exc, NotPythonProject):
        return messages.not_python_project(exc.detail)
    if isinstance(exc, GitHubUnavailable):
        return messages.message("github_unavailable")
    return messages.message("invalid_url")


async def _scan_async(
    settings: Settings,
    coords: RepoCoordinates | None,
    args: argparse.Namespace,
) -> int:
    deps = build_deps(settings)
    try:
        target: ValidatedTarget | LocalFixtureTarget
        if args.local:
            path = Path(args.local).resolve()
            target = LocalFixtureTarget(path=str(path), commit_sha=local_commit_sha(path))
            repo = local_repo_ref(target, coords)
        else:
            assert coords is not None  # checked by the caller
            try:
                target = await deps.validate(coords, args.ref)
            except (
                InvalidRepoUrl,
                RepoNotFound,
                RepoTooLarge,
                NotPythonProject,
                GitHubUnavailable,
            ) as exc:
                print(_admission_message(exc), file=sys.stderr)
                return 2
            repo = github_repo_ref(target)
        scan_id = uuid.uuid4().hex
        token = bind_scan(scan_id)  # so ledger log lines written during reserve carry the scan id
        try:
            budget = await deps.meter.reserve(scan_id)
        finally:
            scan_id_var.reset(token)
        if isinstance(budget, Refused):
            print(_refusal_message(budget), file=sys.stderr)
            return 2
        try:
            report = await run_scan(deps, target, budget, repo=repo, scan_id=scan_id)
        except Exception:
            error_id = uuid.uuid4().hex[:12]
            logger.exception("scan failed", extra={"error_id": error_id})
            print(messages.internal_error(error_id), file=sys.stderr)
            return 1
        finally:
            await deps.meter.settle(budget)
            print_spend_summary(deps.meter)
        if args.format == "md":
            text = render_markdown(report)
        else:
            text = report.model_dump_json(indent=2) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    finally:
        await deps.aclose()


def _scan(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        detail = ", ".join(names) or "see variable rules"
        print("Invalid configuration: " + detail, file=sys.stderr)
        return 1
    if args.executor:
        settings = settings.model_copy(update={"executor": args.executor})
    if settings.executor == "fake" and not args.local:
        print("The fake executor only runs a local fixture: pass --local PATH.", file=sys.stderr)
        return 2
    if not args.repo_url and not args.local:
        print("Pass a repository URL, or --local PATH for a local fixture.", file=sys.stderr)
        return 2
    coords: RepoCoordinates | None = None
    if args.repo_url:
        try:
            coords = parse_repo_url(args.repo_url)
        except InvalidRepoUrl:
            print(messages.message("invalid_url"), file=sys.stderr)
            return 2
    if args.ref:
        try:
            check_ref(args.ref)
        except ValueError:
            print(messages.request_invalid("the ref is not valid"), file=sys.stderr)
            return 2
    if args.local and not Path(args.local).is_dir():
        print("--local must be a directory.", file=sys.stderr)
        return 2
    try:
        require(settings, required_for(settings, "scan"))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    configure_logging(settings)
    return asyncio.run(_scan_async(settings, coords, args))


def _recording_problem(report: ScanReport) -> str | None:
    """Why this scan must not become a demo, or None. A demo of a scan the Sandbox did not serve
    (the base-image selfcheck failed, resolve or analysis did not run) would mislead."""
    states = {step.name: step.state for step in report.steps}
    for name in ("resolve", "analysis"):
        if states.get(name) != "done":
            return f"the {name} step did not finish ({states.get(name)})"
    if report.resolution.method == "failed":
        return "dependency resolution failed"
    return None


async def _record_async(
    settings: Settings, coords: RepoCoordinates, args: argparse.Namespace
) -> int:
    deps = build_deps(settings)
    try:
        try:
            target = await deps.validate(coords, args.ref)
        except (
            InvalidRepoUrl,
            RepoNotFound,
            RepoTooLarge,
            NotPythonProject,
            GitHubUnavailable,
        ) as exc:
            print(_admission_message(exc), file=sys.stderr)
            return 2
        scan_id = uuid.uuid4().hex
        token = bind_scan(scan_id)
        try:
            budget = await deps.meter.reserve(scan_id)
        finally:
            scan_id_var.reset(token)
        if isinstance(budget, Refused):
            print(_refusal_message(budget), file=sys.stderr)
            return 2
        recorder = Recorder()
        try:
            report = await run_scan(
                deps, target, budget, repo=github_repo_ref(target), scan_id=scan_id, sink=recorder
            )
        except Exception:
            error_id = uuid.uuid4().hex[:12]
            logger.exception("recording failed", extra={"error_id": error_id})
            print(messages.internal_error(error_id), file=sys.stderr)
            return 1
        finally:
            await deps.meter.settle(budget)
            print_spend_summary(deps.meter)
        problem = _recording_problem(report)
        if problem is not None:
            print(f"Not recorded: {problem}. Nothing was written.", file=sys.stderr)
            return 1
        recording = recorder.to_recording(args.slug, args.title, args.description, report)
        path = write_recording(Path(settings.replay_dir), recording)
        info = summary(recording)
        print(
            f"Recorded {path} ({len(recording.events)} events, {info.by_verdict})", file=sys.stderr
        )
        return 0
    finally:
        await deps.aclose()


def _record(args: argparse.Namespace) -> int:
    """`record`: run one real scan and save it as a demo replay. Spends credits (section 14)."""
    try:
        settings = load_settings()
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        print(
            "Invalid configuration: " + (", ".join(names) or "see variable rules"), file=sys.stderr
        )
        return 1
    if not _SLUG_RE.fullmatch(args.slug):
        print("--slug must match ^[a-z0-9-]{1,40}$.", file=sys.stderr)
        return 2
    if not args.title.strip() or not args.description.strip():
        print("--title and --description must not be empty.", file=sys.stderr)
        return 2
    try:
        coords = parse_repo_url(args.repo_url)
    except InvalidRepoUrl:
        print(messages.message("invalid_url"), file=sys.stderr)
        return 2
    if args.ref:
        try:
            check_ref(args.ref)
        except ValueError:
            print(messages.request_invalid("the ref is not valid"), file=sys.stderr)
            return 2
    settings = settings.model_copy(update={"executor": "sandbox"})  # a demo is a real scan
    try:
        require(settings, required_for(settings, "scan"))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    configure_logging(settings)
    return asyncio.run(_record_async(settings, coords, args))


def _eval(args: argparse.Namespace) -> int:
    """Run the evaluation suite: exit 0 bar met, 1 not met, 2 could not run (section 15)."""
    from pir.evaluation.runner import run_eval_command  # imports this module; keep it lazy

    try:
        settings = load_settings()
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        print(
            "Invalid configuration: " + (", ".join(names) or "see variable rules"), file=sys.stderr
        )
        return 2
    configure_logging(settings)
    cases_path = Path(args.cases)
    result = asyncio.run(
        run_eval_command(
            settings,
            cases_path=cases_path,
            refresh=args.refresh_research,
            use_langsmith=not args.no_langsmith,
            results_dir=cases_path.resolve().parent / "results",
            recordings_dir=cases_path.resolve().parent / "recordings",
        )
    )
    sys.stdout.write(result.text)
    return result.exit_code


def _serve(args: argparse.Namespace) -> int:
    """Run the API and UI with one uvicorn worker, JSON logs and no access log (section 18)."""
    import uvicorn  # lazy: only `serve` needs the server

    from pir.api.app import create_app  # lazy: imports this module's siblings

    try:
        settings = load_settings()
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        print(
            "Invalid configuration: " + (", ".join(names) or "see variable rules"), file=sys.stderr
        )
        return 1
    try:
        require(settings, required_for(settings, "serve"))  # nothing is needed when live is off
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    configure_logging(settings)
    try:
        app = create_app(settings=settings)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    uvicorn.run(
        app,
        host=args.host,
        port=args.port if args.port is not None else settings.port,
        workers=1,
        log_config=None,  # uvicorn's loggers propagate to the JSON handler on the root logger
        access_log=False,
    )
    return 0


def _hf_ledger(settings: Settings) -> HfDatasetSpendLedger:
    """The hosted ledger for `settings` (tests replace this to use a fake Hub)."""
    return HfDatasetSpendLedger.from_settings(settings)


async def _set_caps_async(settings: Settings, args: argparse.Namespace) -> int:
    ledger = _hf_ledger(settings)
    plan = {
        "balances": {"nebius": args.nebius_balance, "tavily": args.tavily_balance},
        "headrooms": {"nebius": args.headroom_nebius, "tavily": args.headroom_tavily},
        "caps": {"nebius": args.nebius, "tavily": args.tavily},
        "local_allowance": {"nebius": args.local_nebius, "tavily": args.local_tavily},
        "hub_version": ledger.hub_version,
    }
    try:
        current, _ = await ledger.read_head()
        plan_set_caps(current, now=SystemClock().now(), **plan)  # refuse bad values before probing
        await ledger.probe_stale_parent()
        current, head = await ledger.read_head()  # the probe moved the head
        state = plan_set_caps(current, now=SystemClock().now(), **plan)
        await ledger.write(state, head)
    except CapsRefused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    except CasProbeFailed as exc:
        print(f"Stale-parent check failed, nothing was written: {exc}", file=sys.stderr)
        return 1
    except LedgerConflict:
        print(
            "The ledger changed while set-caps ran; nothing was written. Run it again.",
            file=sys.stderr,
        )
        return 1
    except LedgerUnavailable as exc:
        print(f"The hosted ledger is unavailable, nothing was written: {exc}", file=sys.stderr)
        return 1
    print("Stale-parent check passed; caps written to the hosted ledger.", file=sys.stderr)
    for spender in ("hosted", "local"):
        for line in spend_summary_lines(state, spender):
            print(line, file=sys.stderr)
    return 0


def _ledger_set_caps(args: argparse.Namespace) -> int:
    """`ledger set-caps`: exit 0 written, 1 gate or ledger failure (nothing written), 2 refused."""
    try:
        settings = load_settings()
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]).upper() for err in exc.errors() if err["loc"]})
        print(
            "Invalid configuration: " + (", ".join(names) or "see variable rules"), file=sys.stderr
        )
        return 1
    if settings.spend_ledger_backend != "hf_dataset":
        print("set-caps needs SPEND_LEDGER_BACKEND=hf_dataset.", file=sys.stderr)
        return 1
    try:
        require(settings, ["HF_TOKEN", "SPEND_LEDGER_HF_REPO"])
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    configure_logging(settings)
    return asyncio.run(_set_caps_async(settings, args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pir", description="ProveItReachable")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check-config", help="name missing variables; never prints a value")
    check.add_argument("--for", dest="target", choices=["scan", "eval", "serve"], default="scan")
    sub.add_parser("schema", help="print the report JSON Schema")
    scan = sub.add_parser("scan", help="scan a public GitHub repository and print the report")
    scan.add_argument("repo_url", nargs="?", help="https://github.com/owner/repo")
    scan.add_argument("--ref", help="branch, tag or commit SHA (default: the default branch)")
    scan.add_argument("--out", help="write the report to this file instead of stdout")
    scan.add_argument("--format", choices=["json", "md"], default="json", help="report format")
    scan.add_argument("--executor", choices=["sandbox", "fake"], help="override EXECUTOR")
    scan.add_argument("--local", help="scan a trusted local fixture directory (fake or sandbox)")
    evaluation = sub.add_parser("eval", help="run the evaluation suite (spends credits)")
    evaluation.add_argument("--cases", default="eval/cases.toml", help="the cases file")
    evaluation.add_argument(
        "--refresh-research", action="store_true", help="re-record the OSV and Tavily answers"
    )
    evaluation.add_argument(
        "--no-langsmith", action="store_true", help="offline debugging; the run is not scored"
    )
    record = sub.add_parser("record", help="record a real scan as a demo replay (spends credits)")
    record.add_argument("repo_url", help="https://github.com/owner/repo")
    record.add_argument("--slug", required=True, help="^[a-z0-9-]{1,40}$; the file name")
    record.add_argument("--title", required=True, help="shown in the demo picker")
    record.add_argument("--description", required=True, help="shown in the demo picker")
    record.add_argument("--ref", help="branch, tag or commit SHA to pin (default: default branch)")
    serve = sub.add_parser("serve", help="run the API and the web UI")
    serve.add_argument("--host", default="127.0.0.1", help="interface to bind")
    serve.add_argument("--port", type=int, help="port (default: PORT, else 8000)")
    ledger = sub.add_parser("ledger", help="manage the hosted spend ledger")
    ledger_sub = ledger.add_subparsers(dest="ledger_command")
    caps = ledger_sub.add_parser(
        "set-caps", help="prove the stale-parent check, then write caps and the local allowance"
    )
    caps.add_argument("--nebius-balance", type=float, required=True, help="real Token Factory USD")
    caps.add_argument("--tavily-balance", type=float, required=True, help="real Tavily USD")
    caps.add_argument("--nebius", type=float, help="cap in USD (default: the maximum)")
    caps.add_argument("--tavily", type=float, help="cap in USD (default: the maximum)")
    caps.add_argument(
        "--headroom-nebius", type=float, default=HEADROOM_MIN_USD["nebius"], help="at least 5"
    )
    caps.add_argument(
        "--headroom-tavily", type=float, default=HEADROOM_MIN_USD["tavily"], help="at least 3"
    )
    caps.add_argument("--local-nebius", type=float, default=5.0, help="local allowance in USD")
    caps.add_argument("--local-tavily", type=float, default=2.0, help="local allowance in USD")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check-config":
        return _check_config(args.target)
    if args.command == "schema":
        sys.stdout.write(report_schema_json())
        return 0
    if args.command == "scan":
        return _scan(args)
    if args.command == "eval":
        return _eval(args)
    if args.command == "record":
        return _record(args)
    if args.command == "serve":
        return _serve(args)
    if args.command == "ledger" and args.ledger_command == "set-caps":
        return _ledger_set_caps(args)
    parser.print_help(sys.stderr)
    return 2
