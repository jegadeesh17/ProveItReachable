# ProveItReachable Specification

This document defines what ProveItReachable must do, as journeys and testable acceptance criteria. Source: [project mental model](PROJECT_MENTAL_MODEL.md). Architecture decisions are in `ARCHITECTURE.md` (written by the architect). Dates are YYYY-MM-DD.

## 1. Summary

- **Vision:** evidence-backed vulnerability-reachability triage for public Python GitHub repos. Dependency scanners list every vulnerable package. ProveItReachable answers "does my code reach the vulnerable function?" with a verdict plus evidence (advisory links, source locations, call path).
- **Context:** Nebius x NVIDIA Global AI Hackathon entry, Coding & Agentic Engineering track. Submissions close 2026-10-30 10:00 AM PT.
- **Posture:** Production (validation, typed schemas, thorough tests). Under time pressure, features are cut in this order: call-chain proof, progress feed, export formats. Quality gates are never cut.
- **Target persona:** a Python developer or OSS maintainer overwhelmed by scanner alerts. Secondary: hackathon judges who must see the value in under 3 minutes from a cached demo.
- **Milestones:**
  - **M1: Engine + eval (CLI/API).** Resolver, OSV, Tavily, `ast` tool, Nemotron verdicts, JSON report, labeled eval with LangSmith, analysis step inside a Sandbox.
  - **M2: UI + deploy + polish.** Single-screen web UI on the real API, progress feed, cached replays, rate limit and spend cap, hosted demo, error states, Markdown export, optional call-chain proof.
- **Verdict labels (exact strings, case-sensitive):** `likely reachable`, `no reachable path detected`, `uncertain`.
- **Finding contents:** every finding carries a verdict, confidence, limitations, advisory links (exact Tavily/OSV source URLs) and source locations (`file:line`). A `likely reachable` finding also carries a call path.

## 2. User journeys

Each journey is tagged with its milestone. IDs are referenced by the acceptance criteria.

### J1 (M1): Scan a public repo by URL via CLI/API

1. The user submits a public GitHub repo URL through the CLI or HTTP API.
2. The system validates the URL and repo (public, size, Python project).
3. It resolves dependencies exactly (lockfile first, otherwise `uv pip compile`); unresolvable versions are flagged.
4. It queries OSV.dev for known vulnerabilities of each resolved version.
5. It researches each advisory with Tavily to find the vulnerable function or API, keeping source URLs.
6. It runs deterministic `ast` static analysis (imports, aliases, call sites, `file:line`) inside a Sandbox.
7. Nemotron (via Nebius Token Factory) reasons over that evidence through tool calls and produces a verdict per finding.
8. The system returns a JSON report with ranked findings.

### J1b (M2): Scan a public repo by URL in the web UI

Starts from J1; the same pipeline, driven from the single-screen UI.

1. The user pastes a public GitHub repo URL into the UI and starts a scan.
2. The UI shows a step-by-step progress feed: resolve dependencies, query OSV, research advisories, analyze code, reason, verdicts.
3. On completion, the findings report appears on the same screen (see J3).
4. On any error, the UI shows a clear message instead of a blank or broken screen.

### J2 (M2): Pick a curated demo repo (cached replay)

1. The user picks a curated demo repo from a list on the screen.
2. The UI replays a previously recorded scan, including the progress feed, with no live external calls and no credit spend.
3. The same findings report appears as for a live scan (see J3).

### J3 (M2): Read ranked findings

1. The user sees findings ordered with `likely reachable` first.
2. Each finding shows evidence first (call path with `file:line`, advisory links, vulnerable function), then confidence, limitations and the explanation.
3. `uncertain` findings are visually distinct and obvious.
4. If no vulnerable dependencies exist, the user sees a "clean" state.

### J4: Export the report

1. (M1) The user obtains the report as JSON through the CLI/API.
2. (M2) The user exports the report as JSON or Markdown from the UI.

### J5 (M1): Run the evaluation suite

1. The user runs a single eval script over the labeled fixture repos (reachable / no-path / uncertain cases).
2. The script reports precision, recall, case count, abstention rate and limitations.
3. Every run is traced in LangSmith; the labeled dataset is hosted there.

### J6 (M2, cuttable): Gated call-chain proof in a Sandbox

Cut first if the end-to-end product is at risk. Runs only if M1 passed and the Sandbox spike passed.

1. For a `likely reachable` finding, the user (or the pipeline) requests proof.
2. In a Token Factory Sandbox, the system installs the pinned deps, patches the vulnerable function with a recording stub, invokes the repo's entry function, and checks whether the stub is reached.
3. The report shows "proven" or "not proven" with the reason. No exploit payloads are ever used.

## 3. Acceptance criteria

Format: Given / When / Then. Each criterion is tagged with its milestone and journey. Each is checkable by an automated test (external services may be faked or recorded for tests unless stated otherwise).

### 3.1 Scan pipeline and input validation (J1)

- **AC-01 [M1, J1] Pipeline order.** Given a valid public Python repo URL, when a scan runs, then the report records the steps in order: resolve, OSV, advisory research, static analysis, reasoning, report, each with a status.
- **AC-02 [M1, J1] Invalid URL.** Given a URL that is malformed or not a `github.com` repository URL, when it is submitted, then the system returns a clear error and starts no scan (no clone, no external calls).
- **AC-03 [M1, J1] Private or missing repo.** Given a URL of a private or nonexistent GitHub repo, when it is submitted, then the system returns a clear "repository not found or not public" error and starts no scan.
- **AC-04 [M1, J1] Size cap.** Given a repo larger than the size cap, when it is submitted, then the system refuses it with an error that states the cap value, and analyzes nothing. (Cap value: see open question OQ-1.)
- **AC-05 [M1, J1] Not a Python project.** Given a repo with no Python project markers, when it is submitted, then the system returns a "not a Python project" message and no findings.
- **AC-06 [M1, J1] URL fetch boundary.** Given a submitted URL whose host or path would resolve to anything other than a public GitHub repo (for example an internal address or a redirect elsewhere), when it is submitted, then it is rejected and no request is made to the non-GitHub target.

### 3.2 Dependency resolution (J1)

- **AC-07 [M1, J1] Lockfile first.** Given a repo with `uv.lock`, `poetry.lock` or a fully pinned `requirements.txt`, when dependencies are resolved, then the exact versions come from that file and `uv pip compile` is not run.
- **AC-08 [M1, J1] Compile fallback.** Given a repo with unpinned dependency declarations and no lockfile, when dependencies are resolved, then `uv pip compile` is used and the report states that versions were compiled, not locked.
- **AC-09 [M1, J1] Unresolvable never guessed.** Given a dependency whose version cannot be resolved, when the report is produced, then that dependency is listed as unresolved with the reason, it is not queried against OSV with a guessed version, and any finding for it is `uncertain` or absent with the flag shown.

### 3.3 OSV, advisory research, static analysis (J1)

- **AC-10 [M1, J1] OSV query per exact version.** Given resolved dependencies, when OSV is queried, then each query uses the exact package name and resolved version, and each returned advisory ID becomes a candidate finding.
- **AC-11 [M1, J1] Clean state.** Given resolved dependencies with no OSV advisories, when the scan completes, then the report has zero findings and an explicit `clean` status (not an empty or missing result).
- **AC-12 [M1, J1] Advisory research with exact sources.** Given an advisory, when research completes, then the finding contains the vulnerable function or API name (or an explicit "not identified") and advisory links equal to the exact URLs returned by Tavily and OSV, not rewritten or invented.
- **AC-13 [M1, J1] Unidentified vulnerable function.** Given an advisory for which no vulnerable function or API can be identified, when the verdict is produced, then it is `uncertain` with the reason "vulnerable function not identified" (assumption; see OQ-9).
- **AC-14 [M1, J1] Deterministic static analysis.** Given a repo and a vulnerable function name, when the `ast` tool runs twice on the same input, then both runs return identical imports, aliases, call sites and `file:line` locations.
- **AC-15 [M1, J1] Alias handling.** Given a repo that imports the vulnerable module under an alias or via `from ... import ... as ...`, when the `ast` tool runs, then call sites through that alias are found.

### 3.4 Reasoning and verdicts (J1)

- **AC-16 [M1, J1] Inference provider.** Given any reasoning step, when it calls a model, then the request goes only to the Nebius Token Factory endpoint (`https://api.tokenfactory.nebius.com/v1/`) with a Nemotron model, and no other inference provider is contacted.
- **AC-17 [M1, J1] Tool-call reasoning.** Given a finding, when Nemotron reasons, then it obtains code facts only through the `ast` tool calls, and the recorded trace shows those calls.
- **AC-18 [M1, J1] Verdict labels.** Given any report, when it is validated against the report schema, then every verdict is exactly one of `likely reachable`, `no reachable path detected`, `uncertain`; any other value fails validation and is never emitted.
- **AC-19 [M1, J1] Required finding fields.** Given any finding, when the report is validated, then it contains verdict, confidence, limitations (non-empty), at least one advisory link, the affected package and version, and source locations as `file:line`.
- **AC-20 [M1, J1] Call path for likely reachable.** Given a `likely reachable` finding, when the report is validated, then it contains a call path from a repo location to the vulnerable function, and each step is a `file:line` that exists in the analyzed repo.
- **AC-21 [M1, J1] Scope statement for no-path.** Given a `no reachable path detected` finding, when the report is validated, then its limitations state the scope of the analysis (what was and was not examined).
- **AC-22 [M1, J1] No unsupported reachability claim.** Given a model answer of `likely reachable` without a valid call path, when the report is built, then the verdict is downgraded to `uncertain` with the reason.
- **AC-23 [M1, J1] Ranking.** Given a report with mixed verdicts, when findings are listed, then all `likely reachable` come before `no reachable path detected` and `uncertain` ones. (Order between the latter two: see OQ-10.)

### 3.5 Failure handling (J1)

- **AC-24 [M1, J1] Tavily failure.** Given Tavily errors or times out for an advisory, when the scan runs, then that finding is `uncertain` with the failure reason, other findings are unaffected, and the scan does not crash.
- **AC-25 [M1, J1] Nebius failure.** Given Nebius errors or times out during reasoning for a finding, when the scan runs, then that finding is `uncertain` with the failure reason and the scan completes.
- **AC-26 [M1, J1] OSV failure.** Given OSV errors or times out, when the scan runs, then the report completes, marks the affected dependencies as "not checked" with the reason, includes an `uncertain` entry for each, and does not report them as clean.
- **AC-27 [M1, J1] Sandbox timeout in analysis.** Given the Sandbox analysis step exceeds its timeout, when the scan runs, then the affected findings are `uncertain` with reason "sandbox timeout" and the scan completes. (Timeout value: see OQ-5.)
- **AC-28 [M1, J1] Malformed model output.** Given Nemotron returns output that fails schema validation, when the finding is built, then it is retried within a bounded limit and otherwise becomes `uncertain` with the reason, never an unvalidated verdict.

### 3.6 Safety and configuration (J1, whole project)

- **AC-29 [M1, J1] No execution outside Sandbox.** Given any scan, when the repo is analyzed, then no code from the analyzed repo is imported, executed or installed outside a Token Factory Sandbox. A test with a fixture repo containing a canary side effect at import time confirms the canary never fires on the host.
- **AC-30 [M1, J1] Analysis in Sandbox.** Given a scan, when the static analysis step runs, then it runs inside a Sandbox.
- **AC-31 [M1, whole] Secrets from environment.** Given required credentials are missing, when the app starts or a scan begins, then it fails with an error that names the missing variable and does not print any secret value; no secret appears in logs, reports or traces.

### 3.7 JSON report (J4)

- **AC-32 [M1, J4] JSON report.** Given a completed scan, when the user requests the report through the CLI/API, then the output is valid JSON that validates against the published report schema and includes repo URL, resolved commit/ref, dependency list with unresolved flags, step statuses and findings.

### 3.8 Evaluation suite (J5)

- **AC-33 [M1, J5] Labeled cases.** Given the fixture set, when the eval script lists cases, then there are at least 10 labeled cases, including at least one each labeled reachable, no-path and uncertain.
- **AC-34 [M1, J5] Metrics reported.** Given the eval script runs to completion, when it prints and saves results, then the output contains precision, recall, case count, abstention rate and a limitations statement.
- **AC-35 [M1, J5] Metric definitions.** Given a small synthetic set of predictions and labels with known answers, when the metrics are computed, then precision, recall and abstention rate equal the hand-computed values. (Definitions: see OQ-8.)
- **AC-36 [M1, J5] Precision bar.** Given a full eval run, when precision for `likely reachable` is computed, then it is at or above the bar set in `docs/ARCHITECTURE.md`.
- **AC-37 [M1, J5] LangSmith tracing.** Given an eval run, when it executes, then each case produces a LangSmith trace and the labeled dataset exists in LangSmith.
- **AC-38 [M1, J5] Case failure isolation.** Given one eval case raises an error, when the eval runs, then the error is recorded for that case, the remaining cases still run, and the failed case is counted and shown in the output.
- **AC-39 [M1, J5] Claim discipline.** Given the README or UI mentions a "41 to 3" figure, when it is displayed, then it is labeled as a motivating example unless it matches a figure measured by the eval.

### 3.9 Web UI scan and progress (J1b)

- **AC-40 [M2, J1b] Start scan from UI.** Given the UI is loaded, when the user enters a valid public repo URL and starts a scan, then a progress feed appears and the findings report renders on the same screen on completion.
- **AC-41 [M2, J1b] Progress feed order.** Given a live scan, when it runs, then the server-sent events deliver the steps in order (resolve, OSV, advisories, analysis, reasoning, verdicts), and the UI marks each step pending, running, done or failed.
- **AC-42 [M2, J1b] Progress feed cut fallback.** Given the progress feed is cut, when a scan runs, then the UI shows a single busy state and the final report; no other behavior changes.
- **AC-43 [M2, J1b] Error states in UI.** Given each of: invalid URL, private repo, over-size repo, not a Python project, then the UI shows the same message as the API (AC-02 to AC-05), the cap value for over-size, and no stack trace or blank screen.
- **AC-44 [M2, J1b] Partial failure in UI.** Given a Tavily, Nebius, OSV or Sandbox failure during a live scan, when the report renders, then affected findings show as `uncertain` with their reason and the page does not crash.
- **AC-45 [M2, J1b] Unresolved flagged in UI.** Given unresolved dependency versions, when the report renders, then they are shown in a visible "unresolved" list with reasons.

### 3.10 Demo replays, rate limit, spend cap (J2, J1b)

- **AC-46 [M2, J2] Demo list.** Given the UI is loaded, when the user opens the demo picker, then it lists the curated demo repos (set: see OQ-11).
- **AC-47 [M2, J2] Replay costs nothing.** Given a curated demo repo is picked, when the replay runs, then no call is made to Tavily, Nebius, OSV, the Sandbox or GitHub, and spend counters do not change.
- **AC-48 [M2, J2] Replay fidelity.** Given a curated demo replay, when it completes, then the report equals the recorded report for that repo and the progress feed replays the recorded steps.
- **AC-49 [M2, J2] Replay under 3 minutes.** Given a curated demo replay, when it runs, then it reaches the full report in under 180 seconds (target for judges; exact replay pacing: see OQ-11).
- **AC-50 [M2, J1b] Rate limit.** Given a client exceeds the live-scan rate limit, when it submits another live scan, then the request is rejected with a "rate limited" message that says when to retry, and demo replays from that client still work. (Limit values and key: see OQ-3.)
- **AC-51 [M2, J1b] Spend cap.** Given cumulative live-scan spend has reached the hard cap, when any user submits a live scan, then it is refused with a "live scans are paused" message and no external paid call is made. (Cap value: see OQ-4.)
- **AC-52 [M2, J2] Replays during cap.** Given the spend cap is reached, when a user picks a demo repo, then the replay works normally.
- **AC-53 [M2, J1b] Cap checked before spend.** Given spend is within one scan of the cap, when a scan would exceed it, then the scan is stopped or refused before exceeding the cap, and the partial report marks remaining findings `uncertain` with reason "spend cap reached".

### 3.11 Reading findings in the UI (J3)

- **AC-54 [M2, J3] Ranking in UI.** Given a report with mixed verdicts, when it renders, then `likely reachable` findings appear first, in the order of AC-23.
- **AC-55 [M2, J3] Evidence before explanation.** Given a finding card, when it renders, then in document order the evidence (call path with `file:line`, advisory links, vulnerable function) comes before confidence, limitations and the explanation.
- **AC-56 [M2, J3] Uncertain is visually distinct.** Given an `uncertain` finding, when it renders, then it has a distinct style hook (for example a CSS class or data attribute) and a visible text label `uncertain`, so it is not distinguished by color alone.
- **AC-57 [M2, J3] Advisory links.** Given a finding, when it renders, then each advisory link is a clickable link whose href equals the exact source URL in the report and opens safely (`rel="noopener noreferrer"`).
- **AC-58 [M2, J3] Clean state.** Given a scan with zero findings, when the report renders, then the UI shows a "clean" state with a message stating no known-vulnerable dependencies were found and how many dependencies were checked, not an empty screen.
- **AC-59 [M2, J3] Single screen.** Given the app, when the user scans, picks a demo, reads findings and exports, then all of it happens on one screen without navigating to another page.
- **AC-60 [M2, J3] Untrusted text rendering.** Given advisory or model text containing HTML or script, when it renders, then it is displayed as text and not executed.

### 3.12 Export (J4)

- **AC-61 [M2, J4] JSON export.** Given a rendered report, when the user clicks JSON export, then the downloaded file is identical in content to the API JSON report (AC-32).
- **AC-62 [M2, J4] Markdown export.** Given a rendered report, when the user clicks Markdown export, then the downloaded file contains, for every finding, the verdict, confidence, limitations, advisory links, source locations and (for `likely reachable`) the call path, in the same order as the UI.
- **AC-63 [M2, J4] Export from replay.** Given a demo replay, when the user exports, then both formats work with no live calls.
- **AC-64 [M2, J4] Export cut fallback.** Given Markdown export is cut, when the user exports, then JSON export still works and is the only offered format.

### 3.13 Call-chain proof (J6, cuttable)

- **AC-65 [M2, J6] Gated.** Given M1 did not pass or the Sandbox spike did not pass, when the product ships, then no proof control is shown and no proof code path is reachable.
- **AC-66 [M2, J6] Proof success.** Given a `likely reachable` finding and a fixture repo with a known entry function reaching the vulnerable function, when proof runs in a Sandbox, then the pinned deps are installed, the vulnerable function is patched with a recording stub, the entry function is invoked, and the report shows "proven" with the stub's recorded call.
- **AC-67 [M2, J6] Proof not reached.** Given a fixture where the stub is not reached, when proof runs, then the report shows "not proven" with the reason and the verdict is not upgraded.
- **AC-68 [M2, J6] Proof timeout.** Given the Sandbox exceeds the proof timeout, when proof runs, then the step is reported as `uncertain` / "not proven" with reason "sandbox timeout" and the scan completes.
- **AC-69 [M2, J6] No exploit payloads.** Given any proof run, when inputs are inspected, then only recording stubs and the repo's own entry function are used; no exploit payload or crafted malicious input is generated, stored or sent.
- **AC-70 [M2, J6] Proof only in Sandbox.** Given a proof run, when it executes, then all repo code runs only in the Sandbox (see AC-29).

### 3.14 Production operations (NFR-11 to NFR-13)

- **AC-71 [M1, whole] CI workflow.** Given the repo, when the YAML files in `.github/workflows/` are parsed, then one workflow triggers on both `push` and `pull_request` and has steps that run `ruff check` and the fast pytest suite, and it needs no paid-service secret to pass.
- **AC-72 [M1, whole] CI badge.** Given `README.md`, when it is inspected, then it contains a GitHub Actions status badge whose URL points to the workflow from AC-71.
- **AC-73 [M1, whole] JSON log lines.** Given the service starts and runs a scan with all external calls stubbed, when the captured log output is split into lines, then every line parses as a JSON object.
- **AC-74 [M1, J1] Scan log fields and trace correlation.** Given a scan with all external calls stubbed and a fake LangSmith client, when its log lines are inspected, then every line written during the scan carries that scan's `scan_id`, every line that ends a stage carries `stage`, `duration_ms` (non-negative integer) and `outcome`, and the scan's trace metadata carries the same `scan_id`.
- **AC-75 [M1, whole] Logs stay redacted.** Given every secret environment variable is set to a known sentinel value and a stubbed scan in which one external call fails, when all log output is captured, then no sentinel value appears in any line (see AC-31).
- **AC-76 [M2, whole] Budget fractions in health.** Given fake spend pools `nebius` and `tavily` with known budgets and recorded spend, when `GET /api/health` is called, then the response reports for each pool a remaining fraction equal to (budget - spent) / budget clamped to 0-1, and the response contains no secret sentinel value.
- **AC-77 [M2, whole] Keepalive spend alert.** Given the keepalive monitor check and a stubbed health response, when any pool's remaining fraction is below 0.25, then the check exits non-zero and names the pool; when every pool is at or above 0.25 and the health check passes, then it exits zero.

## 4. Regression criteria

Not applicable: new project, no existing behavior.

## 5. Non-functional requirements

- **NFR-01 [M2] Availability.** The hosted demo is publicly accessible, without login, and free for judges until 2026-12-15. A health check on the public URL passes at submission and is monitored until that date.
- **NFR-02 [M1] Execution safety.** Analyzed repo code is never executed outside a Token Factory Sandbox (see AC-29).
- **NFR-03 [M1] Secrets.** Secrets are read only from environment variables, never committed, logged, printed or included in reports or traces (see AC-31).
- **NFR-04 [M1] Inference provider.** Inference goes only through Nebius Token Factory with a Nemotron model (see AC-16).
- **NFR-05 [M1] Typed schemas.** The report, finding, evidence and API request/response shapes are typed and validated; invalid data is rejected, not coerced.
- **NFR-06 [M1] Failure isolation.** No external failure (Tavily, Nebius, OSV, Sandbox, GitHub) crashes a scan; each becomes a visible reason on the affected item.
- **NFR-07 [M1] Tooling.** Python 3.13, pytest, ruff; code passes `ruff` and the test suite.
- **NFR-08 [M2] Delivery artifacts.** The repo has an MIT `LICENSE` and a setup README, and `docs/PLATFORM_FEEDBACK.md` exists as a running log.
- **NFR-09 [M2] Cost durability.** Live scans stay within credits through 2026-12-15 via cached replays, rate limit and spend cap (AC-47, AC-50, AC-51).
- **NFR-10 [M2] Usability under judging.** A judge can reach a full report from a cached demo in under 3 minutes with no instructions beyond the screen (AC-49).
- **NFR-11 [M1] Continuous integration.** A GitHub Actions workflow runs ruff and the fast test suite on every push and pull request, and the README shows its status badge (see AC-71, AC-72).
- **NFR-12 [M1] Structured logs.** Every log line the service writes is a JSON object; lines written during a scan carry `scan_id`, plus `stage`, `duration_ms` and `outcome` where they apply, correlate with that scan's LangSmith trace, and keep secrets redacted (see AC-73, AC-74, AC-75).
- **NFR-13 [M2] Spend alerts.** `GET /api/health` reports the remaining budget fraction (0-1) per spend pool (`nebius`, `tavily`) and never secrets, and the keepalive monitor (NFR-01) fails, so GitHub emails the owner, when any pool is below 0.25 (see AC-76, AC-77).

## 6. Out of scope

Copied verbatim from the mental model's non-goals:

Other languages, private repos, user accounts, auto-fix PRs, exploit generation, Toloka, a paid GPU deploy, a custom tracing UI.

Declined production additions (user, 2026-10-25): an ops stats endpoint, Sentry, Prometheus/Grafana.

## 7. Open questions

Each has the assumption made so work can proceed. The architect decides unless noted.

- **OQ-1 Size cap.** Value not specified (repo size on disk, file count, or both). Assumption: a cap exists, is configurable, and is stated in the refusal message (AC-04).
- **OQ-2 Precision and recall bar.** Set by the architect in `docs/ARCHITECTURE.md` (AC-36). No recall bar is defined; assumption: recall is reported only, with no pass/fail threshold.
- **OQ-3 Rate limit.** Requests per period and the client key (IP or session) are unspecified. Assumption: per-client limit on live scans only; replays are not limited.
- **OQ-4 Spend cap.** The dollar value and how spend is measured (tokens, Tavily calls, Sandbox minutes) are unspecified. Assumption: a single configurable cap across all paid services, sized so credits last until 2026-12-15.
- **OQ-5 Timeouts.** Values for external calls, the Sandbox analysis step, the proof step, and total scan time are unspecified. Assumption: all are configurable with bounded defaults.
- **OQ-6 Supported dependency sources.** The mental model names `uv.lock`, `poetry.lock`, pinned `requirements.txt` and `uv pip compile`. Unspecified: `pyproject.toml`-only projects, `Pipfile`, `setup.py`, dev/optional dependency groups, and whether transitive dependencies are scanned. Assumption: transitive dependencies from lockfiles are included; others go through `uv pip compile` where possible, else unresolved.
- **OQ-7 Repo ref.** Which branch or commit is scanned (default branch HEAD assumed), and whether the commit SHA is recorded in the report (assumed yes).
- **OQ-8 Metric definitions.** How precision, recall and abstention rate are computed over three labels. Assumption: precision and recall are for `likely reachable` against labeled-reachable cases; abstention rate is the share of cases answered `uncertain`; a correct `uncertain` on an uncertain-labeled case is reported separately.
- **OQ-9 Unidentified vulnerable function.** The mental model is silent. Assumption: the verdict is `uncertain` (AC-13), not `no reachable path detected`.
- **OQ-10 Ranking tie-break.** Order of `no reachable path detected` versus `uncertain`, and order within a verdict (for example by confidence or severity). Assumption: `uncertain` before `no reachable path detected` (uncertain must stay obvious), then by confidence descending.
- **OQ-11 Demo repos.** Which and how many curated repos, and the replay pacing. Assumption: 2 to 3 repos recorded from real M1 runs, including one with a clean or mostly no-path result.
- **OQ-12 Confidence format.** Numeric (0 to 1) or categorical (high/medium/low). Assumption: architect chooses; the schema must define it and the UI must show it.
- **OQ-13 Eval fixture source.** Whether fixtures are local synthetic repos, pinned real GitHub repos, or both. Assumption: local synthetic repos plus pinned real repos with fixed commit SHAs, with advisory data cached so eval is repeatable.
- **OQ-14 CLI/API shape.** Command names, endpoints and whether scans are synchronous or job-based. Assumption: architect defines; the API must support the progress events used by AC-41.
- **OQ-15 Proof entry function.** How the repo's entry function for proof is identified (user-provided, detected from the call path, or convention). Assumption: derived from the first step of the call path.
- **OQ-16 Fallback trigger.** The conditions for using the local Docker fallback instead of the Token Factory Sandbox (for example Sandbox unavailable). Resolved 2026-10-25: no fallback; the Sandbox spike passed (ADR-0021).
