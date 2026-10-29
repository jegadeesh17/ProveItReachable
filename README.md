# ProveItReachable

[![CI](https://github.com/jegadeesh17/ProveItReachable/actions/workflows/ci.yml/badge.svg)](https://github.com/jegadeesh17/ProveItReachable/actions/workflows/ci.yml)

Decides whether a known vulnerability in a Python repository's pinned dependencies is actually reachable from that repository's code.

**Live Demo**: [https://proveitreachable-ynjbf5demq-uc.a.run.app](https://proveitreachable-ynjbf5demq-uc.a.run.app)

---

## What it does

Traditional Software Composition Analysis (SCA) alerts on every pinned package matching a CVE database, creating overwhelming alert fatigue. Most reported dependencies are never invoked by application code.

Given a public GitHub repository URL, ProveItReachable:

1. **Intakes Dependencies**: Resolves pinned dependencies from repository manifests (`requirements.txt`, `pyproject.toml`, `Pipfile.lock`).
2. **Queries Known Advisories**: Identifies security advisories from OSV.dev (GHSA, CVE).
3. **Researches Vulnerable Symbols**: Uses Tavily search to identify the exact vulnerable class, function, or method, falling back to OSV GitHub commit diffs.
4. **Builds Sandbox AST Index**: Constructs a deterministic AST call graph inside a **Nebius Token Factory Sandbox** with network egress disabled.
5. **Navigates Call Paths with Nemotron**: Invokes **NVIDIA Nemotron** via **Nebius Token Factory** using structured tool-calling loops to trace execution paths from application entry points down to vulnerable symbols.
6. **Executes Dynamic Sandbox Proofs**: Generates an isolated test harness in a Token Factory Sandbox to dynamically verify whether the vulnerable symbol is reached during runtime execution.
7. **Publishes Audit Reports & Traces**: Emits a deterministic report (JSON and Markdown) with interactive SVG call paths, verifiable proof badges, and an optional sanitized LangSmith trace link.

---

## How it works

| Component | What it does | Where it shows in the app | Evidence |
|---|---|---|---|
| **Nebius Token Factory & Nemotron** | Serves `nvidia/nemotron-3-super-120b-a12b` driving the tool-calling reasoning loop to inspect AST call graphs | Real-time SSE stage stream, reachability verdicts, and reasoning narratives | Token usage counters and model latency displayed in report summary footer |
| **Token Factory Sandboxes** | Runs isolated containers (`contree-sdk`) for offline AST indexing and dynamic exploit verification under non-root `uid 65534` | Progress pipeline steps, verified Sandbox proof badges, and interactive call graphs | Cryptographic proof nonces and offline container execution status |
| **Tavily Search API** | Researches vulnerability advisories across security databases, release notes, and commit histories to locate vulnerable symbols | Advisory research step and source reference links in finding details | Cited web reference URLs and queries logged in execution traces |
| **LangSmith Tracing** | Provides end-to-end trace correlation and runtime observability across LLM invocations and tool calls | "View trace in LangSmith" public share link in report summary footer | Public trace link (`usage.trace_url`) with credentials redacted and 1-line errors |
| **OSV.dev API** | Fetches open source vulnerability metadata and affected package version ranges | Advisory cards, CVE/GHSA identifiers, and severity badges | Direct upstream links to OSV advisory records |

---

## Evaluation

Figures from [`eval/results/latest.json`](eval/results/latest.json): run 2026-10-29, model `nvidia/nemotron-3-super-120b-a12b`, 30 cases, 4 errored; precision 0.91, recall 0.67.

- **Benchmark Size**: 30 labelled test cases across 26 open-source Python repositories
- **Likely-Reachable Precision**: **0.91** (10 of 11 positive claims verified correct against ground truth)
- **Recall**: **0.67** (detects 10 of 15 true reachable call paths)
- **Abstention Rate**: 23.3% (explicitly reports `uncertain` when evidence is incomplete or ambiguously scoped)
- **Alert Noise Reduction**: Evaluated across demo repositories (`scripts/noise_summary.py`), traditional scanners generated 66 alerts; ProveItReachable narrowed these down to 4 likely reachable findings (and 2 proven in Sandbox), **cutting alert noise by 93.9%**.
- **Limitations**: Call-path verification analyzes static entry points (`__main__`, CLI click/argparse commands, FastAPI/Flask route decorators) and explicit call trees. Dynamic reflection, runtime monkeypatching, and unresolved manifests are classified as `uncertain` or `not examined`.

---

## How this was built

ProveItReachable was architected and built using an autonomous multi-agent engineering workflow following strict PSB (Plan · Setup · Build) engineering discipline:

1. **Specification & Architecture Freeze**: Requirements were agreed in [`docs/SPEC.md`](docs/SPEC.md) and frozen in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) with Architectural Decision Records ([`docs/DECISIONS.md`](docs/DECISIONS.md)) before writing product code.
2. **Safety by Construction**:
   - Zero-trust untrusted code execution: repository code never executes on the host; dynamic proofs and AST extraction execute inside ephemeral, network-isolated Token Factory Sandboxes.
   - Zero secret leakage: credentials live only in private environment stores; automated pre-public git sweeps and client anonymizers redact keys across traces and logs.
   - Paced spend meters: compare-and-swap spend ledgers enforce dynamic daily budget caps with unspent credit carry-forward.
3. **Rigorous Verification**: No milestone or task was marked complete without verified automated tests (2,100+ tests including fast unit tests, integration suites, and browser-driven Playwright E2E flows).

---

## Quick start

### Prerequisites

- Python 3.13+
- Git
- Google Cloud CLI (for hosted deployment)

### Local installation

```bash
git clone https://github.com/jegadeesh17/ProveItReachable.git
cd ProveItReachable

python -m venv .venv
source .venv/Scripts/activate      # Git Bash / Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
```

Copy the environment template and provide your keys:

```bash
cp .env.example .env
# Edit .env with your NEBIUS_API_KEY, NEBIUS_PROJECT_ID, TAVILY_API_KEY, and LANGSMITH_API_KEY
```

Run the application locally:

```bash
python -m pir serve
# Open http://127.0.0.1:8000 in your browser
```

---

## Usage

### Scanning a Repository via CLI

```bash
python -m pir scan https://github.com/chaovven/PyRL --out report.json
```

Output:
```text
Intaking repository https://github.com/chaovven/PyRL...
Found 1 pinned dependencies with 1 known advisory:
  - pyyaml 5.3 -> GHSA-8q59-q68m-5rh4
Researching advisory GHSA-8q59-q68m-5rh4...
Vulnerable symbol: yaml.load
Building AST index in Sandbox...
Reasoning with Nemotron-3-Super...
Verdict: LIKELY REACHABLE (PyRL/main.py:86 calls yaml.load)
Running dynamic execution proof in Sandbox...
Proof result: PROVEN (vulnerable symbol reached during execution)
Report saved to report.json
```

### Running Demo Replays

Demo replays require **zero API keys** and execute with **zero runtime cost**:

- Open [https://proveitreachable-ynjbf5demq-uc.a.run.app/?demo=pyrl](https://proveitreachable-ynjbf5demq-uc.a.run.app/?demo=pyrl)
- Open [https://proveitreachable-ynjbf5demq-uc.a.run.app/?demo=co2tools](https://proveitreachable-ynjbf5demq-uc.a.run.app/?demo=co2tools)
- Open [https://proveitreachable-ynjbf5demq-uc.a.run.app/?demo=planesweeppose](https://proveitreachable-ynjbf5demq-uc.a.run.app/?demo=planesweeppose)

---

## Running tests

```bash
# Fast offline unit and integration suite (2,100+ tests, < 15s)
python -m pytest -q -m "not slow"

# Lint and style checks
python -m ruff check .
python -m ruff format --check .

# Full end-to-end browser and server test suite
python -m pytest -q

# Run a single test module
python -m pytest -q tests/unit/test_config.py
```

Tests run completely offline by default: socket connections outside `127.0.0.1` raise an immediate network guard violation, ensuring tests never hit external APIs or incur credit spend.

---

## Configuration

All configuration is driven through environment variables. See [`.env.example`](.env.example) for variable definitions and documentation:

| Variable | Description | Default | Required for |
|---|---|---|---|
| `NEBIUS_API_KEY` | Nebius Token Factory API key | None | Live scans |
| `NEBIUS_PROJECT_ID` | Nebius Token Factory Project ID | None | Live scans |
| `TAVILY_API_KEY` | Tavily Search API key | None | Live scans |
| `LANGSMITH_API_KEY` | LangSmith API key for traces and eval | None | Live scans (tracing) |
| `LANGSMITH_SHARE_TRACES` | Enable public LangSmith share trace links | `false` | Hosted sharing |
| `LIVE_SCANS_ENABLED` | Feature switch for live scans | `true` | Live scans |
| `SPEND_LEDGER_BACKEND` | Spend ledger backend (`file` or `hf_dataset`) | `file` | Spend tracking |
| `RATE_LIMIT_GLOBAL_DAY` | Global live scan daily limit | `10` | Rate limiting |
| `RATE_LIMIT_REPLAYS_PER_CLIENT_HOUR` | Per-client replay rate limit | `30` (`300` hosted) | Rate limiting |

---

## Deployment

The application is deployed on **Google Cloud Run** with continuous keepalive monitoring:

- **Service**: `proveitreachable` in `us-central1`
- **Container Isolation**: Non-root `uid 1000` execution, CPU throttling enabled, Secret Manager credentials injection.
- **Spend Protection**: Automatic kill switch Pub/Sub trigger armed to unlink project billing on spend thresholds, protecting promo allowances.
- **Keepalive Automation**: GitHub Actions workflow [`keepalive.yml`](.github/workflows/keepalive.yml) runs every 6 hours to verify `/api/health` and trigger automated alerts if budget pools drop below 25%.

Detailed operational and deploy procedures are documented in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## Documentation

- [`docs/README.md`](docs/README.md) — Documentation index and document map
- [`docs/SPEC.md`](docs/SPEC.md) — System requirements and acceptance criteria
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — Architectural specification and contracts
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — Architectural Decision Records (ADRs)
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — Production deployment runbook
- [`docs/PLATFORM_FEEDBACK.md`](docs/PLATFORM_FEEDBACK.md) — Platform feedback for sponsors
- [`CHANGELOG.md`](CHANGELOG.md) — Project change history

---

## License

MIT. See [LICENSE](LICENSE).
