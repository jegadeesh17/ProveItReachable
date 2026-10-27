# ProveItReachable

[![CI](https://github.com/jegadeesh17/ProveItReachable/actions/workflows/ci.yml/badge.svg)](https://github.com/jegadeesh17/ProveItReachable/actions/workflows/ci.yml)

Decides whether a known vulnerability in a Python repository's pinned dependencies is actually reachable from that repository's code.

**Demo:** _link placeholder, to be added when the Space is made public (M2-TASK-11)_

## What it does

Given a public GitHub repository URL, the service:

1. reads the repository's pinned dependencies (GitHub metadata only);
2. looks up known advisories for them on OSV.dev;
3. uses Tavily to research each advisory and find the vulnerable function, falling back to OSV GitHub fix-commit diffs when search yields none;
4. builds a deterministic `ast` index of the repository inside a Nebius Token Factory Sandbox;
5. lets Nemotron (Token Factory) decide reachability by querying that index through tool calls;
6. validates every answer (labels, call path, source URLs) and ranks the findings in a deterministic report builder.

The report is available as JSON (`schemas/report.schema.json`) or Markdown. A single-page web UI streams progress over server-sent events. Recorded demo scans replay at no cost.

## Evaluation

Figures from [`eval/results/latest.json`](eval/results/latest.json): run 2026-10-26, model `nvidia/nemotron-3-super-120b-a12b`, 30 cases, 4 errored; precision 0.91, recall 0.67.

Measured alert reduction across demo repositories (`scripts/noise_summary.py`): 66 traditional scanner alerts down to 4 likely reachable (and 2 proven in Token Factory Sandbox), cutting alert noise by 93.9%.

## Setup

Requires Python 3.13. Run from the project root.

```bash
python -m venv .venv
source .venv/Scripts/activate      # Git Bash on Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
```

Then copy `.env.example` to a new local env file and fill in keys in your own editor. Never commit it.

## Configuration

Settings come from environment variables: a local `.env` file, or Space secrets and variables when hosted. [`.env.example`](.env.example) lists every variable with a comment; [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (Setup requirements) has the full table with defaults.

- Live scans need `NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID` and `TAVILY_API_KEY`.
- With `LIVE_SCANS_ENABLED=false` no keys are needed, and the recorded demos are served.
- Secret values are never logged.

## Commands

Run with `.venv` active.

| Purpose | Command |
|---|---|
| Fast checks (also run by CI) | `python -m ruff check . && python -m ruff format --check . && python -m pytest -q -m "not slow"` |
| Full test suite | `python -m pytest -q` |
| One test file | `python -m pytest -q tests/unit/test_config.py` |
| Check configuration | `python -m pir check-config --for scan` |
| Serve the API and web UI locally | `python -m pir serve` (binds `127.0.0.1:8000` by default) |
| Scan a repository | `python -m pir scan https://github.com/owner/repo` |
| Measure alert noise reduction | `python scripts/noise_summary.py` |
| Evaluation (spends credits) | `python -m pir eval` |

Tests never touch the network: a guard in `tests/conftest.py` makes any non-loopback connection raise, and `live` tests are skipped unless `PIR_LIVE_TESTS=1`.

## Deployment

The app runs as a Docker image on a free Hugging Face Docker Space. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). Dependencies install from the hash-checked [`requirements.lock`](requirements.lock). A scheduled GitHub Actions workflow, [`keepalive.yml`](.github/workflows/keepalive.yml), calls `/api/health` every 6 hours and fails when a spend pool has less than 25% of its budget left.

## Security

- Secrets come only from environment variables and are redacted from logs.
- Repository code runs only inside the Nebius Sandbox, never on the host.
- The Docker image excludes `.env`, `data/`, `eval/results/` and `.git/`.

## License

MIT. See [LICENSE](LICENSE).
