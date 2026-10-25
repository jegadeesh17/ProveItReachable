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
| [ADR-0013](#adr-0013-host-the-demo-on-a-free-hugging-face-docker-space) | Host the demo on a free Hugging Face Docker Space | Accepted | 2026-10-24 |
| [ADR-0014](#adr-0014-spend-metering-hard-caps-and-rate-limits) | Spend metering, hard caps and rate limits | Superseded by ADR-0022 | 2026-10-24 |
| [ADR-0015](#adr-0015-durable-spend-ledger-in-a-private-hugging-face-dataset) | Durable spend ledger in a private Hugging Face dataset | Superseded by ADR-0029 | 2026-10-24 |
| [ADR-0016](#adr-0016-cached-replays-for-curated-demo-repos) | Cached replays for curated demo repos | Accepted | 2026-10-24 |
| [ADR-0017](#adr-0017-evaluation-suite-metrics-and-precision-bar-with-langsmith) | Evaluation suite, metrics and precision bar with LangSmith | Accepted | 2026-10-24 |
| [ADR-0018](#adr-0018-call-chain-proof-with-a-recording-stub-gated-and-cuttable) | Call-chain proof with a recording stub, gated and cuttable | Accepted | 2026-10-24 |
| [ADR-0019](#adr-0019-bounded-timeouts-and-failure-becomes-uncertain) | Bounded timeouts and failure becomes uncertain | Accepted | 2026-10-24 |
| [ADR-0020](#adr-0020-mit-license-and-secrets-only-from-environment-variables) | MIT license and secrets only from environment variables | Accepted | 2026-10-24 |
| [ADR-0021](#adr-0021-sandbox-runs-through-the-sdk-and-rest-api-with-no-docker-fallback) | Sandbox runs through the SDK and REST API, with no Docker fallback | Superseded by ADR-0027 | 2026-10-25 |
| [ADR-0022](#adr-0022-spend-metering-from-reported-sandbox-cost-with-build-and-demo-caps) | Spend metering from reported Sandbox cost, with build and demo caps | Superseded by ADR-0029 | 2026-10-25 |
| [ADR-0023](#adr-0023-nemotron-3-super-with-native-tool-calling-only) | Nemotron 3 Super with native tool calling only | Accepted | 2026-10-25 |
| [ADR-0024](#adr-0024-production-operations-ci-json-logs-and-spend-alerts) | Production operations: CI, JSON logs and spend alerts | Accepted | 2026-10-25 |
| [ADR-0025](#adr-0025-contract-additions-from-the-prototype-review) | Contract additions from the prototype review | Accepted | 2026-10-25 |
| [ADR-0026](#adr-0026-self-hosted-geist-fonts-under-a-strict-csp) | Self-hosted Geist fonts under a strict CSP | Accepted | 2026-10-25 |
| [ADR-0027](#adr-0027-sandbox-steps-with-network-on-never-run-repository-code) | Sandbox steps with network on never run repository code | Superseded by ADR-0032 | 2026-10-25 |
| [ADR-0028](#adr-0028-dependency-resolution-order-compiled-without-builds) | Dependency resolution order, compiled without builds | Accepted, amended by ADR-0032 and ADR-0034 | 2026-10-25 |
| [ADR-0029](#adr-0029-one-spend-ledger-for-every-spender-with-allowances-and-a-daily-ceiling) | One spend ledger for every spender, with allowances and a daily ceiling | Accepted | 2026-10-25 |
| [ADR-0030](#adr-0030-images-pinned-by-digest-and-hash-checked-installs) | Images pinned by digest and hash-checked installs | Accepted | 2026-10-25 |
| [ADR-0031](#adr-0031-tavily-research-with-exact-source-urls-and-a-7-day-cache) | Tavily research with exact source URLs and a 7-day cache | Accepted | 2026-10-25 |
| [ADR-0032](#adr-0032-no-repository-file-influences-a-network-on-step) | No repository file influences a network-on step | Accepted | 2026-10-25 |
| [ADR-0033](#adr-0033-sandbox-base-image-imported-by-version-tag-and-checked-against-a-reviewed-fingerprint) | Sandbox base image imported by version tag and checked against a reviewed fingerprint | Proposed | 2026-10-25 |
| [ADR-0034](#adr-0034-sandbox-contract-corrections-from-the-live-platform-checks) | Sandbox contract corrections from the live platform checks | Accepted | 2026-10-25 |

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
Status: Accepted

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
Status: Accepted

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

The accepted prototype and the orchestrator raised contract questions for the freeze: the UI cannot show the live-scan limits; some errors have no fixed message; a Sandbox timeout can turn a whole report uncertain; the UI wants an evaluation figure only when one was really measured; the tool behind each step is mapped in the browser; and the UI should claim "Sandbox" only when one actually ran. Nothing has been recorded yet, so the report format can still change cheaply.

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
Status: Accepted

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
Status: Proposed (needs the user's approval; once accepted it amends ADR-0030 for the Sandbox image only, and the Space image stays pinned by digest)

### Context

ADR-0030 says the Sandbox's starting image must be named by its exact fingerprint (an `@sha256:` digest), so nobody can swap it under us by moving a tag. The live check on 2026-10-25 (M1-TASK-08) found that the Sandbox's image importer (contree-sdk 0.3.6 `images.oci`) answers `HTTP 404 page not found` for every digest reference it was given: the ghcr.io index digest, the amd64 manifest digest, with or without a tag, and docker.io as well. Plain tags import fine. Without a `tag=` override the SDK also invents a local name containing `@`, which then fails with 400. As written, live scans cannot start. The demo is on 2026-10-30.

What a swapped image could do: it runs only inside the Sandbox, which holds no secrets, so it cannot steal keys. It could falsify every scan's report, and its Python and uv run our network-on steps (ADR-0032), so it could use those steps (each capped at 120 to 300 seconds and metered) to send traffic from our account. The Python and uv in the image are therefore part of what ADR-0032's guarantee relies on.

### Decision (proposed)

- **Import by a version tag.** The Sandbox imports `ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm-slim` (astral publishes one tag per uv release; the floating `python3.13-bookworm-slim` tag moves with every release and would trip the check below every week), always with the `tag=` override `pir-base/sha256-<digest hex>`.
- **Configuration keeps the digest.** `SANDBOX_BASE_IMAGE` stays `<tag>@sha256:<digest>` and the settings check is unchanged: the digest records which reviewed build the tag must deliver. The executor imports only the part before `@`. The developer first confirms with `docker buildx imagetools inspect` that the version tag exists and records its index digest (expected to equal `531f855b...44ca`; if not, the version tag's digest is used).
- **A reviewed fingerprint, measured outside the Sandbox.** A new agent command `selfcheck` (network off, no arguments) prints: the Python version, the output of `uv --version`, the sha256 of `/usr/local/bin/python3.13`, `/usr/local/lib/libpython3.13.so.1.0`, `/usr/local/bin/uv`, `/usr/local/bin/uvx`, `/lib64/ld-linux-x86-64.so.2` and `/lib/x86_64-linux-gnu/libc.so.6` (a missing file is recorded as `"missing"`), and one hash over every file and link under `/usr/local/lib/python3.13` (sorted relative paths with their content hashes). The developer runs it once with Docker on the laptop, where pulling by digest works: `docker run --rm --platform linux/amd64 --network none --user 65534:65534 -v <dir with agent.pyz>:/opt/pir:ro <SANDBOX_BASE_IMAGE> /usr/local/bin/python3.13 -I -S /opt/pir/agent.pyz selfcheck --args-b64 e30=`. The decoded result, plus the digest it came from, is committed as `src/pir/executor/base_image_fingerprint.json`.
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

## ADR-0034: Sandbox contract corrections from the live platform checks

Date: 2026-10-25
Status: Accepted (amends ADR-0028 for the compile output and the authoritative uv error text; corrects ARCHITECTURE sections 7.1, 7.2, 13 and 22.1)

### Context

The first runs against the real Nebius Sandbox (M1-TASK-08, 2026-10-25) showed five places where the frozen design did not match how the tools actually behave. None of them changes the safety guarantees; they make the contract describe what the platform and uv really do.

### Decision

1. **Compile output goes to a file.** `uv pip compile ... --output-file -` does not print to the screen in uv 0.13.5 (development) or uv 0.9.30 (Sandbox image): nothing appears and a file literally named `-` is written. The compile command now ends with `--output-file /tmp/pirin/requirements.txt` (a fresh folder created by our agent, so no old file can steer the result). After a successful run the agent reads only that file, with the existing size cap; a missing or oversized file is "compile failed: no output". The workaround that also looked for a file named `-` is removed.
2. **Output size caps.** The Sandbox accepts at most 10 MiB (10485760 bytes) for `truncate_output_at`; 11 MiB is refused. It stays at 10 MiB. The largest result our agent may print is lowered from 10 MiB to 8 MiB (8388608 bytes of base64) on both sides; the agent checks it before printing and, if over, prints nothing and exits with code 3 ("limit exceeded"), and it writes at most 1 MiB of log lines. A valid result can then never be cut off by the platform. The 50 MiB limit after decompression is unchanged.
3. **Spend refusals are exceptions in the executor.** The spend meter's `SpendDenied` is a plain value, not an error. The executor turns it into `ExecutorSpendDenied`, an error carrying the original value as `.denied`, raised before any Sandbox request; the Sandbox client lets it pass through, and the scan pipeline maps its reason to "spend cap reached" or "scan budget reached". The model and search clients keep handling the plain value.
4. **Preparing the image.** In the async SDK, `image.run(...)` is awaited directly and returns the saved image; there is no separate `op.wait()` step.
5. **Which uv error text counts.** The uv inside the pinned Sandbox image (0.9.30) is authoritative, because it is the one that runs in real scans. The live test must match the Sandbox-image fixture `tests/fixtures/http/contree/uv_no_build_sdist.sandbox.stderr.txt` exactly; the development fixture from uv 0.13.5 stays as a second test case. The detector for "a build would be needed" relies only on the sentences both versions share, and a unit test runs it on both. Neither the image nor the development pin changes: the development uv only regenerates `requirements.lock` on the laptop and never runs in the Sandbox, and moving the image would mean re-running every live gate before the demo.

### Alternatives considered

- Dropping `--output-file` and reading the screen output: shorter, but not checked live, and it depends on how uv treats `--quiet`; a file path in our own folder is unambiguous.
- Keeping a 10 MiB result cap with 10 MiB truncation: a result near the cap would be cut and wrongly reported as a Sandbox failure.
- Making `SpendDenied` itself an exception: changes the spend meter (M1-TASK-07, done) and every caller of it, for no gain.
- Matching uv error text across versions exactly: impossible, as the layouts differ; aligning the development pin to 0.9.30 could change the lock file format for no safety benefit.

### Consequences

The contract now matches the platform. The compile and parsing code change in small, testable ways; the protocol cap constant changes in two files; the scan pipeline must catch `ExecutorSpendDenied` for Sandbox steps. Any future change of the Sandbox image also re-records the Sandbox uv error-text fixture.
