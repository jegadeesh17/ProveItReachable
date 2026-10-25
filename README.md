# ProveItReachable

[![CI](https://github.com/jegadeesh17/ProveItReachable/actions/workflows/ci.yml/badge.svg)](https://github.com/jegadeesh17/ProveItReachable/actions/workflows/ci.yml)

Decides whether a known vulnerability in a Python repository's pinned dependencies is actually reachable from its code.

## Development setup

Requires Python 3.13. Run from the project root.

```bash
python -m venv .venv
source .venv/Scripts/activate      # Git Bash on Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
```

Then copy `.env.example` to a new local env file and fill in keys in your own editor. Never commit it.

Commands (with `.venv` active):

| Purpose | Command |
|---|---|
| Fast (also run by CI) | `python -m ruff check . && python -m ruff format --check . && python -m pytest -q -m "not slow"` |
| Full | `python -m pytest -q` |
| One file | `python -m pytest -q tests/unit/test_config.py` |
| Check configuration | `python -m pir check-config --for scan` |

Tests never touch the network: a guard in `tests/conftest.py` makes any non-loopback connection raise, and `live` tests are skipped unless `PIR_LIVE_TESTS=1`.
