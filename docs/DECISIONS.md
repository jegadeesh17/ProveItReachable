# Architecture Decision Records

Each significant technical decision for ProveItReachable, with its reasons, the alternatives considered and the consequences; details live in [ARCHITECTURE.md](ARCHITECTURE.md).

| ID | Title | Status | Date |
|---|---|---|---|
| [ADR-0001](#adr-0001-python-and-fastapi-service-with-typed-schemas) | Python and FastAPI service with typed schemas | Accepted | 2026-10-24 |
| [ADR-0002](#adr-0002-nemotron-on-token-factory-with-pluggable-reasoning-strategy) | Nemotron on Token Factory with pluggable reasoning strategy | Accepted | 2026-10-24 |
| [ADR-0003](#adr-0003-run-untrusted-repo-code-only-inside-the-sandbox) | Run untrusted repo code only inside the Sandbox | Accepted | 2026-10-24 |
| [ADR-0004](#adr-0004-local-docker-fallback-only-by-explicit-approval) | Local Docker fallback only by explicit approval | Accepted | 2026-10-24 |
| [ADR-0005](#adr-0005-repo-intake-through-the-github-api-with-size-caps) | Repo intake through the GitHub API with size caps | Accepted | 2026-10-24 |
| [ADR-0006](#adr-0006-dependency-resolution-order-and-supported-sources) | Dependency resolution order and supported sources | Accepted | 2026-10-24 |
| [ADR-0007](#adr-0007-deterministic-ast-index-queried-by-nemotron-through-tool-calls) | Deterministic ast index queried by Nemotron through tool calls | Accepted | 2026-10-24 |
| [ADR-0008](#adr-0008-osvdev-as-the-advisory-source) | OSV.dev as the advisory source | Accepted | 2026-10-24 |
| [ADR-0009](#adr-0009-tavily-research-with-exact-source-urls) | Tavily research with exact source URLs | Accepted | 2026-10-24 |
| [ADR-0010](#adr-0010-report-schema-verdicts-confidence-and-ranking) | Report schema, verdicts, confidence and ranking | Accepted | 2026-10-24 |
| [ADR-0011](#adr-0011-job-based-http-api-with-server-sent-events-and-a-cli) | Job-based HTTP API with server-sent events and a CLI | Accepted | 2026-10-24 |
| [ADR-0012](#adr-0012-single-screen-frontend-in-plain-html-css-and-javascript) | Single-screen frontend in plain HTML, CSS and JavaScript | Accepted | 2026-10-24 |
| [ADR-0013](#adr-0013-host-the-demo-on-a-free-hugging-face-docker-space) | Host the demo on a free Hugging Face Docker Space | Accepted | 2026-10-24 |
| [ADR-0014](#adr-0014-spend-metering-hard-caps-and-rate-limits) | Spend metering, hard caps and rate limits | Accepted | 2026-10-24 |
| [ADR-0015](#adr-0015-durable-spend-ledger-in-a-private-hugging-face-dataset) | Durable spend ledger in a private Hugging Face dataset | Accepted | 2026-10-24 |
| [ADR-0016](#adr-0016-cached-replays-for-curated-demo-repos) | Cached replays for curated demo repos | Accepted | 2026-10-24 |
| [ADR-0017](#adr-0017-evaluation-suite-metrics-and-precision-bar-with-langsmith) | Evaluation suite, metrics and precision bar with LangSmith | Accepted | 2026-10-24 |
| [ADR-0018](#adr-0018-call-chain-proof-with-a-recording-stub-gated-and-cuttable) | Call-chain proof with a recording stub, gated and cuttable | Accepted | 2026-10-24 |
| [ADR-0019](#adr-0019-bounded-timeouts-and-failure-becomes-uncertain) | Bounded timeouts and failure becomes uncertain | Accepted | 2026-10-24 |
| [ADR-0020](#adr-0020-mit-license-and-secrets-only-from-environment-variables) | MIT license and secrets only from environment variables | Accepted | 2026-10-24 |

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
Status: Accepted

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
Status: Accepted

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
Status: Accepted

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
Status: Accepted

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
Status: Accepted

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
Status: Accepted

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
Status: Accepted

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
