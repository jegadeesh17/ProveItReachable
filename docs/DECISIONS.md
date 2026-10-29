# Architecture Decision Records

Each significant technical decision for ProveItReachable, with its reasons, the alternatives considered and the consequences; details live in [ARCHITECTURE.md](ARCHITECTURE.md).

| ID | Title | Status | Date |
|---|---|---|---|
| [ADR-0001](#adr-0001-python-and-fastapi-service-with-typed-schemas) | Python and FastAPI service with typed schemas | Accepted | 2026-10-24 |
| [ADR-0002](#adr-0002-nemotron-on-token-factory-with-pluggable-reasoning-strategy) | Nemotron on Token Factory with pluggable reasoning strategy | Superseded by ADR-0023 | 2026-10-24 |
| [ADR-0003](#adr-0003-run-untrusted-repo-code-only-inside-the-sandbox) | Run untrusted repo code only inside the Sandbox | Superseded by ADR-0021 | 2026-10-24 |
| [ADR-0004](#adr-0004-local-docker-fallback-only-by-explicit-approval) | Local Docker fallback only by explicit approval | Superseded by ADR-0021 | 2026-10-24 |
| [ADR-0005](#adr-0005-repo-intake-through-the-github-api-with-size-caps) | Repo intake through the GitHub API with size caps | Accepted | 2026-10-24 |
| [ADR-0006](#adr-0006-dependency-resolution-order-and-supported-sources) | Dependency resolution order and supported sources | Superseded by ADR-0028 | 2026-10-24 |
| [ADR-0007](#adr-0007-deterministic-ast-index-queried-by-nemotron-through-tool-calls) | Deterministic ast index queried by Nemotron through tool calls | Accepted | 2026-10-24 |
| [ADR-0008](#adr-0008-osvdev-as-the-advisory-source) | OSV.dev as the advisory source | Accepted | 2026-10-24 |
| [ADR-0009](#adr-0009-tavily-research-with-exact-source-urls) | Tavily research with exact source URLs | Superseded by ADR-0031 | 2026-10-24 |
| [ADR-0010](#adr-0010-report-schema-verdicts-confidence-and-ranking) | Report schema, verdicts, confidence and ranking | Accepted | 2026-10-24 |
| [ADR-0011](#adr-0011-job-based-http-api-with-server-sent-events-and-a-cli) | Job-based HTTP API with server-sent events and a CLI | Accepted | 2026-10-24 |
| [ADR-0012](#adr-0012-single-screen-frontend-in-plain-html-css-and-javascript) | Single-screen frontend in plain HTML, CSS and JavaScript | Accepted | 2026-10-24 |
| [ADR-0013](#adr-0013-host-the-demo-on-a-free-hugging-face-docker-space) | Host the demo on a free Hugging Face Docker Space | Superseded by ADR-0040 | 2026-10-24 |
| [ADR-0014](#adr-0014-spend-metering-hard-caps-and-rate-limits) | Spend metering, hard caps and rate limits | Superseded by ADR-0022 | 2026-10-24 |
| [ADR-0015](#adr-0015-durable-spend-ledger-in-a-private-hugging-face-dataset) | Durable spend ledger in a private Hugging Face dataset | Superseded by ADR-0029 | 2026-10-24 |
| [ADR-0016](#adr-0016-cached-replays-for-curated-demo-repos) | Cached replays for curated demo repos | Accepted | 2026-10-24 |
| [ADR-0017](#adr-0017-evaluation-suite-metrics-and-precision-bar-with-langsmith) | Evaluation suite, metrics and precision bar with LangSmith | Accepted | 2026-10-24 |
| [ADR-0018](#adr-0018-call-chain-proof-with-a-recording-stub-gated-and-cuttable) | Call-chain proof with a recording stub, gated and cuttable | Accepted | 2026-10-24 |
| [ADR-0019](#adr-0019-bounded-timeouts-and-failure-becomes-uncertain) | Bounded timeouts and failure becomes uncertain | Accepted | 2026-10-24 |
| [ADR-0020](#adr-0020-mit-license-and-secrets-only-from-environment-variables) | MIT license and secrets only from environment variables | Accepted, amended by ADR-0040 (hosted secrets kept in Google Secret Manager, still read as environment variables) | 2026-10-24 |
| [ADR-0021](#adr-0021-sandbox-runs-through-the-sdk-and-rest-api-with-no-docker-fallback) | Sandbox runs through the SDK and REST API, with no Docker fallback | Superseded by ADR-0027 | 2026-10-25 |
| [ADR-0022](#adr-0022-spend-metering-from-reported-sandbox-cost-with-build-and-demo-caps) | Spend metering from reported Sandbox cost, with build and demo caps | Superseded by ADR-0029 | 2026-10-25 |
| [ADR-0023](#adr-0023-nemotron-3-super-with-native-tool-calling-only) | Nemotron 3 Super with native tool calling only | Accepted | 2026-10-25 |
| [ADR-0024](#adr-0024-production-operations-ci-json-logs-and-spend-alerts) | Production operations: CI, JSON logs and spend alerts | Accepted | 2026-10-25 |
| [ADR-0025](#adr-0025-contract-additions-from-the-prototype-review) | Contract additions from the prototype review | Accepted | 2026-10-25 |
| [ADR-0026](#adr-0026-self-hosted-geist-fonts-under-a-strict-csp) | Self-hosted Geist fonts under a strict CSP | Accepted | 2026-10-25 |
| [ADR-0027](#adr-0027-sandbox-steps-with-network-on-never-run-repository-code) | Sandbox steps with network on never run repository code | Superseded by ADR-0032 | 2026-10-25 |
| [ADR-0028](#adr-0028-dependency-resolution-order-compiled-without-builds) | Dependency resolution order, compiled without builds | Accepted, amended by ADR-0032 and ADR-0034 | 2026-10-25 |
| [ADR-0029](#adr-0029-one-spend-ledger-for-every-spender-with-allowances-and-a-daily-ceiling) | One spend ledger for every spender, with allowances and a daily ceiling | Accepted | 2026-10-25 |
| [ADR-0030](#adr-0030-images-pinned-by-digest-and-hash-checked-installs) | Images pinned by digest and hash-checked installs | Accepted, amended by ADR-0033 (Sandbox image); image contents partly superseded by ADR-0036 (`eval/results/latest.json`) | 2026-10-25 |
| [ADR-0031](#adr-0031-tavily-research-with-exact-source-urls-and-a-7-day-cache) | Tavily research with exact source URLs and a 7-day cache | Accepted | 2026-10-25 |
| [ADR-0032](#adr-0032-no-repository-file-influences-a-network-on-step) | No repository file influences a network-on step | Accepted, amended by ADR-0041 (proof-install also downloads source packages as files) | 2026-10-25 |
| [ADR-0033](#adr-0033-sandbox-base-image-imported-by-version-tag-and-checked-against-a-reviewed-fingerprint) | Sandbox base image imported by version tag and checked against a reviewed fingerprint | Accepted (amended 2026-10-25: measured on GitHub Actions) | 2026-10-25 |
| [ADR-0034](#adr-0034-sandbox-contract-corrections-from-the-live-platform-checks) | Sandbox contract corrections from the live platform checks | Accepted | 2026-10-25 |
| [ADR-0035](#adr-0035-fixed-prompt-token-overhead-in-the-llm-spend-hold) | Fixed prompt-token overhead in the LLM spend hold | Accepted | 2026-10-25 |
| [ADR-0036](#adr-0036-ship-the-published-eval-summary-in-the-space-image) | Ship the published eval summary in the Space image | Accepted (applies unchanged to the Cloud Run image, ADR-0040) | 2026-10-26 |
| [ADR-0037](#adr-0037-call-path-matching-through-default-argument-equivalences-and-inferred-source-roots) | Call-path matching through default-argument equivalences and inferred source roots | Accepted | 2026-10-26 |
| [ADR-0038](#adr-0038-a-langsmith-run-per-pipeline-step-finding-and-tool-call) | A LangSmith run per pipeline step, finding and tool call | Accepted | 2026-10-26 |
| [ADR-0039](#adr-0039-call-chain-proof-runs-main-block-entries-as-scripts-and-stubs-the-call-the-repository-makes) | Call-chain proof runs main-block entries as scripts and stubs the call the repository makes | Accepted | 2026-10-26 |
| [ADR-0040](#adr-0040-host-the-demo-on-google-cloud-run-with-hard-cost-guards) | Host the demo on Google Cloud Run with hard cost guards | Accepted | 2026-10-26 |
| [ADR-0041](#adr-0041-proof-dependencies-without-a-wheel-are-downloaded-with-the-network-on-and-built-with-it-off) | Proof dependencies without a wheel are downloaded with the network on and built with it off | Accepted | 2026-10-26 |
| [ADR-0042](#adr-0042-call-path-edge-verification-anti-tuning-eval-protocol-recorded-proof-in-replays-deep-links-and-warm-ping) | Call-path edge verification, anti-tuning eval protocol, recorded proof in replays, deep links and warm ping | Accepted | 2026-10-26 |
| [ADR-0043](#adr-0043-fix-commit-fallback-for-advisory-research) | Fix-commit fallback for advisory research | Accepted | 2026-10-27 |
| [ADR-0044](#adr-0044-finding-remediation-native-svg-call-path-view-proof-result-authenticity-nonce-and-judging-window-budget-profile) | Finding remediation, native SVG call-path view, proof-result authenticity nonce, and judging-window budget profile | Accepted | 2026-10-27 |
| [ADR-0045](#adr-0045-per-scan-platform-usage-in-the-report-with-an-optional-public-trace-link) | Per-scan platform usage in the report, with an optional public trace link | Accepted | 2026-10-29 |

## ADR-0001: Python and FastAPI service with typed schemas

Date: 2026-10-24
Status: Accepted

### Context

The project is built by one developer in about six days at a Production posture: inputs validated, data typed, thorough tests. The developer works in Python 3.13 with pytest and ruff, and these are fixed.

### Decision

One Python 3.13 package (`pir`) in a project `.venv`, packaged with `pyproject.toml`. FastAPI and Uvicorn serve both the API and the web page. Pydantic v2 models in strict mode describe every report, request and response, so bad data is rejected rather than quietly converted. httpx handles outbound HTTP; pytest (with pytest-asyncio) tests it; ruff lints and formats it.

### Alternatives considered

- Flask: simpler, but no built-in typed request models or async streaming.
- Django: far more than one screen and a few endpoints need.
- Plain dataclasses instead of Pydantic: no validation or JSON Schema for free.

### Consequences

One language and one deployable unit. The report's JSON Schema is generated from the code, so it cannot drift. Async code requires async-aware tests.

## ADR-0002: Nemotron on Token Factory with pluggable reasoning strategy

Date: 2026-10-24
Status: Superseded by ADR-0023

### Context

The hackathon requires inference on Nebius Token Factory with a Nemotron model. Token Factory is OpenAI-compatible and documents tool calling and a JSON mode, but none of this has been tried yet; the spike on 2026-10-25 will check it. The model IDs come from third-party listings.

### Decision

- All inference goes through one client fixed to `https://api.tokenfactory.nebius.com/v1/`, built on the `openai` SDK. The address is a code constant, not a setting. The model comes from `NEBIUS_MODEL`, and the app refuses to start if the name does not contain "nemotron".
- Default model `nvidia/nemotron-3-super-120b-a12b`, to be confirmed by the spike. Reason: tool-calling quality drives the precision bar, and this mixture-of-experts model activates only about 12B parameters per token, so it should cost close to a small model. If the spike shows it is missing or too expensive, `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` is the cheaper fallback.
- Reasoning sits behind a strategy interface with two plug-ins: native tool calling (default) and a JSON-mode loop in which the model writes its tool requests as JSON. Both use the same tools and limits, and either can be chosen with `REASONING_STRATEGY`.

### Alternatives considered

- A single hard-coded strategy: cheaper to write, but if the spike shows tool calling does not work, the core would have to be rewritten.
- Agent frameworks (LangChain agents and similar): more dependencies and less control over validation and metering.
- A different provider for some calls: not allowed by the rules.

### Consequences

Switching model or strategy is a configuration change. Each call is metered from the returned token counts. Prices are placeholders until the spike.

## ADR-0003: Run untrusted repo code only inside the Sandbox

Date: 2026-10-24
Status: Superseded by ADR-0021

### Context

The repositories we analyze are untrusted. Installing or compiling their dependencies can run their build scripts (for example `setup.py`), which is code execution. The track is about agents operating in Token Factory Sandboxes ("ContRee", `contree-sdk`). Their model is image-based: a run can be saved as a new image, and network access can be turned off per run.

### Decision

- No repo code is ever imported, executed or installed on the host. Everything that touches repo files runs inside the Sandbox through an `Executor` interface with three adapters: ContRee (primary), Docker (approved fallback only, ADR-0004) and a fake for tests.
- Inside the Sandbox, a small program of our own that uses only the standard library (the sandbox agent) downloads the repo by commit, unpacks it safely, resolves dependencies, builds the static analysis index and, in M2, runs the proof.
- The unpacked repo is saved as a clean image. Steps that may run repo code (compiling dependencies, proof) run in throwaway copies, so they cannot tamper with what the analysis reads. The analysis runs with network off. No secrets are ever passed into the Sandbox.
- Everything the Sandbox returns is treated as untrusted data: size-capped and strictly validated.

### Alternatives considered

- Parsing and compiling on the host: faster, but it breaks the hard safety rule.
- One long-lived sandbox for all steps: simpler, but code run during compile could falsify the analysis.
- Running each model tool call as a sandbox command: many slow, metered operations per finding.

### Consequences

The safety guarantee can be tested (canary fixture) and reviewed in one place. Several ContRee details are unverified until the spike and are isolated in the adapter. Each scan uses 3 to 4 sandbox operations.

## ADR-0004: Local Docker fallback only by explicit approval

Date: 2026-10-24
Status: Superseded by ADR-0021

### Context

If the Sandbox spike fails, the project still needs somewhere safe to work on repo files. The approved fallback is a locked-down local Docker container. OQ-16 asks when it may be used.

### Decision

- The Docker adapter may be used only if the spike shows the Sandbox is unusable, and only after both the user and the security reviewer approve. It is selected explicitly with `EXECUTOR=docker` plus `DOCKER_FALLBACK_APPROVED=yes`. There is never an automatic switch while the app runs.
- Containers run with no network, as a non-root user, with a read-only root, no host folders mounted, no Docker socket, all capabilities dropped, CPU, memory and process limits, and a hard timeout enforced from the host.
- Because there is no network, compiling dependencies and the proof feature are unavailable in this mode; repos without a lockfile get "unresolved" entries. The repo download is fetched by the host as data only and copied in; the host never unpacks it.
- The fallback runs only on the developer's machine. The hosted demo cannot run Docker, so it would serve replays only.

### Alternatives considered

- Automatic failover to Docker: it would hide Sandbox problems and silently change the safety boundary.
- Docker with network for compiling: breaks the "no network" rule.

### Consequences

Using the fallback is a visible, recorded decision. Results in fallback mode cover fewer repos.

## ADR-0005: Repo intake through the GitHub API with size caps

Date: 2026-10-24
Status: Accepted

### Context

Users paste a URL. That URL must never be able to make our server contact anything other than GitHub (server-side request forgery, AC-06). We also need to refuse private, missing, oversized and non-Python repos before spending anything (AC-02 to AC-05), and to record exactly which commit was scanned (OQ-7). OQ-1 asks for the size cap.

### Decision

- The URL is checked by a strict pattern before any network activity: only `https://github.com/<owner>/<repo>`, with an optional `/tree/<ref>`.
- The server then asks the GitHub API (only `api.github.com`, redirects refused unless they stay on that host) whether the repo exists and is public, pins the default branch (or the requested ref) to a commit SHA, and reads the file list and sizes.
- Caps: 50 MB of files and 5,000 files at that commit (configurable; both values appear in the refusal message). A Python project needs at least one `.py` file and a dependency file at the repository root.
- Every outbound HTTP client has a fixed list of allowed hosts. The user's text only fills the owner and repo parts of a GitHub API path. The source download uses the commit SHA and runs inside the Sandbox.
- An optional read-only `GITHUB_TOKEN` raises GitHub's limit of 60 requests per hour.

### Alternatives considered

- `git clone` on the host: brings untrusted files onto the host and fetches the history.
- Downloading the tarball on the host and uploading it: possible, but upload limits are undocumented, and it holds untrusted bytes on the host. Kept only for the Docker fallback.
- Using GitHub's reported repo size: it includes history and is approximate.

### Consequences

Validation needs three GitHub calls and gives clear, early errors. Results are reproducible because the SHA is recorded.

## ADR-0006: Dependency resolution order and supported sources

Date: 2026-10-24
Status: Superseded by ADR-0028

### Context

Exact versions are the basis of every finding. The fixed rule: lockfiles first, then `uv pip compile`, otherwise flag the dependency as unresolved and never guess. OQ-6 asks which files are supported and whether indirect (transitive) dependencies count.

### Decision

- Order: `uv.lock`, `poetry.lock`, `Pipfile.lock` (cheap to read, so added), then a fully pinned root `requirements.txt`, then `uv pip compile` on `pyproject.toml`, `requirements.in`, an unpinned `requirements.txt`, or `setup.py` / `setup.cfg`.
- Lockfiles contribute all their packages, including transitive ones, with groups recorded. Compiling includes transitive dependencies of the runtime requirements only; dev and optional groups are left out and the report says so.
- Anything that cannot be resolved, or comes from git, a local path or a URL, is listed as unresolved with the reason and is never looked up with a guessed version.
- All of this runs in the Sandbox, because compiling can execute repo code.

### Alternatives considered

- Guessing the latest version for unpinned dependencies: forbidden; it produces false findings.
- Supporting monorepo subfolders: more value, more time; recorded as a limitation.

### Consequences

The resolution method ("locked" or "compiled") is always visible in the report. Repos with only a `Pipfile` come out unresolved.

## ADR-0007: Deterministic ast index queried by Nemotron through tool calls

Date: 2026-10-24
Status: Accepted

### Context

Facts about the code must be repeatable and exact (`file:line`), and Nemotron must get them through tool calls, not text search (AC-14, AC-15, AC-17). Code analysis must happen in the Sandbox (AC-30).

### Decision

- The sandbox agent builds one deterministic index per scan using Python's `ast` module: files, imports with their aliases, function definitions and entry points, uses of the vulnerable packages with the source line, and the repo's internal call graph. Everything is sorted, with no timestamps.
- On the host, a read-only tool answers Nemotron's questions from that index (`find_imports`, `find_call_sites`, `find_callers`, `get_definition`, `list_entry_points`, `find_paths`). The model cannot run anything or see the repo any other way.
- A deterministic gate checks the model's answer: a "likely reachable" call path must consist of real `file:line` locations returned by the tools and must end at a call to the vulnerable function. Otherwise the finding becomes `uncertain`.

### Alternatives considered

- grep or text search: misses aliases, so it does not meet AC-15.
- One sandbox command per tool call: slow and metered.
- Sending whole files to the model: costly, and the model could invent locations.

### Consequences

Tool answers are instant, free and identical every time. Method calls on objects and dynamic imports are only partly tracked, and the report states this in its limitations.

## ADR-0008: OSV.dev as the advisory source

Date: 2026-10-24
Status: Accepted

### Context

We need the known vulnerabilities for each exact package version, for free and without a key.

### Decision

Use the OSV.dev API: one batch query with exact names and versions for the PyPI ecosystem, then the details of each advisory. Duplicate IDs (GHSA, PYSEC, CVE) are merged. If OSV fails, the affected dependencies are marked "not checked" and shown as `uncertain`, never as clean.

### Alternatives considered

- The GitHub Advisory Database API: needs a token and covers less of PyPI.
- pip-audit: wraps OSV/PyPI anyway and would run on the host.

### Consequences

Free and keyless. Severity comes from the advisory's own field and is "unknown" when missing.

## ADR-0009: Tavily research with exact source URLs

Date: 2026-10-24
Status: Superseded by ADR-0031

### Context

Advisories often do not name the vulnerable function. Tavily is required for research (and is a prize target), and links must be the exact URLs returned (AC-12). OQ-9: what to do when no function can be identified.

### Decision

- For each advisory, one Tavily search (basic depth by default) followed by one Nemotron extraction call that names the vulnerable function from the OSV text and the Tavily snippets.
- A name is kept only if it has the shape of a Python path and its last part appears word for word in the supplied text. Its evidence URLs must be among the URLs actually returned. Links are stored exactly as returned.
- If no function can be identified, the finding is `uncertain` with "vulnerable function not identified", and no further reasoning is spent on it.
- Research results are cached per advisory for 30 days, so repeated advisories cost nothing.

### Alternatives considered

- Asking the model from memory, without search: no sources, and invented function names.
- Fetching full pages: more credits and more untrusted text, for little gain.

### Consequences

About 1 to 2 Tavily credits per new advisory. Research quality depends on the snippets.

## ADR-0010: Report schema, verdicts, confidence and ranking

Date: 2026-10-24
Status: Accepted

### Context

The report is consumed by the UI, the export, the evaluation and the recorded demos. The verdict labels are fixed. OQ-12 asks for the confidence format and OQ-10 for the ranking.

### Decision

- A versioned, strict report schema (`schema_version "1.0"`) with exactly three verdicts: `likely reachable`, `no reachable path detected`, `uncertain`. Rules are built into the schema: a call path is required for "likely reachable", a reason for "uncertain", and at least one limitation for every finding.
- **Confidence is a category, high, medium or low,** not a number. Model probabilities are poorly calibrated, and a number would suggest false precision. Deterministic caps apply: `uncertain` is always low; "likely reachable" can be high only when the path starts at a real entry point with no guessed links; "no reachable path detected" is at most medium when files were skipped, and low for indirect dependencies.
- **Ranking:** likely reachable, then uncertain (so it stays obvious), then no reachable path detected. Within each, by confidence, then severity, then package name and advisory ID.

### Alternatives considered

- Numeric confidence from 0 to 1: looks precise but is not.
- Free-text verdicts: cannot be validated or evaluated.

### Consequences

Any schema change ripples into the UI, the exports, the eval and the recordings, so it is listed as expensive to change.

## ADR-0011: Job-based HTTP API with server-sent events and a CLI

Date: 2026-10-24
Status: Accepted

### Context

A scan takes minutes. The UI must show live step-by-step progress (AC-41). M1 must also work from a command line (OQ-14).

### Decision

- `POST /api/scans` validates and refuses quickly (bad URL, private repo, size, rate limit, spend cap) and otherwise returns a job ID at once.
- Progress arrives through server-sent events at `/api/scans/{id}/events`. They are replayable after a reconnect via `Last-Event-ID`.
- The report comes from `/api/scans/{id}/report` (JSON) and `/report.md` (Markdown). Demo replays use the same job and event shape.
- Errors share one format, with fixed messages that the UI shows verbatim.
- Jobs live in memory in a single server process.
- CLI: `python -m pir scan | eval | record | serve | schema | check-config`.

### Alternatives considered

- One long synchronous request: proxies time out and there is no progress.
- WebSockets: two-way, which is not needed, and harder to proxy.
- A job queue with Redis: more infrastructure than a one-process demo needs.

### Consequences

Simple to build and test. Reports are lost on restart (acceptable for a demo). Scaling beyond one process would need a shared store.

## ADR-0012: Single-screen frontend in plain HTML, CSS and JavaScript

Date: 2026-10-24
Status: Accepted

### Context

The UI is one screen. It must render untrusted text safely, ship in the same unit as the API, and first run as a prototype against mock data that later becomes the real frontend.

### Decision

- Plain HTML, CSS and JavaScript modules: no framework, no build step, no third-party scripts. FastAPI serves them.
- One API interface with two adapters: `http` (real) and `mock` (fixture files, chosen with `?api=mock`, with named scenarios for every state).
- Text is inserted only as text, never as HTML. A test fails if HTML-injection functions appear in the code, and a strict Content Security Policy is set.
- Markdown export is produced by the server, so it is tested in Python.

### Alternatives considered

- React or Svelte with Vite: nicer components, but a Node build in the Docker image, more surface and more time.
- Server-rendered templates with HTMX: safe escaping, but the mock-first prototype is harder.
- Streamlit: fast to start, but weak control over layout, accessibility and server-sent events.

### Consequences

Zero build tooling. The prototype is the real UI. The code must be kept tidy by hand.

## ADR-0013: Host the demo on a free Hugging Face Docker Space

Date: 2026-10-24
Status: Superseded by ADR-0040

### Context

A public demo, without login, must stay up until 2026-12-15 at zero or near-zero cost, with no paid GPU. The app is one container and needs outbound internet. The heavy work happens in the Nebius Sandbox, not on the host.

### Decision

- Hugging Face Spaces with the Docker SDK on the free CPU Basic tier (2 vCPU, 16 GB RAM). **Cost: $0.**
- Secrets are stored as Space secrets. The Space stays private until the repository is made public (a public Space exposes its source).
- A free scheduled GitHub Actions workflow pings `/api/health` every 6 hours, which keeps the free Space from sleeping (it sleeps after about 48 hours idle) and doubles as the monitor.
- Free Spaces lose their disk on restart, so the spend ledger is stored elsewhere (ADR-0015).

### Alternatives considered

- Render free tier: sleeps after 15 minutes, slow cold starts, no disk.
- Koyeb free tier: small machine, scales to zero, no disk.
- Fly.io: about $2 to $5 per month, needs a card, so it needs the user's approval.
- Hugging Face persistent storage (about $5 per month) or a CPU upgrade (about $22 per month): paid, needs approval, not needed.
- Google Cloud Run: needs a billing account and more setup.

### Consequences

Free, and familiar to judges. One process only. The Docker fallback cannot run there. SSE through the Hugging Face proxy and the client-IP header are checked at the deploy task.

## ADR-0014: Spend metering, hard caps and rate limits

Date: 2026-10-24
Status: Superseded by ADR-0022

### Context

The credits ($50 Token Factory, $25 Tavily) must last until 2026-12-15 while strangers can start live scans. OQ-3 asks for the rate limit and OQ-4 for the cap and how spend is measured. The two credits are separate pools.

### Decision

- **Metering:** Token Factory spend = tokens reported by each response × configured price, plus Sandbox seconds × a configured rate. Tavily spend = credits per search × price per credit. Prices start as pessimistic placeholders until the spike gives real figures.
- **Two hard caps, one per pool** (defaults $25 Token Factory, $12 Tavily). The rest of the credits are kept for development, the eval, recordings and headroom. Either cap being reached pauses live scans.
- **Reserve before spending:** each scan reserves its maximum budget (default $0.75 and $0.40) before any paid call, and every call checks first with an upper-bound estimate. Spend never passes the cap; if the reservation runs out, the remaining findings say "spend cap reached".
- **Rate limits** on live scans per client IP: 3 per hour and 10 per day; 30 per day overall; at most 2 at once. Replays are never limited or charged.
- A kill switch turns live scans off entirely.

### Alternatives considered

- One combined cap: one pool could run dry while the other still has budget.
- Checking spend after each scan: one big scan could overshoot.
- Counting scans instead of money: scans differ widely in cost.

### Consequences

Overspending is prevented by design, at the cost of refusing some scans early. Real prices must be entered after the spike.

## ADR-0015: Durable spend ledger in a private Hugging Face dataset

Date: 2026-10-24
Status: Superseded by ADR-0029

### Context

A hard cap only works if the record of spending survives restarts. Free Hugging Face Spaces lose their disk on restart, and persistent disk costs money.

### Decision

- Spending is stored in one small JSON file in a private Hugging Face dataset on the same account, written with `huggingface_hub` when a scan reserves its budget and when it finishes (two writes per scan). The token used can write only to that dataset.
- **Fail closed:** if the ledger cannot be read or written, live scans pause.
- A reservation left behind by a crash counts as fully spent.
- Locally, the same ledger is a plain file.

### Alternatives considered

- Keeping it in memory: every restart would grant a fresh cap.
- Upstash Redis (free tier): a better fit for counters, but a new account and vendor.
- Paid Space storage (about $5 per month): needs approval.
- Manual reset values in environment variables: error-prone.

### Consequences

Free and durable, with no new vendor. Each write takes a second or two, which is fine at our volume. The dataset's commit limits are checked at deploy.

## ADR-0016: Cached replays for curated demo repos

Date: 2026-10-24
Status: Accepted

### Context

Judges need to see value in under 3 minutes, and demos must cost nothing (AC-47 to AC-49). OQ-11 asks which repos and what pacing.

### Decision

- Three public repos are recorded from real M1 scans (one with a reachable finding, one mixed with an uncertain finding, one clean or mostly no-path). Each recording, with its full event timeline and report, is saved as a file in the repository.
- A replay never calls any service. It replays the events compressed to at most about 20 seconds and returns the recorded report unchanged. It is labeled as a recorded demo with its date.

### Alternatives considered

- Live scans of demo repos: they cost credits and can fail during judging.
- A static screenshot or video only: not interactive.

### Consequences

The demo works even when live scans are paused. Changing the report format means re-recording.

## ADR-0017: Evaluation suite, metrics and precision bar with LangSmith

Date: 2026-10-24
Status: Accepted

### Context

The eval is a core deliverable. It must report precision, recall, case count, abstention and limitations, with tracing and a hosted dataset in LangSmith. OQ-2, OQ-8 and OQ-13 ask for the bar, the definitions and the fixtures.

### Decision

- **Fixtures:** at least 12 labeled cases (at least 4 reachable, 3 no-path, 3 uncertain) from at least 5 small synthetic repos we write, plus at least 2 real public repos pinned to a commit. OSV and Tavily answers are recorded so runs are repeatable; Nemotron and the Sandbox always run live in a scored run.
- **Metrics:** "likely reachable" is the positive class. Precision = correct reachable claims / all reachable claims (a reachable claim on an uncertain-labeled case counts as wrong). Recall = correct reachable claims / reachable-labeled cases. Abstention = share answered uncertain. A case that crashes is counted as uncertain and listed. Also reported: correct abstentions, no-path precision, "dangerous misses" (reachable cases called no-path) and a confusion matrix.
- **Precision bar: at least 0.80 for "likely reachable",** with at least one reachable prediction. Recall is reported but not gated.
- LangSmith holds the dataset; each run is an experiment with one trace per case, including every tool call.

### Alternatives considered

- A bar of 0.90 or 1.0: with only about 5 reachable cases, one mistake would fail it, which says more about luck than quality.
- No bar: does not meet AC-36.
- Only real repos: hard to label reliably in the time available.

### Consequences

The bar tolerates at most one wrong reachable claim in five. Falling below it triggers the stricter build pipeline. The small sample is stated as a limitation.

## ADR-0018: Call-chain proof with a recording stub, gated and cuttable

Date: 2026-10-24
Status: Accepted

### Context

Optional M2 feature, cut first under time pressure: show that the vulnerable function is really reached, without any exploit. OQ-15 asks how the entry function is chosen.

### Decision

- Built only if M1 and the Sandbox spike pass, and switched on only with `FEATURE_PROOF=true` on the real Sandbox.
- In the Sandbox, the pinned dependencies are installed, the vulnerable function is replaced with a harmless recorder, and the repo's entry function is called with placeholder arguments. The report then says "proven" or "not proven" with the reason.
- The entry function is the first step of the validated call path; the user may pick only another step of that same path.
- Proof never changes the verdict. No exploit input is ever created.

### Alternatives considered

- Asking the model to write test inputs: risks crafted malicious input.
- Upgrading verdicts on proof: mixes two kinds of evidence.

### Consequences

Strong evidence when it works; often "not proven" for functions that need real arguments. Not available in the Docker fallback.

## ADR-0019: Bounded timeouts and failure becomes uncertain

Date: 2026-10-24
Status: Accepted

### Context

Every external service can fail or hang. The spec requires failures to become visible reasons, never crashes (AC-24 to AC-28). OQ-5 asks for the timeout values.

### Decision

- Every external call and Sandbox step has a configurable timeout and a small retry budget (GitHub 10 s; OSV 15 s with 2 retries; Tavily 20 s with 1 retry; Nebius 60 s with 1 retry; Sandbox steps 60 to 180 s; the whole scan 900 s).
- Any failure turns only the affected findings into `uncertain`, with a reason code and plain text; the report always completes.
- Invalid model output is retried twice, then becomes `uncertain`.

### Alternatives considered

- Failing the whole scan on any error: one flaky call would lose all results.
- Unlimited retries: costs credits and time.

### Consequences

Scans always finish with honest gaps marked. Long scans are cut off at 15 minutes.

## ADR-0020: MIT license and secrets only from environment variables

Date: 2026-10-24
Status: Accepted, amended by ADR-0040 (hosted secrets are kept in Google Secret Manager and reach the app as environment variables)

### Context

The hackathon requires an open-source license. The project uses several paid API keys that must never leak.

### Decision

- MIT license.
- All secrets come only from environment variables (a local `.env` file that is never committed, or Space secrets when hosted), held as secret types in code.
- A missing variable produces an error that names it but shows no value.
- Logs and traces pass through a redaction filter. A test checks that fake secrets appear nowhere in logs, reports, recordings or traces.

### Alternatives considered

- Config files with keys: easy to commit by accident.
- A secrets manager: unnecessary for one developer and one Space.

### Consequences

The project is simple to set up from `.env.example`. Redaction must cover every new log path.

## ADR-0021: Sandbox runs through the SDK and REST API, with no Docker fallback

Date: 2026-10-25
Status: Superseded by ADR-0027

### Context

ADR-0003 put every step that touches repo files inside the Token Factory Sandbox, and ADR-0004 kept a locked-down local Docker container as a fallback in case the Sandbox did not work. On 2026-10-25, after beta access was granted, the platform spike passed. It also showed how the Sandbox really behaves: the Python SDK (`contree-sdk` 0.3.6) can import our base image (`images.oci`) and add files, but its `run()` does nothing until `.wait()` is called, always runs as the all-powerful root user, and cannot switch the network off. The REST API can: `POST /sandboxes/v1/instances` accepts a network on/off switch and a non-root user, and the result is read by polling `GET /sandboxes/v1/operations/{id}`. A run with network on can be saved as an image and a later run with network off can start from it. A run that times out is reported as a success with a `timed_out` flag, not as an error. Output can be truncated with a flag, and saved images can be untagged.

### Decision

- The safety rule stays exactly as before: code from an analyzed repository never runs anywhere except inside a Token Factory Sandbox. Everything the Sandbox returns is treated as untrusted and strictly checked.
- `ContreeExecutor` is the only real executor. The SDK is used only to import the base image, to add our own trusted files (our agent program) to it with one fixed setup command, and to untag images at the end. Every command of our agent, and so everything that touches repo files, runs through the REST API as an unprivileged user (uid/gid 65534) with the network explicitly on or off per command (on only to download the repo, compile dependencies, or install them for the proof).
- The "fetch with network, save, analyse offline" pattern is kept: the downloaded repo is saved as a clean image, steps that might run repo code work on throwaway copies, and the analysis runs with the network off. No secrets are passed in; commands start from an empty environment.
- A timed-out run is checked for explicitly and always becomes "sandbox timeout"; truncated output counts as a failure.
- The Docker fallback, its settings (`EXECUTOR=docker`, `DOCKER_FALLBACK_APPROVED`) and its setup steps are removed. The fake executor stays for tests.

### Alternatives considered

- Keeping the Docker fallback as insurance: more code, a second safety boundary to review and test, and it could never run on the hosted demo anyway.
- Using only the SDK: it runs as root with no network control, which breaks the safety design.
- Using only the REST API: possible, but the SDK's image import and file upload are verified and simpler.

### Consequences

One safety boundary to review and test. Two ways of talking to the same service (SDK and REST), both pinned by recorded-response tests. If the Sandbox service is down, live scans report "sandbox failed" and the demo relies on recorded replays. There is no second path if the platform changes, so the Sandbox boundary is listed as expensive to change.

## ADR-0022: Spend metering from reported Sandbox cost, with build and demo caps

Date: 2026-10-25
Status: Superseded by ADR-0029

### Context

ADR-0014 metered Sandbox use as seconds times a guessed price. The spike showed each Sandbox operation reports its own `cost` (about $0.00005 to $0.00025 for a small operation) and its duration and CPU use. Sandboxes are free during the beta, but that may change. The user's credit-budget rule (2026-10-24) also splits the credits: build-time spend is capped at $10 Token Factory and $5 Tavily, and the rest is kept for the hosted demo.

### Decision

- **Metering:** inference spend = tokens reported by each response × configured price. Sandbox spend = the cost each operation reports; if an operation reports none, its duration × $0.001 per second (twice the highest rate the spike saw). Before each paid call (model, Tavily or Sandbox) an upper-bound estimate is held against the scan's budget and replaced by the real amount afterwards, so parallel calls cannot overshoot.
- **Two hard caps, one per pool** (Token Factory, which includes Sandboxes; Tavily). The defaults are the build caps, $10 and $5, used by the local ledger. At deploy the hosted demo's caps are set to the balance left then minus a safety margin ($5 and $3), expected about $25 and $12.
- Unchanged from ADR-0014: each scan reserves its maximum budget ($0.75 and $0.40) before any paid call; rate limits of 3 live scans per hour and 10 per day per visitor, 30 per day overall, 2 at once; a kill switch; replays are never limited or charged.
- Every live scan and eval run prints what it spent and what is left.

### Alternatives considered

- Keeping the per-second guess: the real figure is now available, and a guess either wastes budget or under-counts.
- Not metering Sandboxes while they are free: the cap would silently stop being a cap if billing starts.
- One combined cap: one pool could run dry while the other still had budget (as in ADR-0014).

### Consequences

The cap tracks real spend more closely. The caps must be set at deploy from the balances left after building, which is a manual step recorded in the deployment guide.

## ADR-0023: Nemotron 3 Super with native tool calling only

Date: 2026-10-25
Status: Accepted

### Context

ADR-0002 chose Nemotron on Token Factory and kept two reasoning plug-ins (native tool calling and a JSON-mode loop) because tool calling was untested. The spike showed `nvidia/nemotron-3-super-120b-a12b` answers in under a second, tool calling works first time (automatic and forced), JSON mode works, and token usage is reported. Replies start with blank lines. LangSmith's `list_runs` is deprecated in favour of `client.runs.query()`.

### Decision

- All inference goes through one client fixed to `https://api.tokenfactory.nebius.com/v1/`; the default model is `nvidia/nemotron-3-super-120b-a12b`; the app refuses a model name without "nemotron".
- Only native tool calling is built. The final round forces the `submit_verdict` tool. The JSON-mode loop is not built; the strategy interface stays so it could be added without touching other code.
- Model text is stripped of leading and trailing whitespace before it is used or parsed.
- LangSmith runs are read with `client.runs.query()`; `list_runs` is banned by a lint rule.

### Alternatives considered

- Building the JSON-mode loop anyway: time spent on a path the spike showed is not needed.
- The cheaper `NVIDIA-Nemotron-3-Nano-30B-A3B` model: tool-calling quality drives the precision bar; switching is a one-line setting change if cost demands it.

### Consequences

Less code to build and test before the deadline. If tool calling ever regresses, a second strategy has to be written then.

## ADR-0024: Production operations: CI, JSON logs and spend alerts

Date: 2026-10-25
Status: Accepted

### Context

On 2026-10-25 the user added three production requirements (NFR-11 to NFR-13): automatic checks on every push and pull request with a status badge, structured logs that can be tied to a scan and its LangSmith trace without leaking secrets, and an alert when either credit pool runs low. The user declined an operations statistics endpoint, Sentry and Prometheus/Grafana.

### Decision

- **CI:** a GitHub Actions workflow runs ruff and the fast tests on every push and pull request. It needs no secrets, pins its actions to exact commits, and the README shows its badge. A test reads the workflow file, which adds `pyyaml` as a development-only dependency.
- **Logs:** Python's built-in logging with our own JSON formatter: one JSON object per line. Every line written during a scan carries its `scan_id` (passed along automatically between parallel tasks); every stage-end line carries `stage`, `duration_ms` and `outcome`; the scan's LangSmith trace carries the same `scan_id`. Every line passes through the existing secret redaction.
- **Spend alerts:** `GET /api/health` reports, per pool, the fraction of the budget left (0 to 1), never amounts or keys. A small stand-alone script run by the existing keepalive workflow every 6 hours fails, naming the pool, when any fraction is below 0.25, so GitHub emails the owner.

### Alternatives considered

- A logging library such as structlog or python-json-logger: a new dependency for something the standard library does.
- Sentry, Prometheus or Grafana: declined by the user.
- A separate alert workflow: duplicates the keepalive, which already calls the health check.
- Reporting dollar amounts in the health check: reveals the budget; fractions are enough.

### Consequences

Free and dependency-light. The health response gains a field the keepalive depends on, so it becomes part of the API contract. Log fields are tested, so new log paths must keep them.

## ADR-0025: Contract additions from the prototype review

Date: 2026-10-25
Status: Accepted

### Context

The accepted prototype raised contract questions for the freeze: the UI cannot show the live-scan limits; some errors have no fixed message; a Sandbox timeout can turn a whole report uncertain; the UI wants an evaluation figure only when one was really measured; the tool behind each step is mapped in the browser; and the UI should claim "Sandbox" only when one actually ran. Nothing has been recorded yet, so the report format can still change cheaply.

### Decision

- (a) **Accepted:** `GET /api/config` `limits` also returns `live_scans_per_client_hour` and `live_scans_per_client_day`.
- (b) **Accepted:** fixed messages for `scan_not_found`, `report_not_ready` and `request_invalid`; new codes `demo_not_found` (unknown demo) and `finding_not_found` (proof on an unknown finding), each with a fixed message.
- (c) **Confirmed:** if the analysis step times out, every finding that has no earlier reason becomes `uncertain` with "sandbox timeout". Without the code index no verdict can be backed by evidence, and the spec (AC-27) requires exactly this. The index timeout (120 s) and the size caps keep it rare.
- (d) **Accepted:** `GET /api/config` gains an optional `eval` object (`precision`, `n`, `run_date`, `model`), published only from a full scored eval run recorded in `eval/results/latest.json`; otherwise it is empty and the UI shows no figure.
- (e) **Accepted, as a list:** each report step and each step event carries `tools`, the tools whose results the step used (for example `tavily` and `nemotron` for research), because several steps use more than one tool.
- (f) **Rejected as a separate field:** a `sandbox_used` flag or per-step Sandbox timing. "Sandbox" appears in the `tools` of the analysis and resolve steps only when a real Sandbox operation returned, which is the flag; step timing already exists as start and finish times, and per-operation cost and duration go to the logs.
- The report schema version stays "1.0", since no report has been recorded or published yet.

### Alternatives considered

- A single `tool` string per step: cannot express steps that use two tools.
- Reusing `scan_not_found` for an unknown demo: misleading text for the user.
- Hard-coding the eval figure in the UI: risks showing a number that was never measured (AC-39).

### Consequences

The mock fixtures and the browser's step-to-tool mapping must be updated to the frozen models. The new fields are part of the expensive-to-change report and API contract.

## ADR-0026: Self-hosted Geist fonts under a strict CSP

Date: 2026-10-25
Status: Accepted

### Context

The accepted prototype uses the Geist and Geist Mono fonts, loaded from Google Fonts. The plan forbids CDNs and third-party resources in the UI, and the Content Security Policy only allows the page's own origin. Both fonts are published under the SIL Open Font License, which allows bundling them.

### Decision

The font files (woff2) are stored in `web/fonts/` with the licence text, declared in `web/css/tokens.css`, and served by our own app. The Google Fonts links are removed. The CSP stays restricted to the app's own origin, with an explicit `font-src 'self'`. A test fails if the page or stylesheets reference any external URL.

### Alternatives considered

- Allowing Google Fonts in the CSP: every visitor's browser would contact Google, the demo would depend on a third party during judging, and the policy would be looser.
- System fonts only: drops the accepted design.
- Installing the fonts through npm: needs a build step, which the frontend does not have.

### Consequences

A few hundred kilobytes of font files in the repository. The UI works without any outside network access.

## ADR-0027: Sandbox steps with network on never run repository code

Date: 2026-10-25
Status: Superseded by ADR-0032

### Context

ADR-0021 ran every step that touches repo files in the Token Factory Sandbox, as a non-root user, with the network switched on only to download the repo, compile its dependency list, or install dependencies for the proof. The security design review (round 1, 2026-10-25) pointed out that the design assumed those network-on runs could reach only GitHub and PyPI. They cannot be limited: the platform's network setting is a plain on/off switch with no list of allowed sites (confirmed in the platform spike). Compiling a dependency list can run the repo's own build scripts (`setup.py` or a build backend), and installing can build source packages. Either would let any anonymous visitor run their own code with full internet access on the owner's Nebius account, for example to scan or attack other sites, which could get the account suspended before the demo ends on 2026-12-15.

### Decision

- Unchanged from ADR-0021: code from an analyzed repository never runs anywhere except inside a Token Factory Sandbox, and everything the Sandbox returns is untrusted and strictly checked. `ContreeExecutor` is the only real executor; the SDK only imports the base image, adds our own agent with one fixed setup command, and untags images; every agent command runs through the REST API as uid/gid 65534 with the network explicitly on or off; the repo is downloaded once into a clean saved image and analysed with the network off; no secrets enter the Sandbox; a timeout always becomes "sandbox timeout"; there is no Docker fallback.
- New: **network on means the whole internet.** It stays on only for three steps, and none of them runs anything from the repo or from a source package:
  - downloading and unpacking the repo, done by our own agent only;
  - compiling the dependency list with `uv pip compile --no-build` (and `UV_NO_BUILD=1`), ignoring any uv settings in the repo, so no build script ever runs. A repo whose dependency list can only be found by building it is reported as unresolved with "dynamic metadata needs a build; not run";
  - installing dependencies for the proof from ready-made wheels only (`--only-binary :all:`, `--no-build`). A dependency with no wheel is not installed, and the proof says "not proven" with "uncertain: no wheel ...".
- The only step that runs repo code, the proof run, has the network off. The time limits of the network-on steps stay at 120, 180 and 300 seconds.

### Alternatives considered

- Accepting the risk with a lower compile quota (the reviewer's other option): fewer runs, but each one still gives a stranger internet-connected code execution on our account.
- Downloading source packages with the network on and building them later with it off: keeps more repos resolvable, but adds steps, time and complexity before the deadline; it can be added later.
- Not compiling at all: every repo without a lockfile would come out unresolved, including the many with a plain `pyproject.toml`.

### Consequences

The safety guarantee no longer depends on a network filter the platform does not have. Repos that declare dependencies only through `setup.py`, `setup.cfg` or dynamic metadata come out "incomplete" with a clear reason, and some proofs end "not proven". This rule is now part of the expensive-to-change Sandbox boundary. Platform feedback: an egress allowlist would let builds run safely.

## ADR-0028: Dependency resolution order, compiled without builds

Date: 2026-10-25
Status: Accepted, amended by ADR-0032 (`--no-sources` and sanitized compile input) and ADR-0034 (compile output file, authoritative uv error text)

### Context

ADR-0006 set the order for finding exact dependency versions and allowed `uv pip compile` on `setup.py` and `setup.cfg`, noting that compiling can execute repo code. ADR-0027 forbids running any repo code while the network is on, so compiling may no longer build anything.

### Decision

- Order: `uv.lock`, `poetry.lock`, `Pipfile.lock`, then a fully pinned root `requirements.txt`, then `uv pip compile --no-build` on a `pyproject.toml` whose dependencies are written out (not "dynamic"), a `requirements.in`, or an unpinned `requirements.txt`.
- A repo with only `setup.py` / `setup.cfg`, or with dynamic dependency metadata, or whose compile would need to build a package, is not built: its declared dependencies are unresolved with "dynamic metadata needs a build; not run".
- Unchanged from ADR-0006: lockfiles contribute every package including transitive ones, with groups; compiling covers runtime dependencies and their transitive ones only, and says so; anything unresolvable, or from git, a local path or a URL, is listed as unresolved with the reason and never looked up with a guessed version; names and versions must match the PEP 503 and PEP 440 formats. All of this runs in the Sandbox, because the repo's files are untrusted.

### Alternatives considered

- Building with the network on: the risk ADR-0027 removes.
- Reading `setup.py` with a parser instead of running it: unreliable, since `setup.py` is a program; left as a possible improvement.
- Supporting monorepo subfolders: more value, more time; still a limitation.

### Consequences

The resolution method ("locked" or "compiled") is always visible. Repos that need a build, and repos with only a `Pipfile`, come out unresolved with the reason; the report is "incomplete" rather than wrong.

## ADR-0029: One spend ledger for every spender, with allowances and a daily ceiling

Date: 2026-10-25
Status: Accepted

### Context

ADR-0015 kept spending in a private Hugging Face dataset for the hosted demo and in a local file otherwise; ADR-0022 metered actual cost, with $10 and $5 build caps locally and hosted caps set at deploy. The security review (round 1) found three gaps. After deploy, local scans, eval runs and demo recordings would still spend through the local file on the build caps, which the hosted cap cannot see, so total spending could exceed the real balance. Nothing limited spending per day, so a visitor rotating addresses could use up the hosted budget in about a day and leave live scans off for six weeks. And the hold for a model call assumed the answer stays within `max_tokens`, although reasoning tokens can go past it.

### Decision

- **Metering (unchanged from ADR-0022):** model spend = reported tokens × configured price; Sandbox spend = each operation's reported cost, else its duration × $0.001 per second. Every paid call first holds an upper-bound estimate against the scan's budget and replaces it with the real amount. Two pools: Token Factory (including Sandboxes) and Tavily.
- **Before deploy:** every spender writes to a local file ledger with the build caps ($10 and $5).
- **After deploy: one ledger for everyone.** The private Hugging Face dataset holds the caps (balance left at deploy minus $5 and $3 headroom, expected about $25 and $12) and an allowance per spender: `local` (command-line scans, eval runs, recordings; default $5 and $2) and `hosted` (the public demo; the rest). They are written once with `python -m pir ledger set-caps`. The app refuses to use the local file once the dataset is configured, and refuses to spend until caps are set. Each spender can only reserve within its own allowance, so together they can never pass the caps.
- **Safe concurrent writes:** each change is read → change → write, where the write names the version it read (`parent_commit`); if someone else wrote in between, the write is rejected and the change is re-applied to the fresh copy, up to three times. Inside one process a lock makes reservations and settlements take turns. If the ledger cannot be read or written, live scans pause (fail closed). Reservations left by a crash count as spent.
- **Daily ceiling for the hosted demo:** per pool, (allowance − spent) / days left until 2026-12-15, fixed at the first scan of each day. Unspent money rolls forward. Over the ceiling, visitors are told to retry after midnight UTC; replays still work. The global limit falls from 30 to 10 live scans a day; per visitor 3 an hour and 10 a day, 2 at once; per-scan reservations $0.75 and $0.40; the kill switch stays.
- **Model-call holds:** requests send `max_completion_tokens`, which also bounds reasoning tokens. If Token Factory does not honour it (checked in the first model-client task), the hold uses a worst-case token budget instead. If a call still costs more than its hold, that scan may spend nothing more.

### Alternatives considered

- Separate ledgers with the remaining balance split between them: the reviewer's concern stays, because two records must be kept consistent by hand.
- One shared ledger without allowances: a long eval run could starve the demo, or the demo the eval.
- Caps in each process's settings: settings on the laptop and the Space can drift apart; storing them in the ledger gives one source of truth.
- A database such as Upstash Redis: a new vendor and account, while the Hugging Face dataset already supports safe conditional writes.
- Forbidding local spending after deploy: blocks the eval runs that measure precision.

### Consequences

Spending stays within the real balance whoever spends it, and the hosted budget lasts until 2026-12-15. Each scan makes two or more dataset writes; the commit rate is checked at deploy. After deploy the local `.env` needs `HF_TOKEN` and the dataset name. Busy days end earlier for visitors, with replays still available.

## ADR-0030: Images pinned by digest and hash-checked installs

Date: 2026-10-25
Status: Accepted (amended by ADR-0033 for the Sandbox base image only; the image-contents rule recorded with it is partly superseded by ADR-0036 for `eval/results/latest.json`)

### Context

The security review (round 1) noted that the Sandbox base image and the Space's `python:3.13-slim` image are referenced by tags, which their publishers can move, and that the Space installs Python packages without checking their hashes. A replaced image or package would run with our secrets on the Space or inside our Sandbox runs.

### Decision

- Both images are referenced by their exact `@sha256:` digest; the app refuses a Sandbox image setting without one.
- The Space installs Python packages only from a committed `requirements.lock` that lists a hash for every file, using `pip install --require-hashes`. The lock is generated with `uv pip compile --generate-hashes`, so `uv` becomes a development-only dependency. A test checks that every runtime dependency appears in the lock with a hash.

### Alternatives considered

- pip-tools for the lock file: works, but adds a second tool when uv is already the resolver used in the Sandbox.
- Writing hashes by hand: error-prone.
- Keeping tags: simplest, but the image can change underneath us.

### Consequences

Updating an image or a dependency becomes a deliberate edit (new digest or regenerated lock). The first Sandbox task checks that the SDK accepts a digest reference.

## ADR-0031: Tavily research with exact source URLs and a 7-day cache

Date: 2026-10-25
Status: Accepted

### Context

ADR-0009 cached research results per advisory for 30 days. The security review (round 1) noted that a poisoned search result would then be shown to every visitor for a month.

### Decision

- Unchanged from ADR-0009: for each advisory, one Tavily search (basic depth by default), then one Nemotron extraction that names the vulnerable function from the OSV text and the snippets; a name is kept only if it has the shape of a Python path and its last part appears word for word in the supplied text; evidence URLs must be among the URLs returned; links are stored exactly as returned; if no function is identified the finding is `uncertain` with "vulnerable function not identified".
- Changed: research results are cached for **7 days**, and only under advisory IDs that pass a strict format check.

### Alternatives considered

- 30 days: the exposure the reviewer flagged.
- 1 day or no cache: repeated advisories would cost Tavily credits on almost every scan for little gain.

### Consequences

A bad result is served for at most a week, and its source link stays visible on the finding so readers can judge it. An advisory's vulnerable function rarely changes within a week, and the hosted cache is lost on every restart anyway, so the extra cost is at most 1 to 2 credits per advisory per week.

## ADR-0032: No repository file influences a network-on step

Date: 2026-10-25
Status: Accepted

### Context

ADR-0027 made sure no build script runs while the Sandbox has internet access. The security design review (round 2, 2026-10-25) found a gap: the dependency tool `uv` was started inside the repository's folder, and before doing anything it looks around its current folder for a Python program to use (a `.venv` folder, a `.python-version` file). A repository could ship a fake `.venv/bin/python` and so have its own program run, with full internet access, on the owner's account. The review also found that a repository could make uv contact servers of its choosing, through `[tool.uv.sources]` in `pyproject.toml` or through option lines such as `--extra-index-url` in a requirements file (no code runs that way, but the repo picks the hosts and can invent versions). A third, smaller point: a single model call that costs more than its reserved amount can push spending a little past an allowance.

### Decision

This ADR replaces ADR-0027's network-on rule with a stronger **invariant: while networking is on, nothing from the repository executes and no file the repository controls influences any tool run** (as working folder, configuration, interpreter, environment or input). Everything else in ADR-0027 stays as it was. Concretely:

- Every uv call (dependency compile and proof install) starts in an empty folder outside the repository (`/tmp/uvwd`, created fresh by our agent; the step fails if it already exists), is told exactly which Python to use (`--python /usr/local/bin/python3.13`), may not download a Python (`UV_PYTHON_DOWNLOADS=never`), and gets a fixed environment with `VIRTUAL_ENV` and `CONDA_PREFIX` unset.
- **Amends ADR-0028:** compile also runs with `--no-sources`, and uv never reads the repository's manifest. Our agent reads it as plain data, keeps only ordinary package requirements, and writes them to a separate file (`/tmp/pirin/requirements.in`) that is passed to uv by its full path. Option lines (`--index-url`, `--extra-index-url`, `--find-links`, `-e`, `-r`, `-c` and any other line starting with `-`) are dropped and noted; `-r` includes inside the repo are read by the agent itself, as for pinned requirements files. Requirements that point at a web address, a local path or a version-control repository are dropped and reported unresolved with ADR-0028's existing reason "non-registry source", so no new reason code is needed. Both uv calls also name the package index explicitly (`https://pypi.org/simple`).
- Tests: a fixture repository with a booby-trapped `.venv/bin/python`, `pyvenv.cfg`, a `.pth` file and `.python-version` (plus uv settings and hostile requirement lines); fast tests check uv's working folder, interpreter flag, environment and sanitized input; a live test proves in the real Sandbox that the trap never fires and gates live compile and proof install.
- Related round-2 items (ADR-0029 is unchanged in substance): `ledger set-caps` keeps at least $5 (Token Factory) and $3 (Tavily) between the caps and the real balance, and refuses to write caps until it has proved on the live ledger that Hugging Face rejects a write based on an out-of-date version. Until that proof passes, the hosted ledger refuses all spending and live scans stay paused.

**Audit of every network-on step** (working folder, configuration, interpreter, environment; all pass after this ADR):

| Step | Working folder | Configuration | Interpreter | Environment and inputs |
|---|---|---|---|---|
| `fetch` (our agent only) | `/`; the repo does not exist yet | Python `-I -S` reads no `PYTHON*` variable, `site`, `.pth` or user files | `/usr/local/bin/python3.13` by full path, from the pinned base image | `env -i` fixed set, no proxy variables; URL built from validated parts; extraction confined to `/work/repo` |
| `resolve --compile` (uv) | fresh empty `/tmp/uvwd` | `--no-config` (no `uv.toml`, `[tool.uv]`, user or system files), `--no-sources` | `--python /usr/local/bin/python3.13`, no downloads; no `.venv`, `.python-version`, `VIRTUAL_ENV`, `CONDA_PREFIX` or `PATH` search | fixed set; input is the agent's sanitized file outside the repo; `--index-url` fixed; `--no-build`; `HOME` and cache under `/tmp`, out of the repo's reach |
| `proof-install` (uv) | fresh empty `/tmp/uvwd` | as compile | as compile | fixed set; each `name==version` built by the agent from validated pins after `--`; `--only-binary :all:`, `--no-build`, `--no-deps`; target `/work/deps`, outside the repo |

### Alternatives considered

- Keeping the repository folder as uv's working folder and only adding `--python`: closes the known trick but leaves uv free to read other files there in future versions; an empty folder removes the whole class.
- Letting uv read the repository's `pyproject.toml` or requirements file with more "ignore" flags: each new uv feature could reopen the hole; having our agent rewrite the input is simpler to check.
- Rejecting any repository that contains a `.venv` folder: easy to dodge (`.python-version`, symlinks) and it would refuse harmless repos.

### Consequences

uv can no longer be steered by anything inside a repository, and the guarantee is checked by fast tests and a live canary. Repos that rely on extra package indexes, local paths or git dependencies show those dependencies as unresolved with a clear reason, and constraint files are ignored (noted in the report). The rule is part of the expensive-to-change Sandbox boundary; any new network-on step must pass the same audit.

## ADR-0033: Sandbox base image imported by version tag and checked against a reviewed fingerprint

Date: 2026-10-25
Status: Accepted (approved by the user on 2026-10-25; amends ADR-0030 for the Sandbox image only, and the Space image stays pinned by digest). Open item: the version tag `ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim` must be confirmed and its index digest recorded during the Sandbox-image rework. The contract details are in ARCHITECTURE sections 7.1 and 7.2.
Amended 2026-10-25 (approved by the user): the fingerprint and the tag's digest are measured on a GitHub Actions runner instead of with Docker on the developer's machine. This changes only where the measurement runs, not what is checked; see "Amendment 2026-10-25" below and ARCHITECTURE sections 7.2 and 19.1.

### Context

ADR-0030 says the Sandbox's starting image must be named by its exact fingerprint (an `@sha256:` digest), so nobody can swap it under us by moving a tag. The live check on 2026-10-25  found that the Sandbox's image importer (contree-sdk 0.3.6 `images.oci`) answers `HTTP 404 page not found` for every digest reference it was given: the ghcr.io index digest, the amd64 manifest digest, with or without a tag, and docker.io as well. Plain tags import fine. Without a `tag=` override the SDK also invents a local name containing `@`, which then fails with 400. As written, live scans cannot start. The demo is on 2026-10-30.

What a swapped image could do: it runs only inside the Sandbox, which holds no secrets, so it cannot steal keys. It could falsify every scan's report, and its Python and uv run our network-on steps (ADR-0032), so it could use those steps (each capped at 120 to 300 seconds and metered) to send traffic from our account. The Python and uv in the image are therefore part of what ADR-0032's guarantee relies on.

### Decision

- **Import by a version tag.** The Sandbox imports `ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim` (astral publishes one tag per uv release; the floating `python3.13-bookworm-slim` tag moves with every release and would trip the check below every week), always with the `tag=` override `pir-base/sha256-<digest hex>`.
- **Configuration keeps the digest.** `SANDBOX_BASE_IMAGE` stays `<tag>@sha256:<digest>` and the settings check is unchanged: the digest records which reviewed build the tag must deliver. The executor imports only the part before `@`. The developer first confirms with `docker buildx imagetools inspect` that the version tag exists and records its index digest (expected to equal `531f855b...44ca`; if not, the version tag's digest is used). (Amended 2026-10-25: the fingerprint workflow does this check; see below.)
- **A reviewed fingerprint, measured outside the Sandbox.** A new agent command `selfcheck` (network off, no arguments) prints: the Python version, the output of `uv --version`, the sha256 of `/usr/local/bin/python3.13`, `/usr/local/lib/libpython3.13.so.1.0`, `/usr/local/bin/uv`, `/usr/local/bin/uvx`, `/lib64/ld-linux-x86-64.so.2` and `/lib/x86_64-linux-gnu/libc.so.6` (a missing file is recorded as `"missing"`), and one hash over every file and link under `/usr/local/lib/python3.13` (sorted relative paths with their content hashes). The developer runs it once with Docker on the laptop, where pulling by digest works: `docker run --rm --platform linux/amd64 --network none --user 65534:65534 -v <dir with agent.pyz>:/opt/pir:ro <SANDBOX_BASE_IMAGE> /usr/local/bin/python3.13 -I -S /opt/pir/agent.pyz selfcheck --args-b64 e30=`. The decoded result, plus the digest it came from, is committed as `src/pir/executor/base_image_fingerprint.json`. (Amended 2026-10-25: the same command runs on a GitHub Actions runner; see below.)
- **Checked on the image actually used.** In every process, right after `prepare` and before the first `fetch` or `unpack`, `SandboxAgentClient` runs `selfcheck` with network off from the prepared image (cached with `prepare`, so once per process) and requires the result to equal the committed fingerprint exactly, and the fingerprint's digest to equal the digest in `SANDBOX_BASE_IMAGE`. Any difference raises `SandboxFailed("selfcheck", "base image does not match the reviewed fingerprint")`: the scan reports the Sandbox as unavailable and nothing else runs (fail closed). `FakeExecutor` skips the check.
- The live test `test_images_oci_accepts_digest` is replaced by `test_base_image_fingerprint_matches` (import by tag, `selfcheck` equals the committed fingerprint).
- Changing the image (new uv or Python) is a deliberate edit, as under ADR-0030: new tag and digest, a re-measured fingerprint, a re-run of the live gates and a re-recorded Sandbox uv error-text fixture (ADR-0034).
- The digest-import failure is reported to Nebius. When digest imports work, the executor goes back to importing by digest and keeps `selfcheck` as a cheap second check (a new ADR).

### Alternatives considered

- **(a) Check the tag's digest from the host through the ghcr.io registry API, then import by tag** (about half a day; adds `ghcr.io` to the host allowlist): there is a gap between the check and the Sandbox's own download in which the tag can move; it checks the registry, not the image the platform actually runs (the platform reuses earlier imports); and nothing proves afterwards what was imported.
- **(b) A one-time setup step that checks the digest, imports by tag and stores the platform's image ID in configuration** (about one day; new CLI command and setting): the stored ID cannot change, but the same gap exists at setup and the ID's lifetime on the platform is unknown, so it could vanish during the demo.
- **(c) Import once by tag, verify inside the Sandbox, then pin the platform image ID:** the same check as the decision, but it adds (b)'s storage and lifetime risk; checking per process on the image in use removes both at the cost of one short Sandbox run per process.
- **Accept a tag without any check** (minutes): drops the security review's finding 7 for the Sandbox image.
- **Wait for Nebius to fix digest imports:** no date; misses the demo.
- **Copy the image into our own registry:** tags there can move too, it needs another account, and digest imports would still fail.

### Consequences

- **Security:** the files that run our network-on steps are proven equal to those of the reviewed build, on the very image every scan uses, so there is no gap between check and use. Limit: the check runs on the image's own Python, so a deliberately crafted malicious image could lie about its own files. That would need a compromise of astral's publishing aimed at this check, and the harm stays bounded as described above. This is weaker than a working digest import and much stronger than a bare tag.
- **Cost and time:** one Sandbox run of a few seconds per process (well under $0.01, metered); about half a day to one day of development; fits the 2026-10-30 demo.
- **Availability:** if astral re-publishes the version tag with different contents, live scans stop with "Sandbox unavailable" until the image is re-pinned; demo replays keep working.

### Amendment 2026-10-25: fingerprint measured on GitHub Actions

Approved by the user on 2026-10-25. Everything above stays in force except where the measurement runs.

**Context.** The developer's laptop has CPU virtualization turned off in its firmware, so Docker Desktop cannot run there (and neither can WSL2 or Podman, which need the same feature). The fingerprint still has to be measured outside the Sandbox, from the image pulled by its exact digest.

**Decision.**
- A new workflow `.github/workflows/fingerprint.yml` measures the fingerprint on a GitHub-hosted Ubuntu runner, where Docker is available and pulling by digest works. It starts only when someone presses "Run" (`workflow_dispatch`, no inputs), can only read the repository (`permissions: contents: read`), uses no secrets, pins every action to a full commit SHA, and puts no `${{ }}` expression inside any shell command.
- The image is always the default `SANDBOX_BASE_IMAGE` written in the code (`Settings`), never a value typed in when starting the run, so the workflow cannot be used to measure an arbitrary image. Before measuring, it confirms that the version tag still points to the recorded digest and stops if not.
- It runs the same `docker run ... selfcheck` command as above (network off, unprivileged user, agent mounted read-only), decodes the result with the project's own decoder, writes the fingerprint file in the committed shape and uploads it as a download named `base-image-fingerprint`.
- The workflow never commits or pushes. The developer downloads the file (`gh run download`), reviews it and commits it as `src/pir/executor/base_image_fingerprint.json`.
- The automated workflow checks for continuous integration (AC-71, AC-72: runs on every push and pull request) apply to `ci.yml` only; `fingerprint.yml` has its own checks (manual only, read-only, SHA-pinned, no secrets, no `${{` in commands).
- Docker is no longer a tool the developer's machine needs.

**Alternatives considered.**
- Turn virtualization on in the firmware: a change to the user's own machine that may not be possible, and the measurement would still depend on one laptop; the workflow can be re-run by anyone with access.
- Measure inside the Sandbox: circular, because the Sandbox cannot import by digest, so nothing would prove the measured image is the reviewed build.
- Download the image layers by digest and hash the files on the laptop without running them: no Docker needed, but the Python and uv versions require running the image, and a second hashing implementation could drift from `selfcheck`.
- Let the workflow commit the file itself: needs write access and skips the human review.
- Let the person starting the run type the image name: the workflow could then be pointed at any image.
- A rented cloud machine with Docker: needs another account and is harder to reproduce.

**Consequences.**
- **Security:** the measurement is now trusted to a GitHub-hosted runner and the pinned actions instead of the laptop. The image is still pulled by its digest, so its contents cannot be swapped; the run has no secrets and cannot write to the repository, so a faulty step can at worst produce a wrong file, which the developer reviews and which the live test then compares with the Sandbox (a mismatch stops live scans).
- **Cost and time:** a few runner minutes per image change, within GitHub's free allowance; the repository must be on GitHub with Actions enabled, and the workflow must be on the default branch before it can be started.

## ADR-0034: Sandbox contract corrections from the live platform checks

Date: 2026-10-25
Status: Accepted (amends ADR-0028 for the compile output and the authoritative uv error text; corrects ARCHITECTURE sections 7.1, 7.2, 13 and 22.1)

### Context

The first runs against the real Nebius Sandbox (2026-10-25) showed five places where the frozen design did not match how the tools actually behave. None of them changes the safety guarantees; they make the contract describe what the platform and uv really do.

### Decision

1. **Compile output goes to a file.** `uv pip compile ... --output-file -` does not print to the screen in uv 0.13.5 (development) or uv 0.9.30 (Sandbox image): nothing appears and a file literally named `-` is written. The compile command now ends with `--output-file /tmp/pirin/requirements.txt` (a fresh folder created by our agent, so no old file can steer the result). After a successful run the agent reads only that file, with the existing size cap; a missing or oversized file is "compile failed: no output". The workaround that also looked for a file named `-` is removed.
2. **Output size caps.** The Sandbox accepts at most 10 MiB (10485760 bytes) for `truncate_output_at`; 11 MiB is refused. It stays at 10 MiB. The largest result our agent may print is lowered from 10 MiB to 8 MiB (8388608 bytes of base64) on both sides; the agent checks it before printing and, if over, prints nothing and exits with code 3 ("limit exceeded"), and it writes at most 1 MiB of log lines. A valid result can then never be cut off by the platform. The 50 MiB limit after decompression is unchanged.
3. **Spend refusals are exceptions in the executor.** The spend meter's `SpendDenied` is a plain value, not an error. The executor turns it into `ExecutorSpendDenied`, an error carrying the original value as `.denied`, raised before any Sandbox request; the Sandbox client lets it pass through, and the scan pipeline maps its reason to "spend cap reached" or "scan budget reached". The model and search clients keep handling the plain value.
4. **Preparing the image.** In the async SDK, `image.run(...)` is awaited directly and returns the saved image; there is no separate `op.wait()` step.
5. **Which uv error text counts.** The uv inside the pinned Sandbox image (0.9.30) is authoritative, because it is the one that runs in real scans. The live test must match the Sandbox-image fixture `tests/fixtures/http/contree/uv_no_build_sdist.sandbox.stderr.txt` exactly; the development fixture from uv 0.13.5 stays as a second test case. The detector for "a build would be needed" relies only on the sentences both versions share, and a unit test runs it on both. Neither the image nor the development pin changes: the development uv only regenerates `requirements.lock` on the laptop and never runs in the Sandbox, and moving the image would mean re-running every live gate before the demo.

### Alternatives considered

- Dropping `--output-file` and reading the screen output: shorter, but not checked live, and it depends on how uv treats `--quiet`; a file path in our own folder is unambiguous.
- Keeping a 10 MiB result cap with 10 MiB truncation: a result near the cap would be cut and wrongly reported as a Sandbox failure.
- Making `SpendDenied` itself an exception: changes the spend meter (done) and every caller of it, for no gain.
- Matching uv error text across versions exactly: impossible, as the layouts differ; aligning the development pin to 0.9.30 could change the lock file format for no safety benefit.

### Consequences

The contract now matches the platform. The compile and parsing code change in small, testable ways; the protocol cap constant changes in two files; the scan pipeline must catch `ExecutorSpendDenied` for Sandbox steps. Any future change of the Sandbox image also re-records the Sandbox uv error-text fixture.

## ADR-0035: Fixed prompt-token overhead in the LLM spend hold

Date: 2026-10-25
Status: Accepted (amends the LLM hold estimate of ADR-0029; corrects ARCHITECTURE sections 7.5 and 11.1)

### Context

Before every model call the spend meter sets aside ("holds") an upper-bound estimate of its cost. The prompt part of that estimate was the prompt length in characters divided by 2.5. The first live calls (2026-10-25) showed that Token Factory's chat template adds about 20 prompt tokens to every call that this does not cover: a 43-character prompt was billed 32 prompt tokens against an estimate of 18. A call that uses its full answer allowance can then cost more than its hold; the scan budget then marks the scan as overrun and refuses all later spending in that scan, so reasoning ends early.

### Decision

The hold's prompt tokens become `ceil(chars / CHARS_PER_TOKEN) + PROMPT_OVERHEAD_TOKENS`, with `CHARS_PER_TOKEN = 2.5` and `PROMPT_OVERHEAD_TOKENS = 64`, both module constants in `pir/llm.py`. The overhead applies in both modes: the normal one (answer allowance `max_tokens`) and the fallback one (answer allowance `LLM_HOLD_COMPLETION_TOKENS`). 64 is about three times the observed overhead, leaving room for tool definitions and per-message wrapping. What is recorded after the call (the billed usage) does not change.

### Alternatives considered

- An environment setting for the overhead: the project keeps the other estimate factor (`CHARS_PER_TOKEN`) as a constant, and a setting adds a knob nobody needs to turn; a code change with a test is the right way to adjust it.
- A smaller overhead (for example 24): fits the one measurement but leaves no margin for longer conversations with tools.
- Raising the overrun tolerance instead: would weaken the cap guarantee rather than fix the estimate.

### Consequences

Each hold grows by 64 prompt tokens, a fraction of a cent at the configured prices, so the budget slightly over-reserves during a scan and releases the difference when the call is recorded. Overruns caused by the template overhead stop. The LLM client's hold calculation and its unit test change; no other contract changes.

## ADR-0036: Ship the published eval summary in the Space image

Date: 2026-10-26
Status: Accepted (partly supersedes the image-contents rule recorded with ADR-0030, for `eval/results/latest.json` only)

### Context

The security review (round 1) asked that the Space image never contain secrets or local output, and the hosting section of [ARCHITECTURE.md](ARCHITECTURE.md) (section 18), written alongside ADR-0030, turned that into a rule: `.dockerignore` excludes all of `eval/results/`. The first Cloud Run build step (commit 9a013b7) followed the rule. But the web page's evaluation figure ("precision on N cases") is read at runtime from `eval/results/latest.json`, so the deployed demo would show no figure at all, even after a scored evaluation run. The rule's own text in ADR-0030 is about pinning images and checking package hashes; the blanket exclusion of `eval/results/` lives in section 18 and the security review's test list.

`eval/results/latest.json` is different from the rest of that folder. It is committed to git, so it is already public with the repository. It holds only aggregate figures (the `EvalRunSummary` model: whether the run was scored, run date, model name, LangSmith experiment name, case count, errored count, precision, recall, abstention rate, the bar and whether it was met). It holds no per-case traces, no repository code, no prompts or model text, and no keys. The other files in `eval/results/` (timestamped per-run JSON and Markdown) are gitignored and may hold per-case detail; `latest.md` is committed but the app does not read it.

### Decision

- The Space image contains exactly one file from `eval/results/`: `latest.json`, at `/app/eval/results/latest.json`, where the app looks for it.
- `.dockerignore` keeps excluding `eval/results/` and adds the exception `!eval/results/latest.json` after it. Every other file in that folder, including `latest.md` and the timestamped run files, stays out of the build context.
- The Dockerfile copies that single file by name (`COPY eval/results/latest.json ./eval/results/latest.json`); no other `eval/` path is copied.
- Tests check that the exception is the only re-included path under `eval/results/`, that other result files stay excluded, and that the Dockerfile copies only that one file from `eval/`.
- Everything else in ADR-0030 (digest-pinned images, hash-checked installs) and every other `.dockerignore` entry is unchanged.

### Alternatives considered

- Keep excluding the whole folder: the deployed demo would never show the evaluation figure, which is the project's main credibility signal.
- Move the summary out of `eval/results/` (for example into `web/` or `demo/`): changes a path the evaluation runner, the "Expensive to change" list and the UI contract already depend on, for no security gain.
- Bake the figure into the page or into an environment variable at build time: a second copy of the same numbers that can drift from the committed file.
- Re-include the whole folder: would let per-run files with per-case detail into the image.

### Consequences

The deployed demo shows the evaluation figure whenever the committed `latest.json` is a scored run with a precision value, and shows none otherwise, as before. Updating the figure on the Space means committing a new `latest.json` and rebuilding the image. The image gains one small file that is already public; nothing secret or per-case enters it. The `.dockerignore` and Dockerfile packaging tests change to match.

## ADR-0037: Call-path matching through default-argument equivalences and inferred source roots

Date: 2026-10-26
Status: Accepted

### Context

In the M1 user acceptance review, the tool said "no reachable path detected" for PyRL, although its `main.py` calls `yaml.load(f)` on PyYAML 5.3 every time it starts. The first suspicion was that code inside `if __name__ == "__main__":` does not count as an entry point. The code shows that it does: every module-level statement, including the body of that `if` and any `with` or `try` inside it, belongs to the module's entry point (`main_block`). A recorded run confirms it: the eval case for the same call under advisory GHSA-6757-jp84-gxfx was judged "likely reachable" with a valid path.

The real causes are two others.

1. **The vulnerable call is spelled differently.** The research step names the vulnerable functions from the advisory text. For the PyYAML advisories it finds `yaml.full_load` and `yaml.FullLoader`. PyRL never writes either name; it writes `yaml.load(f)` without a `Loader`, and in PyYAML 5.1 to 5.4.1 that call uses `FullLoader` by default. Matching by exact name cannot see this, so the call-path search found no use. This explains both the demo miss and the eval miss `pyrl-yaml-load-default-loader-14343` (the twin case passed only because that run's research happened to name `yaml.load`).
2. **The project adds its own folder to the import path.** In PlaneSweepPose the vulnerable call sits in `lib/core/config.py`, and the scripts import it as `core.config` after a helper adds `lib/` to Python's search path. The index names the file `lib.core.config`, so the import `core.config` links to nothing, and the chain from the script's entry point stops there. The tool reported "no path within depth 8". This cause is inferred from the repository's layout and the recorded finding; it is confirmed with a fixture of the same shape and with the re-recorded demo.

The eval miss `yaml-dynamic-getattr` has a third cause (the loader is chosen by name at run time) and is not addressed here.

### Decision

- **Entry points stay as they are** and are now written down in [ARCHITECTURE.md](ARCHITECTURE.md) section 6.3. A regression test with PyRL's shape (a call inside `if __name__ == "__main__":`, nested in `with` and `try`, and a helper function called from it) locks that in.
- **Default-argument equivalences.** The index records, for each call of a watched library, how many positional arguments it passes and which keyword names. A small reviewed table says when a call with a missing argument is the same as another function. Its first rows: on PyYAML 5.1 up to 6.0, `yaml.load` and `yaml.load_all` without a `Loader` count as uses of `yaml.full_load` / `yaml.full_load_all` and `yaml.FullLoader`. The call-path search and the verdict check both use the same function to apply the table, so a path the search returns is accepted by the check, and a "no reachable path" answer is downgraded to "uncertain" when such a call exists. A call that passes any `Loader`, or that uses `*args` or `**kwargs`, never matches.
- **Inferred source roots.** When an import matches no file in the repository, it is linked to the one repository module whose name ends with it (for example `core.config` to `lib.core.config`). This happens only if the repository itself changes Python's import path in its own code, exactly one module matches, and the name is neither a top-level module of the repository nor the vulnerable library. Links made this way count as heuristic, so a path through one is reported with at most medium confidence and says so.
- No new dependency; the agent's protocol version stays 1, because the new index fields are optional.

### Alternatives considered

- **Treat more code as an entry point** (console scripts declared in `pyproject.toml`, `setup.cfg` or `setup.py`, or every public function): would not fix either miss, and "every public function" would invent paths that never run, which threatens the precision bar. Console scripts are a real but unobserved gap; deferred.
- **Ask the research step to also name `yaml.load`:** the model's answer varies between runs, and a bare `yaml.load` symbol would also match safe calls such as `yaml.load(f, Loader=SafeLoader)` (the co2tools case), turning a correct "no path" into a false "reachable".
- **Mark every `yaml.load` call as vulnerable:** the same false positive, without looking at the `Loader` argument.
- **Read the folder added to the import path from the `sys.path` call:** the path is usually built at run time (`os.path.join(this_dir, "..", "lib")`), so static reading is fragile; the unique-suffix rule with the presence check is simpler and conservative.
- **Raise the search depth above 8:** the depth was not the cause.

### Consequences

PyRL's `yaml.load(f)` is found for both PyYAML advisories, so a demo can show "likely reachable" with a real call path. Repositories laid out like PlaneSweepPose get call paths through their own helper modules, at medium confidence at most. The index model gains two optional fields; the ast tool is built per finding with the package and version; the verdict check uses the shared matcher. The three demo replays and the published eval result must be re-recorded to show the change. New library defaults need a new reviewed table row with a test. A wrong suffix link could in principle create a false path; it is limited by the conditions above, flagged as heuristic, and guarded by a golden-index test showing that the eval fixtures and the canary are indexed exactly as before.

Note (2026-10-26): the reasoning prompt now tells the model that a call-site item with an `implied_by` field is a use of the vulnerable symbol through a library default (on PyYAML 5.1 to 5.4, `yaml.load(f)` without a `Loader` uses `yaml.FullLoader`), so it can copy such a path into a "likely reachable" verdict (commit c5c5d65; how research names the symbols is in [ARCHITECTURE.md](ARCHITECTURE.md) section 7.4).

## ADR-0038: A LangSmith run per pipeline step, finding and tool call

Date: 2026-10-26
Status: Accepted

### Context

[ARCHITECTURE.md](ARCHITECTURE.md) promises that each eval case trace in LangSmith has child runs per step, LLM call and tool call. In the M1 user acceptance review the trace showed only the case, the scan and a flat list of model calls: no run for the six pipeline steps and none for the code-index tool calls the model made. The test for trace correlation (AC-74) checked only that the scan's trace carries its `scan_id`, so the gap went unnoticed. Without the tree, a reviewer cannot see which step was slow or which tool answers the model relied on, which is the point of the traces for judges and for debugging.

### Decision

- Beneath the scan run, the pipeline opens one run per step (`pir.step.resolve`, `osv`, `research`, `analysis`, `reasoning`, `report`), skipped steps included, in pipeline order.
- Inside the research step, one run per advisory researched, holding the Tavily search as a tool run (none when the cached answer is used) and the model's extraction call.
- Inside the reasoning step, one run per finding, holding its model calls and one tool run per code-index tool call, named after the tool, in call order. Tool runs record the arguments and the size of the answer, not the answer itself.
- The exact tree is in [ARCHITECTURE.md](ARCHITECTURE.md) section 19.2 and is tested offline with the fake LangSmith client, using the real LLM client against a recorded fake Token Factory so the model-call runs appear.
- With tracing off, nothing changes; tracing never changes a report. The same redaction applies to every run.

### Alternatives considered

- **Keep the flat trace and fix the documentation:** removes a promise that judges and the eval review rely on.
- **Separate traces per step linked by metadata:** harder to read in LangSmith and breaks the one-trace-per-case read-back.
- **A run for every Sandbox and OSV call as well:** more detail than the review needs; the step runs already show their duration and outcome. Can be added later without a contract change.
- **Record full tool answers in tool runs:** the same code snippets already appear in the model's input; recording them twice adds volume without new information.

### Consequences

Each eval case and live scan shows a readable tree in LangSmith. The tracing helpers, the pipeline's step runner, the researcher and the tool-calling loop change; reports, logs and the API do not. A new acceptance criterion tests the tree's shape; one eval run is needed to confirm it in LangSmith.

## ADR-0039: Call-chain proof runs main-block entries as scripts and stubs the call the repository makes

Date: 2026-10-26
Status: Accepted

### Context

The call-chain proof ([ADR-0018](#adr-0018-call-chain-proof-with-a-recording-stub-gated-and-cuttable), [ARCHITECTURE.md](ARCHITECTURE.md) section 16) was written for entries that are functions: it imports the entry's module and calls the function with placeholder values. The flagship demo, PyRL, has a different entry. Its validated call path starts at `main:<module>`, whose kind is `main_block` ([ADR-0037](#adr-0037-call-path-matching-through-default-argument-equivalences-and-inferred-source-roots)): the vulnerable call sits inside `if __name__ == "__main__":`. Importing a file does not run that block, so the proof would always say "not proven" for the demo the user wants to show. The user approved building the proof so that it works for this demo (plan approved 2026-10-26).

Two more details of PyRL would also defeat the proof as written:

1. **The repository calls a different name than the vulnerable symbol.** PyRL calls `yaml.load(f)`; the path was found because, on PyYAML 5.3, that call means `yaml.FullLoader` (ADR-0037). A stub on `yaml.full_load` or `yaml.FullLoader` would never be called by name, and a stub on any `yaml.load` call would also fire for safe calls that pass a `Loader`.
2. **The script keeps running after the vulnerable call.** PyRL loads its settings and then trains. With the stub returning a placeholder, the script could run past the 60-second limit, and the proof would report a timeout even though the call was reached.

### Decision

- **Main-block entries run as a script.** When the entry is a whole module (`<module>`) and the file has a top-level `if __name__ == "__main__":` guard (checked inside the Sandbox with the same code-reading check as the index; nothing runs for the check), the proof runs the file with Python's `runpy.run_path(<file>, run_name="__main__")`, the way `python <file>` runs it: the file's folder first on the import path, the working folder at the repository root, `sys.argv` set to just the file's path, and an empty standard input. Other whole-module entries are imported, which runs their module-level code. Function entries are unchanged.
- **The stub sits on the call the repository makes.** The proof stubs the name in the last step of the call path (for PyRL, `yaml.load`). When that step was matched only through a default-argument equivalence, the proof also names that exact line and counts only calls made from it, because only that line is known from the code to leave the argument out.
- **The first recorded call ends the run.** The stub records the repository file and line that called it, then stops the script. A watchdog shortly before the time limit reports whatever was recorded, so a reached call stays "proven" even when the script ignores the stop.
- **Safety and limits are unchanged.** The run is the existing `proof-run` step: same image with the pinned wheels, network off, no secrets, the same 60-second limit and the same recording stub. No command-line arguments, settings or input are invented, and no exploit payloads are passed (only the harness's own placeholders).
- **Every ending is a stated result, never a crash.** A script that exits (`SystemExit`) ends the run; if it exited before the call it is "not proven" with its exit code. An `argparse` failure on missing required arguments is "not proven" with the reason that the script needs command-line arguments the proof does not supply. Any other error is "not proven" with the error's class name. Text the script prints is never copied into a reason. The harness always writes exactly one result and then ends its own process, and the server treats any proof failure as "not proven", so the scan, the job and the verdict are never affected.
- No new dependency; the agent's protocol version stays 1. The `proof-run` step gains an optional `--site <file>:<line>` argument for the equivalence case.

### Alternatives considered

- **Import the module and call `main()` if one exists:** many scripts, PyRL among them, keep their logic in the guarded block itself, so there is nothing to call.
- **Copy the guarded block into a new function and call it:** rewriting repository code changes what is proven and breaks file and line numbers.
- **Supply guessed command-line arguments so `argparse` passes:** guessed values are invented input, close to a payload, and could steer the script into paths the real use never takes. Reporting "needs arguments" is honest.
- **Stub the vulnerable symbol (`yaml.FullLoader`) instead of the called name:** it is reached only inside PyYAML, so the recorded caller would be a library file, not the repository line the report shows.
- **Run the script in a separate child process inside the Sandbox:** stronger isolation from the harness, but a second process layer for a guarantee the single result line, the watchdog and the final process exit already give.

### Consequences

The flagship demo's path can be shown as "proven" with the exact repository line, in a few seconds instead of a timeout. Scripts that need command-line arguments, files or input before reaching the call are "not proven" with a clear reason; the proof is weaker for them, which is stated, not hidden. The `proof-run` step gains one optional argument, and the proof tests gain main-block fixtures (reached, `argparse` failure, early exit, a safe call before the vulnerable one). A new acceptance criterion covers this. Section 16 of [ARCHITECTURE.md](ARCHITECTURE.md) holds the details.

## ADR-0040: Host the demo on Google Cloud Run with hard cost guards

Date: 2026-10-26
Status: Accepted (supersedes ADR-0013; amends ADR-0020 for where hosted secrets are kept; ADR-0030 and ADR-0036 apply unchanged to the image)

### Context

[ADR-0013](#adr-0013-host-the-demo-on-a-free-hugging-face-docker-space) chose a free Hugging Face Docker Space. On 2026-10-26 creating that Space failed with HTTP 402: "hosting Gradio and Docker Spaces on free cpu-basic requires a PRO subscription", and the account is not PRO (details in [DEPLOYMENT](DEPLOYMENT.md) and [PLATFORM_FEEDBACK](PLATFORM_FEEDBACK.md)). So the free host the design relied on does not exist for this account.

Everything else from that deploy attempt still holds. The private spend-ledger dataset `jegadeesh17/pir-spend-ledger` exists and passed its live safety check, and Hugging Face datasets stay free, so the ledger stays where it is. Only the place that runs the app changes. The developer laptop cannot run Docker (CPU virtualization is off), so the image must be built remotely. The demo still has to be reachable without login until 2026-12-15 so judges can use it.

The user approved the move on 2026-10-26, verbatim: "I approve: create a GCP project proveitreachable on my billing account, set a $1/$5 budget alert, store my keys from .env in Secret Manager, and deploy ProveItReachable to Cloud Run with max 1 instance."

### Decision

- **Host:** Google Cloud Run, service `proveitreachable` in a new project `proveitreachable` on the user's billing account, region `us-central1` (a free-tier region). Google's Cloud Build service builds the image from the existing `Dockerfile`, so no local Docker is needed.
- **What gets uploaded:** the build input is an export of the last commit (`git archive HEAD`) unpacked into an empty temporary folder. Only committed files are uploaded, so the local `.env`, `data/` and unfinished work never leave the laptop.
- **No code or Dockerfile change:** the app keeps listening on port 7860 and the deploy tells Cloud Run to use that port (`--port 7860`).
- **Cost guards:**
  - at most 1 instance and none when idle (`--max-instances 1`, `--min-instances 0`);
  - billing only while a request is being served (request-based billing, `--cpu-throttling`);
  - 1 vCPU and 1 GiB of memory, because files the app writes to disk use instance memory on Cloud Run;
  - a $5 budget on the project with email alerts at $1 and $5;
  - an image-store cleanup rule that keeps only the 2 newest images;
  - the app's own limits unchanged: per-visitor and daily rate limits, and the spend caps of $10.00 for Token Factory and $5.00 for Tavily.
- **Secrets:** `NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID`, `TAVILY_API_KEY`, `HF_TOKEN` and `LANGSMITH_API_KEY` (plus `GITHUB_TOKEN` if set) are stored in Google Secret Manager, one version each, which stays within its free allowance of 6 active versions. Cloud Run hands them to the app as environment variables, so the app still reads secrets only from its environment ([ADR-0020](#adr-0020-mit-license-and-secrets-only-from-environment-variables)). The app runs as its own service account, which may read only these secrets. The values are piped from the local `.env` into Google by a script that never prints them.
- **Spend ledger:** unchanged: the private Hugging Face dataset, with `SPEND_LEDGER_BACKEND=hf_dataset`.
- **Access:** the service starts private, with no public access. Checks before launch send the developer's Google identity token. Making it public is the go-public task, done only after the user confirms.
- **Long-running streams:** each request may last up to 1,200 seconds (`--timeout 1200`). That covers a scan's progress stream (the scan deadline is 900 seconds), and a cut stream resumes where it stopped.
- **Visitor address:** Google's front end adds the visitor's address to the `X-Forwarded-For` header. `TRUSTED_PROXY_HOPS` stays at the safe value 0 until a deploy check measures the right value.
- **Logs:** the app's JSON lines go to Cloud Logging. Cloud Run's own request logs record raw visitor IP addresses, so they are excluded from storage, keeping the rule that no raw IP is logged.
- **Unchanged:** the page's `frame-ancestors` rule still allows embedding on huggingface.co. That is harmless because the app has no logins or cookies to attack, and removing it would need a code change for no security gain. The 6-hourly keepalive check stays as the monitor and spend alert. No keep-warm is needed, because a cold start takes a few seconds.

### Alternatives considered

- **Hugging Face PRO subscription:** keeps the original design, but costs a monthly fee for the whole account even when the demo is idle.
- **Fly.io (about $2 to $5 per month, card required):** a fixed monthly cost, a new CLI and a remote builder to learn. Cloud Run's free tier covers the expected demo traffic, and `gcloud` is already installed and logged in.
- **Render or Koyeb free tiers:** rejected in ADR-0013 (they sleep quickly, have small machines and no disk).
- **Always-allocated CPU or one instance always running:** no cold start, and a scan keeps running with nobody watching. But it bills for idle time, around the clock with one instance kept running.
- **A `.gcloudignore` file that mirrors `.dockerignore`:** works, but is a second ignore list to keep in sync, and it would upload uncommitted work.
- **Making the container read Cloud Run's `PORT` variable:** a Dockerfile change for no gain.
- **An automatic billing shut-off (a budget message that triggers a function to disable billing):** more moving parts. The one-instance limit already bounds the cost of normal use.

### Consequences

- **Cost:** hosting is expected to stay inside the monthly free tier (2 million requests, 180,000 vCPU-seconds, 360,000 GiB-seconds and 1 GiB of North America egress per billing account), but $0 is no longer guaranteed.
  - Worst case at one instance busy all month: about $58 for CPU (at about $0.000024 per vCPU-second beyond the free tier) and $6 for memory, so about $65.
  - A flood of tiny requests is billed per request ($0.40 per million beyond the free 2 million), which the instance limit does not cap.
  - Budget alerts only warn, can arrive hours late, and do not stop billing. The response to an alert is to remove public access or roll back.
- **Restarts:** the service stops when idle. Jobs, reports and rate-limit counters held in memory are lost then, as on the Space. The durable daily spend ceiling stays the binding control.
- **Scans need a watcher:** because CPU is given only while a request is open, a scan advances while the visitor's page holds its progress stream or polls the status (the UI does both). An abandoned scan stalls, and its spend reservation counts as fully spent after twice the scan deadline, which errs on the safe side.
- **Deploying** needs `gcloud` and the user's billing account. Rotating a key means adding a Secret Manager version, disabling the old one and redeploying.
- **The public address** becomes Cloud Run's `run.app` URL. The keepalive variable and the README use it from the go-public task on.
- **Re-planned tasks:** The deploy steps rewrite the deploy steps and checks for Cloud Run. The go-public step makes the service public by granting public invoke access instead of changing a Space's visibility.
- No new Python dependency. Details are in [ARCHITECTURE.md](ARCHITECTURE.md) section 18.

## ADR-0041: Proof dependencies without a wheel are downloaded with the network on and built with it off

Date: 2026-10-26
Status: Accepted (amends ADR-0032 for `proof-install`; ADR-0018 and ADR-0039 otherwise unchanged)

### Context

The call-chain proof ([ADR-0039](#adr-0039-call-chain-proof-runs-main-block-entries-as-scripts-and-stubs-the-call-the-repository-makes), commit 977e4b7) installs the repository's pinned dependencies only from ready-made packages ("wheels"). This happens in the `proof-install` step, which has internet access. A dependency that is published only as source code is skipped, and the proof says "not proven: no wheel".

Every demo finding is on PyYAML 5.3.1. On PyPI (checked 2026-10-26) that release has only Windows wheels and a source package, `PyYAML-5.3.1.tar.gz`. It has no wheel for Linux, which is what the Sandbox runs (Python 3.13). So every demo proof ends "not proven: no wheel", and the proof cannot show its value on the demos the user wants to present.

Building a source package means running its build script. That script is third-party code. The project's safety rule ([ADR-0032](#adr-0032-no-repository-file-influences-a-network-on-step)) is that nothing like it may run while the Sandbox has internet access, because that access is unrestricted. But the proof's last step, `proof-run`, already runs the repository's own untrusted code with the network off, with these same dependencies loaded. Running a dependency's build script under exactly those conditions exposes nothing new. The user approved this direction on 2026-10-26.

### Decision

Split the work into a download phase with the network on and a build phase with the network off. The exact contract is in [ARCHITECTURE.md](ARCHITECTURE.md) section 16.

- **Phase 1: download, network on, no package code runs. This stays inside `proof-install`, with no new network-on step.**
  - Wheels are installed exactly as before.
  - For each pin without a wheel, our own agent code (not pip or uv) reads PyPI's JSON page for that exact name and version. It takes the source package's address and its published sha256 checksum, and downloads the file from `files.pythonhosted.org` with a 20 MiB cap per file, a 50 MiB cap in total and at most 10 files. It checks the checksum and saves the file. The file is never unpacked or opened, so no metadata step runs.
  - When at least one source package was saved, the agent also downloads two fixed build tools: `setuptools` 75.8.0 and `wheel` 0.45.1, as wheels. Each must match both PyPI's checksum and a checksum committed in our agent's code.
  - Downloads share the existing 300-second limit of `proof-install`.
- **Phase 2: build, network off, in a new step `proof-build`.**
  - The step starts from the image `proof-install` saved. It re-checks every file and checksum, then runs `uv pip install --no-deps --target /work/deps --offline --no-index --find-links /work/buildwheels --build-constraints <file>`, plus the usual fixed interpreter and no-configuration flags, on each local file.
  - Each run uses a fresh empty working folder and the fixed environment, the same as `proof-install`.
  - uv builds in a temporary environment made only from the two local build-tool wheels. Anything else a package needs to build fails the build; nothing is fetched.
  - Limits: 120 seconds per package, 270 seconds in total, inside a 300-second step limit.
  - The build's output is never read or logged.
  - The resulting image is saved, and `proof-run` runs from it.
- **New rule:** no network-on step ever runs from an image that a build or repository code has touched.
- **Failures are stated results:**
  - a source package that cannot be downloaded: "uncertain: no wheel for <name>==<version>; its source package could not be downloaded";
  - a failed or timed-out build: "uncertain: could not build <name> <version> from source".
  - Both are "not proven", `proof-run` is skipped, and the verdict never changes.
- **Audit of the changed and new steps**, in the same form as ADR-0032:

| Step | Network | Working folder | Configuration | Interpreter | Environment and inputs |
|---|---|---|---|---|---|
| `proof-install` (uv for wheels; agent's own HTTP for source packages) | ON | fresh empty `/tmp/uvwd` | `--no-config`, `--no-sources` | `--python /usr/local/bin/python3.13`, no downloads | as ADR-0032 for wheels; source packages fetched only from `pypi.org` JSON and `files.pythonhosted.org`, no redirects, checksum-checked, written with exclusive create into fresh `/work/sdists/`, never opened; two build-tool wheels pinned by checksum into fresh `/work/buildwheels/` |
| `proof-build` (uv, builds) | OFF | fresh empty `/tmp/uvbuild-<i>` per package | `--no-config`, `--no-sources` | `--python /usr/local/bin/python3.13`, no downloads | fixed set without `UV_NO_BUILD`, plus `UV_OFFLINE=1`; `--offline --no-index --find-links /work/buildwheels --build-constraints /tmp/pirbuild/build-constraints.txt`; input only re-checked files under `/work/sdists/`; target `/work/deps`; output discarded |

### Alternatives considered

- **Build with the network on** (drop `--no-build`): a dependency's build script would run with unrestricted internet access on the owner's account. This breaks the core safety rule.
- **Download with `pip download` or uv's resolver:** both can prepare a source package's metadata, which may run its `setup.py`, while the network is on. The agent's own download from the JSON page provably runs nothing.
- **A separate network-on step just for downloads:** one more internet-facing step to audit, with no safety gain, because `proof-install` already fetches from PyPI under the same rules.
- **Let the build fetch its build tools from PyPI:** this needs the network during the build. Fetching two fixed, checksum-pinned wheels beforehand keeps the build offline and repeatable.
- **Disable build isolation and pre-install the build tools into the image:** this changes the base image, which means a new fingerprint and re-running every live gate. Isolation fed from local wheels needs no image change.
- **Add a Linux wheel of PyYAML 5.3.1 that we build ourselves:** we would be shipping a modified dependency, and it would only help this one package.
- **Prove the demos on a newer PyYAML that has wheels:** the proof would no longer be about the version the repository pins.

### Consequences

- The demo proofs on PyYAML 5.3.1 can reach "proven".
- Other source-only dependencies work if they build with `setuptools` and `wheel` alone and without a C compiler. The slim base image is expected to have none, so a C extension is skipped where the package allows it, as PyYAML does. Others fail with a clear "could not build" reason.
- A worst-case proof now takes up to 960 seconds of Sandbox operations. That is inside Cloud Run's 1,200-second request limit. The largest single spend hold is $0.30, inside the $0.75 per-scan budget.
- New code: the `proof-build` agent command, a `TIMEOUT_PROOF_BUILD_S` setting (default 300), the two checksum constants and a client method. No new Python dependency, no base-image change, and the agent protocol version stays 1.
- AC-66 and AC-68 in [SPEC](SPEC.md) need new wording, because a dependency without a wheel is now built offline instead of skipped.
- One live check (`test_proof_build_pyyaml_sdist_offline`, about $0.30 of promo credit) must show PyYAML 5.3.1 building on the Sandbox's Python 3.13 with the network off, and a main-block proof reaching "proven".
- **If PyYAML 5.3.1 does not build on Python 3.13** (for example, its `setup.py` imports `distutils`, which Python 3.13 no longer ships; setuptools normally supplies a replacement, but that is unverified here):
  - The fallback is a reviewed per-package "copy the pure-Python source" row: for PyYAML 5.x, copy `lib3/yaml/` from the archive into `/work/deps/yaml` with the network off, and run nothing.
  - Trade-off: it is safer, because no build code runs, but it covers only packages someone has reviewed and installs no package metadata. That fallback needs its own ADR before it is built.

## ADR-0042: Call-path edge verification, anti-tuning eval protocol, recorded proof in replays, deep links and warm ping

Date: 2026-10-26
Status: Accepted

### Context

At the close of M2 and preparation for hackathon submission, three critical risks were identified:

1. **Call-path evidence loophole**: The reachability gate (`_first_failed_reachability_rule` in `src/pir/report_builder.py`) checked each step individually (valid file, valid line range, present in AST seen locations) and verified that the final step matched a vulnerable symbol. However, it never checked pairwise connectivity: whether step `i` actually called step `i+1`, or whether the final step's `TargetSite.caller` matched the symbol of step `N-1`. As a result, stitched or disconnected paths produced by Nemotron could pass the gate as `likely reachable`.
2. **Small evaluation suite**: The baseline evaluation suite had only 14 cases (7 likely reachable). This sample was too small to provide a statistically defensible precision claim for hackathon judging across varied Python vulnerability patterns and dependency structures.
3. **Sandbox proof invisibility in demos**: Token Factory Sandboxes are central to ProveItReachable, yet every finding in the 3 recorded demo replays had `"proof": null`. Live Sandbox proofs take up to 960 seconds in the worst case, cannot run under replay mode, risk rate-limit exhaustion, and incur cold-start delays. Furthermore, judges landing on the site had to manually click demo scenarios without a direct URL to a pre-computed report.
4. **Operations and rate limiting**: Cloud Run scales to zero with ~20 s cold starts on the 6-hourly keepalive cron, and `TRUSTED_PROXY_HOPS=0` treated all visitors as a single client bucket pending second-network verification.

### Decision

Adopt five targeted improvements across evidence validation, evaluation rigor, demo experience, and production operations:

1. **Deterministic call-path edge verification in the verdict gate**:
   - In `_first_failed_reachability_rule`, after validating individual steps, enforce pairwise edge continuity: for each consecutive pair `(path[i], path[i+1])` where `i+1 < len(path) - 1`, require an `Edge` in `index.edges` where `caller == path[i].symbol`, `callee == path[i+1].symbol`, and `(file, line) == (path[i+1].file, path[i+1].line)`.
   - Require `edge.heuristic` to match `path[i+1].edge == "heuristic_call"`.
   - For the final step, require `TargetSite.caller == path[-2].symbol` (or `path[0].symbol` for a two-step path).
   - Any violation triggers `downgrade()` to `uncertain`, with reason code `unsupported_reachability_claim` and detail text `rule 2: step <i> does not call step <i+1>`.
   - Share caller→callee map resolution between `AstTool` and `report_builder`.
2. **Anti-tuning evaluation protocol and suite growth**:
   - Grow `eval/cases.toml` to ~30 cases (at least 28 cases), targeting ~45% `likely reachable`, ~30% `no reachable path detected`, and ~25% `uncertain`.
   - Include ~13 new synthetic cases covering multi-hop chains, class methods, decorator-wrapped calls, `__init__` re-exports, conditional imports, test-only usage, string name references, dead code behind `if False`, and diverse OSV advisories beyond PyYAML and requests, plus 2–3 pinned GitHub repositories.
   - **Anti-tuning rule**: All case ground truth labels and rationales must be committed before the live scored run and can never be adjusted afterwards to match model predictions.
   - Run `python -m pir eval --refresh-research` once to cache advisory research, followed by a single live scored evaluation run updating `eval/results/latest.md` and `latest.json`.
3. **Recorded Sandbox proof in replays**:
   - Add `--proof` to `pir record`. When passed, after the scan completes, it executes the Sandbox proof pipeline (ADR-0039, ADR-0041) for each `likely reachable` finding and stores the verified `Proof` model into `Finding.proof` before writing the demo recording.
   - Replay (`pir/replay.py`) serves recorded proofs verbatim at zero runtime cost and zero Sandbox execution.
   - The web UI detects non-null `Finding.proof` in replays and renders "proven in Token Factory Sandbox" directly, without dispatching live proof requests.
   - Re-record all 3 demo replays with edge validation active, with PyRL demonstrating a proven Sandbox execution.
4. **Replay deep linking**:
   - Support `/?demo=<slug>` in the web UI (e.g., `/?demo=pyrl`) to automatically select and start that replay on initial load.
   - Synchronize mock chip scenarios in `web/js/api/mock.js` with `web/fixtures/demos.json`.
5. **Judging warm ping and proxy hop verification**:
   - Add a 10-minute warm ping cron (`*/10 * * * *`) in `.github/workflows/keepalive.yml` calling `/api/health` during the active judging window. This keeps Cloud Run warm without exceeding the free tier (<4,500 requests/month out of 2M free allowance) and within the ₹100 budget cap.
   - Verify `TRUSTED_PROXY_HOPS` during the go-public step via a second-network test (mobile data connection) to properly isolate visitor rate limits.

### Alternatives considered

- **Dynamic edge verification via sandbox execution**: Checking edges dynamically by running repo code would break the offline analysis invariant and add excessive latency and cost. The deterministic `ast` index already captures all call edges and target sites.
- **Requiring live proof on every demo replay**: Running live proofs during judging would expose judges to 960s worst-case latency, cold starts, rate limits, and quota exhaustion. Pre-recording real proofs created in Token Factory Sandboxes provides identical proof artifacts with 0s latency and $0 cost.
- **Setting `min-instances=1` on Cloud Run**: Keeping a container continuously warm via `min-instances=1` bills CPU/memory 24/7 and would exceed the strict ₹100 ($5) hard budget cap. A 10-minute lightweight health ping achieves the warm response within Cloud Run's free tier.
- **Post-hoc evaluation label tuning**: Modifying evaluation labels after observing model failures would artificially inflate precision and invalidate the evaluation benchmark.

### Consequences

- Broken or stitched call paths are strictly caught and downgraded to `uncertain`, closing the reachability evidence loophole.
- The evaluation suite doubles to ~30 cases with an untuned, defensible precision figure.
- Judges can inspect real Token Factory Sandbox proofs immediately from recorded demo replays with zero waiting time.
- Deep links (`/?demo=pyrl`) allow direct links from Devpost and README into interactive reports.
- Cloud Run remains warm and responsive during judging without violating cost caps.
- No breaking schema changes: `Finding.proof` was already an optional field in `ScanReport` v1.0.
 
## ADR-0043: Fix-commit fallback for advisory research

Date: 2026-10-27
Status: Accepted

### Context

When researching OSV advisories with Tavily search and Nemotron LLM extraction, some advisories produce no dotted candidate symbols (for example when search snippets lack function names, the LLM extraction fails, or advisory descriptions are concise). Without candidate symbols, reachability analysis cannot evaluate vulnerable call paths and defaults to `no_symbols` or `uncertain`, leaving reachable vulnerabilities unreported. However, many OSV advisories contain direct links to GitHub fix commits in their advisory metadata (`references` with type `FIX` or commit URLs).

### Decision

Introduce a deterministic fix-commit fallback (`src/pir/fix_commit.py`) called by `Researcher._fallback_fix_commit` when Tavily search and LLM extraction yield no dotted symbols:
1. Extract unique GitHub commit references `(owner, repo, sha)` from advisory links using `commit_refs`.
2. Fetch the patch diff via the GitHub API (or disk cache at `DATA_DIR/cache/diffs/`).
3. Discard test files, test fixtures, and non-Python files (`_is_test_file`).
4. Parse hunk headers (`@@ ... @@`) and definition lines (`def`, `async def`, `class`) from Python source diffs (`changed_functions`).
5. Qualify extracted symbols with the package import root (mapping package names via PEP 503 normalization and `IMPORT_NAMES`).
6. Cap candidate symbols at 10 items.
7. Record the commit URL as an `AdvisoryLink` with `source="osv"` and `title="Fix commit"`, and include it in research `evidence`.

The fallback uses existing GitHub API access and local diff parsing without making additional Tavily search queries or LLM inference calls, incurring zero extra search spend.

### Alternatives considered

- **Additional Tavily search queries or advanced search depth**: Incurs additional latency and consumes paid search credits ($0.008–$0.016 per query) without guaranteeing patch details.
- **Passing full diffs into Nemotron LLM for extraction**: Incurs LLM token spend, risks exceeding completion token caps, exposes reasoning to prompt injection from repository diffs, and introduces non-deterministic symbol parsing.
- **Ignoring advisories without LLM-extracted symbols**: Leaves known, reachable vulnerabilities unanalyzed even when the exact fixing patch is already referenced in the OSV record.

### Consequences

- Higher symbol extraction recall for advisories with linked fix commits.
- Zero search credit cost and zero LLM token cost for the fallback path.
- Strictly deterministic AST and diff hunk parsing.
- Cached diffs prevent repeated GitHub API calls across scans.
- Test files and test helper functions are safely filtered out, preventing false candidate symbols.

## ADR-0044: Finding remediation, native SVG call-path view, proof-result authenticity nonce, and judging-window budget profile

Date: 2026-10-27
Status: Accepted

### Context

At the close of M2 and preparation for hackathon submission and judging, four key technical needs were identified:
1. **Actionable remediation advice**: Security reports identify reachable vulnerabilities, but developers need deterministic, actionable guidance on how to fix them (the minimum safe package version to upgrade to, or patch references).
2. **Visual call-path representation**: Long text call paths in findings are hard to visually parse quickly; a visual diagram showing the flow from repository entry point to the vulnerable sink improves triage.
3. **Sandbox proof authenticity**: When running untrusted repository code inside `proof-run` in the Sandbox, malicious or crafted code could attempt to spoof the stdout result line (e.g. printing `PIR_PROVEN`) to falsely claim a finding was proven.
4. **Judging-window budget profile**: Approximately $8 of Nebius promo credits remain. Spreading the budget evenly across 50 days to 2026-12-15 yields ~$0.16/day, which is below the $0.75 reservation needed for even a single live scan, blocking live judge testing.

### Decision

Implement four coordinated architectural enhancements:
1. **Remediation field in findings**:
   - Add an optional, additive `remediation: Remediation | None = None` field to `Finding` in `schemas/report.schema.json` and `pir/models/report.py`.
   - Compute remediation deterministically in `report_builder.py` using OSV `fixed` and `introduced` version ranges to recommend the minimum safe package upgrade target, or pointing to fix-commit guidance.
   - Non-breaking and fully backward-compatible: existing demo replays and stored reports without `remediation` remain valid without re-recording.
2. **Native hand-drawn SVG call-path view**:
   - Implement an interactive, native SVG call-path diagram in `web/js/render.js` without any external charting libraries or NPM dependencies.
   - Visualize the validated call chain step-by-step from repository entry point to vulnerable sink.
   - Support light and dark theme tokens, responsive layout down to 375px mobile viewports, and include an accessible text fallback.
3. **Proof-result authenticity nonce**:
   - Prevent untrusted repository code executed under `proof-run` from forging a "proven" result line.
   - The host generates a cryptographically secure random per-run nonce (`proof_nonce`) passed into the isolated execution environment via environment variable / arguments.
   - The proof runner harness emits the expected nonce token in its result line / exit verification, and the host strictly verifies the token before accepting a `proven` verdict.
4. **Judging-window budget profile**:
   - Reshape budget caps in `.env.example` and the spend ledger to support live judge evaluations through 2026-12-15 within the remaining ~$8 Nebius promo credits.
   - Allow live judge scans by preserving the $0.75 per-scan reservation while tightening the global daily scan limit and maintaining a high-signal judge allowance pool, ensuring live scans run reliably without exhausting promo credits before judging completes.

### Alternatives considered

- **LLM-generated remediation**: Non-deterministic, prone to hallucinated package versions, and incurs additional token spend. Deterministic OSV version range parsing is reliable, instant, and testable.
- **External diagram libraries (Mermaid.js, D3.js, Cytoscape)**: Violates the no-external-frontend-dependency architecture, bloats client bundle size, and complicates CSP. Native inline SVG requires zero external dependencies.
- **Relying solely on process exit codes or unauthenticated stdout**: Vulnerable to repository code spoofing proof completion strings.
- **Flat daily ceiling to 2026-12-15 ($0.16/day)**: Permanently locks out live judge scans since the minimum reservation is $0.75.

### Consequences

- Reports provide actionable, deterministic upgrade guidance per finding.
- Zero frontend dependencies added; SVG renders smoothly across devices and color themes.
- The per-run proof nonce rejects pre-written or replayed `proven` results. It is not a full authentication boundary: the target runs in the same interpreter that holds the nonce, so a repository written against this harness could read it and forge a `proven` result for its own report only. Running the target in a child process that cannot see the nonce would close this gap.
- Live judge scans are enabled within the hard ~$8 remaining credit ceiling.
- Backward compatibility is preserved: schema changes are strictly additive and optional.

## ADR-0045: Per-scan platform usage in the report, with an optional public trace link

Date: 2026-10-29
Status: Accepted

### Context

A report says what a scan found but not what it took to produce: how many Nemotron calls and tokens, Tavily searches, Sandbox operations and OSV queries one scan used, and what the paid ones cost. The figures already exist inside the scan. Its `ScanBudget` meters every paid call, and the LLM client, the researcher and the executor see each call they make. The scan's LangSmith trace shows the work step by step, but only to people with access to our LangSmith project. The project owner decided on 2026-10-28 to publish these per-scan figures in the report.

### Decision

1. **Report field.** `ScanReport` gains `usage: ScanUsage | None = None`. The field is optional and additive, and `schema_version` stays `"1.0"`, as it did for `remediation` (ADR-0044). The shapes in `pir/models/report.py` are mirrored in `schemas/report.schema.json`, with no extra keys allowed, like every other report object:

   ```python
   class NemotronUsage(Strict):
       calls: int = Field(ge=0); input_tokens: int = Field(ge=0); output_tokens: int = Field(ge=0); usd: float = Field(ge=0)
   class TavilyUsage(Strict):  searches: int = Field(ge=0); usd: float = Field(ge=0)
   class SandboxUsage(Strict): operations: int = Field(ge=0)
   class OsvUsage(Strict):     queries: int = Field(ge=0)
   class ScanUsage(Strict):
       nemotron: NemotronUsage; tavily: TavilyUsage; sandbox: SandboxUsage; osv: OsvUsage
       trace_url: str | None = None   # always serialized; null when not shared
   ```

   When `usage` is present, all four services are present, and a service the scan did not use has zeros. Every `usd` value is finite, non-negative, and rounded to 4 decimal places (`round(x, 4)`), the precision the spend summary already prints. The spend code keeps unrounded values internally; the rounding happens only when the report is assembled.
2. **Source of truth.** The figures come only from the scan's own `ScanBudget` (one per scan, created by `reserve(scan_id)`) and from counters at the existing client seams. We never call vendor billing APIs and never read account balances. The counters mean:
   - `nemotron.calls`: the chat-completion requests `pir/llm.py` sent for this scan, retries and failed calls included. `input_tokens` and `output_tokens`: the sum of the `usage` each response returned; a call without a returned `usage` (a timeout or error) adds no tokens. `usd`: the amounts `ScanBudget.record()` booked for those calls.
   - `tavily.searches`: the Tavily requests `pir/research.py` sent; cached answers are not counted. `usd`: the booked amounts.
   - `sandbox.operations`: the Sandbox commands the live executor ran for this scan, timeouts included. The fake executor counts 0 (the same rule as `StepStatus.tools`). The report gives no dollar figure for Sandboxes.
   - `osv.queries`: the package-version queries the scan passed to the OSV client, whether answered from its 24-hour cache or from the network.

   To split dollars by service, `record()` takes a service label (`nemotron`, `tavily`, `sandbox`) and the budget keeps per-service totals beside its pool totals. Pools, holds, caps and settlement are unchanged. The report step makes no paid call, so the figures are final when the report is built.
3. **Trace link.** A new setting, `LANGSMITH_SHARE_TRACES` (default `false`, documented in `.env.example`), controls sharing. When it and tracing are both on, and the scan's run is the trace root (live and CLI scans, never a scan nested under the evaluation runner's case run), `run_traced()` calls `flush()` after the root run ends, then calls LangSmith `share_run` on that run. `run_scan` copies the returned link onto `usage.trace_url`. The flush and share together are limited to 3 seconds, which keeps the report inside the UI's existing `report_not_ready` retry window. On any error, on a timeout, or when the link is not `https://` on a `smith.langchain.com` host with a `/public/` path, `trace_url` is null. A warning is then logged with the exception type only. Sharing never changes another report field and never fails a scan. Recording a demo (`record`) always sets `trace_url` to null, so no expiring link is stored in a fixture.

   **Hosted service:** the project owner enabled `LANGSMITH_SHARE_TRACES=true` there. Two conditions must hold before go-live: a security review approves the share path, and one shared trace, inspected by hand, shows no runtime or environment metadata. The reasoning is that once the repository is public, the prompts are already in public source, so sharing adds little exposure, and a judge gets a working trace link from their own scan.
4. **What a shared trace exposes.** A shared trace exposes the whole run tree of [ARCHITECTURE.md](ARCHITECTURE.md) section 19.2:
   - the `scan_id` and the state of each step;
   - Tavily queries and result URLs;
   - each finding's package, version and advisory ID;
   - every model call's full messages: our system prompts and tool definitions, advisory text, code snippets from the public repository, and the model's answers with token counts;
   - tool-call arguments and answer sizes, run timings, and the metadata the LangSmith SDK attaches.

   The guard is `redact_payload` in `pir/tracing.py`, applied to every run's inputs and outputs before upload. It replaces each configured secret value and any `Authorization` or `Bearer` value with `[REDACTED]`. It does not remove prompts or repository text, and run metadata does not pass through it.
5. **Display.**
   - **Web UI:** when `usage` is present, the UI adds one footer line, with the trace link when it is not null, to the existing "How this report was made" section (`pipelineStrip()` in `web/js/render.js`). When `usage` is absent, the line is hidden. The same section gains a static evaluation line, and "Powered by" adds Sandboxes and LangSmith.
   - **Markdown export:** the export (`pir/markdown.py`) adds one usage line when `usage` is present.
   - **Dollar label:** both the UI and the export label the dollar figure as metered model and search spend, not a total. Sandboxes are shown as an operation count only.
   - **Recorded demo replays:** they predate this field, hold no `usage`, and are not re-recorded.

### Alternatives considered

- **A new usage panel in the UI:** rejected because it duplicates "How this report was made".
- **Vendor billing APIs or account balances:** rejected. They are unverified, they report the whole account rather than one scan, and showing them would publish our balances.
- **Re-recording the demo replays so they carry `usage`:** rejected. It spends credits and risks changing recorded results for one footer line.
- **Defaulting the share setting to on in code:** rejected. Sharing stays a deliberate switch for each deployment.
- **A Sandbox dollar figure:** rejected by the project owner. Sandboxes are free in the beta, so the report shows their operation count only, while their metering continues in the spend ledger.

### Consequences

- Old reports, fixtures and replays stay valid unchanged. Consumers must treat `usage` as optional.
- The `usd` figures are metered amounts, not invoices. A timed-out model call is booked at its full hold, so they lean high. `nemotron.usd` plus `tavily.usd` can be below the scan's pool spend, because Sandbox cost is metered in the `nebius` pool ([ARCHITECTURE.md](ARCHITECTURE.md) section 11) but not broken out. A call-chain proof has its own reservation, so its usage is not in the scan's report.
- With sharing on, a live scan's report can arrive up to 3 seconds later. Each shared trace stays public until it is unshared in LangSmith or deleted by LangSmith's trace retention. After that, the link in a downloaded report stops working.
- **Expensive to change:** the field name `usage`, its keys, their types and the meaning of each counter ship in public JSON, in the published JSON Schema and in every report users download. Renaming or retyping them later breaks those consumers and needs a new `schema_version`. A shared link cannot be recalled from reports already downloaded; it can only be unshared, one run at a time, in LangSmith.
