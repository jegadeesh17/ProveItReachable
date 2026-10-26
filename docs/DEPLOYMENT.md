# Deployment

Hosting per ARCHITECTURE section 18 (ADR-0013, ADR-0030, ADR-0033, ADR-0036). Part A (offline) was built and checked on 2026-10-26 (M2-TASK-08, commit 9a013b7). Part B (Space, ledger, caps, deploy checks) was attempted on 2026-10-26 (attempt 1).

**Status (2026-10-26, part B attempt 1): BLOCKED at Space creation.**

- **Done:** ADR-0036 packaging (`.dockerignore` re-includes only `eval/results/latest.json`; the Dockerfile copies that one file; the packaging tests pass locally).
- **Done:** the HF token has write access; its owner (`jegadeesh17`) matches the owner of `SPEND_LEDGER_HF_REPO`; the ledger dataset `jegadeesh17/pir-spend-ledger` exists and is **private** (created 2026-10-26 under the approval); the M2-TASK-02 live gate (`tests/live/test_live_hf_ledger.py`, 2 tests) **passed** on it.
- **Blocked:** creating the private Space `jegadeesh17/proveitreachable` returned **HTTP 402**: "hosting Gradio and Docker Spaces on free cpu-basic requires a PRO subscription". The account is not PRO (`isPro` false). No Space exists. Nothing was uploaded, no Space secret or variable was set, and `ledger set-caps` has not run.
- Nothing is public. No GitHub setting changed. No workflow was triggered.

## 1. What is built

- **Dockerfile:** `FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962` (multi-arch index digest; its linux/amd64 manifest is `sha256:d879ed88565a7a3c614b980b369d4a5025a9d057b71f93d09fc6d0b2ca823a18`).
  - Verified 2026-10-26 two ways: `docker buildx imagetools inspect python:3.13-slim` printed the index digest, and the Docker Hub registry manifest API (`HEAD .../library/python/manifests/3.13-slim`, `docker-content-digest` header) returned the same value.
- Runs as uid 1000. Command: `python -m pir serve --host 0.0.0.0 --port 7860` (one Uvicorn worker, JSON logs).
- Installs: `pip install --no-cache-dir --require-hashes -r requirements.lock`, then `pip install --no-cache-dir --no-deps .`.
- Copies `pyproject.toml`, `README.md`, `LICENSE`, `src/`, `web/`, `demo/` (the replays), and the single file `eval/results/latest.json` to `./eval/results/latest.json` (ADR-0036: the published eval summary, so the page can show the eval figure). `.dockerignore` excludes `.env`, `.env.*` (except `.env.example`), `data/`, `eval/results/` (except `!eval/results/latest.json`), `.venv/` and `.git/`.
- **requirements.lock** (committed) is regenerated with `uv pip compile pyproject.toml --universal --generate-hashes --python-version 3.13 -o requirements.lock` (uv 0.13.5 from the dev extra). A fast test checks that every runtime dependency is in it at its pin with a `--hash`.

## 2. Local Docker build: skipped on this laptop

- **Why:** Docker cannot run on this laptop. CPU virtualization is off in the firmware (see the fingerprint ADR in DECISIONS.md), so the Docker daemon does not start. The Docker CLI (29.6.2) is installed; `docker info` could not reach the engine on 2026-10-26.
- **What replaces it (decided, ADR-0036 and the M2-TASK-08 brief):**
  1. The image build: Hugging Face builds the image from the Space repository. Poll the Space runtime stage; on `BUILD_ERROR`, fetch the build logs.
  2. The image has no `.env`: list the Space repository files through the HF API. Assert there is no `.env*` file, nothing under `data/`, and nothing under `eval/results/` except `latest.json`.
  3. The app serves: `GET /` and `GET /api/health` on the private Space URL with the token as a Bearer header.
  4. Only `latest.json` ships from `eval/results/`: the repository file list shows it, and the deployed page or API shows the eval figure.
- **Local commands** (for a machine where the daemon runs):

  ```bash
  docker build -t proveitreachable:local .
  docker run --rm -p 127.0.0.1:7860:7860 -e LIVE_SCANS_ENABLED=false proveitreachable:local
  curl -s http://127.0.0.1:7860/api/health
  docker run --rm --entrypoint sh proveitreachable:local -c 'test ! -e /app/.env && ls -ld /app/data && test "$(ls /app/eval/results)" = "latest.json"'
  ```

  `LIVE_SCANS_ENABLED=false` lets `serve` start with no keys.

## 3. Hugging Face Space (part B): BLOCKED on 2026-10-26

- **Space:** `jegadeesh17/proveitreachable`, private, SDK Docker, app_port 7860. **Not created.** `create_repo` returned HTTP 402 (see the status above). ARCHITECTURE section 18 assumed free hosting ($0); a Docker Space on the free CPU tier needs a PRO subscription.
- **Decision for the user** (each option needs approval):
  1. Subscribe to Hugging Face PRO on this account. Part B then continues on the same design (the Space, secrets, variables, build, `set-caps`, deploy checks). Costs a monthly fee; the price is on huggingface.co/pro.
  2. Another host (Fly.io was the listed paid alternative: about $2 to $5 per month, card required). This needs a new ADR and rework of this file.
  3. Stop at part A. The image and the ledger dataset stay as they are.
- **README front matter** goes at the top of the Space repository's `README.md` (not this repository's README):

  ```yaml
  ---
  title: ProveItReachable
  sdk: docker
  app_port: 7860
  pinned: false
  license: mit
  ---
  ```

- **Space repository content:** the `git ls-files` set (tracked files only, the working tree version), minus `.env.example` and minus every `eval/results/` file except `latest.json`. The Space's `README.md` is this repository's README with the front matter above prepended.
- **Space secrets** (values typed in the Space settings, never in files): `NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID`, `TAVILY_API_KEY`, `HF_TOKEN` (write access to the ledger dataset), `LANGSMITH_API_KEY`, and `GITHUB_TOKEN` only if it is set. Set through `add_space_secret`, values from the local `.env` inside a Python process.
- **Space variables** (not secret): `SPEND_LEDGER_BACKEND=hf_dataset`, `SPEND_LEDGER_HF_REPO=jegadeesh17/pir-spend-ledger`, `TRUSTED_PROXY_HOPS=0` (until the XFF check in section 5 sets it), `NEBIUS_PRICE_INPUT_PER_MTOK=0.30`, `NEBIUS_PRICE_OUTPUT_PER_MTOK=0.90`, `TAVILY_PRICE_PER_CREDIT_USD=0.008`, `LANGSMITH_TRACING=true`, `LOG_LEVEL=INFO`, `LIVE_SCANS_ENABLED=false` until `set-caps` succeeds, then `true`.
  - `LIVE_SCANS_ENABLED` must be `true` for `/api/health` to report `budget_remaining`: the app builds the spend meter only when live scans are enabled (`src/pir/api/app.py`), and the value is null otherwise.
  - **Prices:** Nebius: the user's Nebius bill gives $0.26 for 0.87M input tokens and $0.15 for 0.17M output tokens, which matches the Token Factory catalog (`https://tokenfactory.nebius.com/api/public/models_info`: `nvidia/nemotron-3-super-120b-a12b` at 0.30 input and 0.90 output USD per million tokens). The code has no cached-input price, so cached tokens are counted at the full input price (conservative). Tavily: $0.008 per credit (`https://tavily.com/pricing`, Pay As You Go; the user's billing page agrees).
- **Local `.env`:** it sets `SPEND_LEDGER_HF_REPO` but not `SPEND_LEDGER_BACKEND`, so the settings refuse to load (the `file` backend is refused while the HF repo is set). The user sets `SPEND_LEDGER_BACKEND=hf_dataset` in `.env`. The agent does not edit `.env`. Until then, commands run with `SPEND_LEDGER_BACKEND=hf_dataset` in the process environment.
- **Balances and caps** (not run; blocked): `python -m pir ledger set-caps --nebius-balance 29.99 --tavily-balance 32.40 --nebius 10 --tavily 5` with the HF ledger settings. Balances are the user's figures on 2026-10-26: Nebius $0.49 trial credits plus $29.50 account balance = $29.99; Tavily 4,050 credits (925 plan plus 3,125 add-on) at $0.008 = $32.40. Local allowances take the defaults ($5 Nebius, $2 Tavily); headroom takes the minimums ($5 Nebius, $3 Tavily). The formula maximum (recorded spend 0 on a new ledger + balance − headroom) is $24.99 Nebius and $29.40 Tavily, so the approved caps of **$10.00** and **$5.00** are within it.

## 4. Keepalive and spend alert

- `.github/workflows/keepalive.yml`: schedule `17 */6 * * *` and `workflow_dispatch`. It runs `python -I src/pir/keepalive.py "$PIR_HEALTH_URL"`.
- GitHub **repository variable** `PIR_HEALTH_URL` (not a secret): `https://<hf-owner>-<space-name>.hf.space/api/health`. Set it when the Space goes public (M2-TASK-11).
- Exit codes: `0` health ok and every pool at or above 0.25; `1` a pool is below 0.25 (the output names it, for example `Budget alert: nebius at 0.18 (below 0.25)`); `2` health check failed (non-200, invalid JSON, status not ok, `budget_remaining` null, a non-https URL, or no answer after 3 tries 20 s apart). A failed run makes GitHub email the owner.
- **`workflow_dispatch` against the private Space: DEFERRED to M2-TASK-11.** The URL needs a token, and the keepalive script sends none. No workflow was triggered.

## 5. Deploy checks (part B)

Status on 2026-10-26 (attempt 1). "BLOCKED" means the check needs the Space, which does not exist.

| Check | How | Status and evidence |
|---|---|---|
| Image builds (replaces the local `docker build`) | Poll the Space runtime stage; on failure, fetch the build logs | BLOCKED (no Space) |
| Image has no `.env`; nothing from `data/`; only `latest.json` from `eval/results/` | List the Space repository files through the HF API; `.dockerignore` and the COPY line are checked by `tests/unit/test_packaging.py` | Local part PASS (ADR-0036 tests in the fast command). Space listing BLOCKED |
| `GET /` and `GET /api/health` on the private URL | Bearer token; `/api/health` must return `status: ok` | BLOCKED |
| Eval figure shown from the shipped `latest.json` | Page or `/api/` response carries the figure | BLOCKED |
| Demo replay streams through the HF proxy (SSE if enabled) | Replay a demo on the Space | BLOCKED |
| `X-Forwarded-For` hop count; `TRUSTED_PROXY_HOPS` set to match (fail-safe 0) | Two requests with different spoofed leftmost XFF entries: the logged `client` hash must not change. Then an IPv4 and an IPv6 request (if the proxy accepts IPv6): the hash differs only when the hop count is 1. The logs hold a salted hash (salt random per process), so the hash cannot be matched to the address directly. | BLOCKED. Keep `TRUSTED_PROXY_HOPS=0` until a run shows otherwise |
| `frame-ancestors` allows the HF embed | Response `Content-Security-Policy` on `/` contains `frame-ancestors 'self' https://huggingface.co` | BLOCKED (the value is set in `src/pir/api/security_headers.py`) |
| `ledger set-caps` succeeded (stale-parent gate passed) | `python -m pir ledger set-caps` with the figures in section 3 | BLOCKED (not run). The gate itself passed: M2-TASK-02 live test, 2 passed |
| `/api/health` shows `budget_remaining` | `GET /api/health` after `set-caps` and `LIVE_SCANS_ENABLED=true` | BLOCKED |
| Dataset commit rate is acceptable | Ledger commits during a short live scan | DEFERRED: needs a live scan, which spends within the caps. Not requested for attempt 1 |
| Keepalive `workflow_dispatch` against the Space URL | Actions run | DEFERRED to M2-TASK-11 (private URL needs auth; no workflow triggered) |
| M2-TASK-02 HF ledger live gate (`PIR_LIVE_TESTS=1 pytest tests/live/test_live_hf_ledger.py`) | Run before the deploy continues | PASS on 2026-10-26 (2 passed, 6.84 s) |

Results go in the M2-TASK-08 entry of `docs/QA_RESULTS.json` and in this table.

## 6. Rollback

- **Pause live spend:** set `LIVE_SCANS_ENABLED=false` as a Space variable and restart the Space. Replays keep working and need no keys.
- **Code:** revert the Space repository to the previous good commit with a new commit. Do not force-push without the user's approval.
- **Spend state** lives in the HF ledger dataset and is not touched by a code rollback. Caps are in ledger state, so the ledger survives a rollback.

## 7. Open points for the user

- **Hosting plan:** Docker Spaces need a Hugging Face PRO subscription on this account (section 3). Choose PRO or another host; part B resumes after the choice.
- **Commit the ADR-0036 changes.** `Dockerfile`, `.dockerignore` and `tests/unit/test_packaging.py` are uncommitted. The agent does not commit.
- **`.env`:** add `SPEND_LEDGER_BACKEND=hf_dataset` (section 3).
- **Eval figure:** `latest.json` ships in the image (ADR-0036). Updating the figure means committing a new `latest.json` and rebuilding the Space.
- **Build tooling.** `pip install --no-deps .` builds with setuptools (the build backend) from build isolation. It is not in the hashed lock, because it is not a runtime dependency.
- **Docker unavailable** on 2026-10-26; the image has not been built or run locally (section 2).
