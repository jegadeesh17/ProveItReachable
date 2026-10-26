# Deployment

Hosting per ARCHITECTURE section 18 (ADR-0013, ADR-0030, ADR-0033). Built and checked offline on 2026-10-26 (M2-TASK-08, part A).

**Status:** the offline part is done. The deploy part (Space creation, secrets, `ledger set-caps`, deploy checks) is **PENDING** and has not been run. Nothing has been deployed, no Space exists and no remote was written to.

## 1. What is built

- **Dockerfile:** `FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962` (multi-arch index digest; its linux/amd64 manifest is `sha256:d879ed88565a7a3c614b980b369d4a5025a9d057b71f93d09fc6d0b2ca823a18`).
  - Verified 2026-10-26 two ways: `docker buildx imagetools inspect python:3.13-slim` printed the index digest, and the Docker Hub registry manifest API (`HEAD .../library/python/manifests/3.13-slim`, `docker-content-digest` header) returned the same value.
- Runs as uid 1000. Command: `python -m pir serve --host 0.0.0.0 --port 7860` (one Uvicorn worker, JSON logs).
- Installs: `pip install --no-cache-dir --require-hashes -r requirements.lock`, then `pip install --no-cache-dir --no-deps .`.
- Copies only `pyproject.toml`, `README.md`, `LICENSE`, `src/`, `web/` and `demo/` (the replays). `.dockerignore` also excludes `.env`, `.env.*` (except `.env.example`), `data/`, `eval/results/`, `.venv/` and `.git/`.
- **requirements.lock** (committed) is regenerated with `uv pip compile pyproject.toml --universal --generate-hashes --python-version 3.13 -o requirements.lock` (uv 0.13.5 from the dev extra). A fast test checks that every runtime dependency is in it at its pin with a `--hash`.

## 2. Local build and run (needs a running Docker daemon)

```bash
docker build -t proveitreachable:local .
docker run --rm -p 127.0.0.1:7860:7860 -e LIVE_SCANS_ENABLED=false proveitreachable:local
curl -s http://127.0.0.1:7860/api/health
docker run --rm --entrypoint sh proveitreachable:local -c 'test ! -e /app/.env && test ! -e /app/eval/results && ls -ld /app/data'
```

`LIVE_SCANS_ENABLED=false` lets `serve` start with no keys.

**Result on 2026-10-26: NOT RUN.** The Docker CLI (29.6.2) is installed, but the daemon is not running (`docker info` could not reach the Docker Desktop Linux engine pipe). So the image build, the no-`.env` / no-`data/` / no-`eval/results/` check, and the loopback run of `/` and `/api/health` are all still to do. Start Docker Desktop and run the commands above before part B.

## 3. Hugging Face Space (part B, PENDING)

- **Space:** private, SDK Docker, under the user's Hugging Face account. Proposed name: `proveitreachable`. Visibility is set at creation and changed only in M2-TASK-11.
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

- **Space secrets** (values typed in the Space settings, never in files): `NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID`, `TAVILY_API_KEY`, `HF_TOKEN` (fine-grained, write access to the ledger dataset only), `LANGSMITH_API_KEY`, and optionally `GITHUB_TOKEN`.
- **Space variables** (not secret): `LIVE_SCANS_ENABLED`, `SPEND_LEDGER_BACKEND=hf_dataset`, `SPEND_LEDGER_HF_REPO` (`<user>/pir-spend-ledger`), `TRUSTED_PROXY_HOPS` (default `0`; set to the hop count the XFF check confirms, expected `1`), `LANGSMITH_TRACING=true`, `LOG_LEVEL`, and the two prices below.
- **Prices** (set as Space variables; read 2026-10-26):
  - `NEBIUS_PRICE_INPUT_PER_MTOK` and `NEBIUS_PRICE_OUTPUT_PER_MTOK`: the Token Factory public catalog (`https://tokenfactory.nebius.com/api/public/models_info`) lists `nvidia/nemotron-3-super-120b-a12b` at **0.30** input and **0.90** output USD per million tokens (cached input 0.06). The code defaults are the pessimistic 0.60 and 2.40. Confirm in the Token Factory console; the user decides which value to set.
  - `TAVILY_PRICE_PER_CREDIT_USD`: `https://tavily.com/pricing`, Pay As You Go, **$0.008 per credit** (the code default).
- **Balances:** the real Token Factory and Tavily balances come from the user's consoles and are never guessed. Part B runs `python -m pir ledger set-caps --nebius-balance <USD> --tavily-balance <USD>` with the HF ledger settings, after the stale-parent gate passes (ARCHITECTURE 11.1). It has not been run.

## 4. Keepalive and spend alert

- `.github/workflows/keepalive.yml`: schedule `17 */6 * * *` and `workflow_dispatch`. It runs `python -I src/pir/keepalive.py "$PIR_HEALTH_URL"`.
- GitHub **repository variable** `PIR_HEALTH_URL` (not a secret): `https://<hf-owner>-<space-name>.hf.space/api/health`. Set it when the Space goes public (M2-TASK-11).
- Exit codes: `0` health ok and every pool at or above 0.25; `1` a pool is below 0.25 (the output names it, for example `Budget alert: nebius at 0.18 (below 0.25)`); `2` health check failed (non-200, invalid JSON, status not ok, `budget_remaining` null, a non-https URL, or no answer after 3 tries 20 s apart). A failed run makes GitHub email the owner.
- A private Space does not answer without a token, so the `workflow_dispatch` run against it is deferred to M2-TASK-11 unless the URL is reachable.

## 5. Deploy checks (part B): all PENDING

| Check | How | Status |
|---|---|---|
| Built image has no `.env` | `docker run --rm --entrypoint sh <image> -c 'test ! -e /app/.env'` | PENDING (Docker not running on 2026-10-26) |
| Built image has nothing from `data/` or `eval/results/` | the same `sh` check on `/app/data` and `/app/eval/results` | PENDING |
| Image runs; `/` and `/api/health` respond on loopback | section 2 | PENDING |
| Private Space serves `/`; `/api/health` returns `status: ok` | `curl` the Space URL with the owner's access | PENDING |
| Demo replay streams through the HF proxy (SSE if enabled) | open a demo in the Space | PENDING |
| `X-Forwarded-For` hop count: a spoofed XFF and the logged client hash match the real address; `TRUSTED_PROXY_HOPS` set to match (default 0) | send a request with a spoofed leftmost XFF and compare the logged `client` hash | PENDING |
| `frame-ancestors` allows the HF embed | response header check | PENDING |
| Dataset commit rate is acceptable | ledger commits during a short scan | PENDING |
| `ledger set-caps` succeeded (stale-parent gate passed); `/api/health` shows `budget_remaining` | command output and `/api/health` | PENDING |
| Keepalive `workflow_dispatch` passes against the Space URL | Actions run | PENDING, or deferred to M2-TASK-11 if the Space is private |

Results go in the M2-TASK-08 entry of `docs/QA_RESULTS.json` and in this table.

## 6. Rollback

- **Pause live spend:** set `LIVE_SCANS_ENABLED=false` as a Space variable and restart the Space. Replays keep working and need no keys.
- **Code:** revert the Space repository to the previous good commit with a new commit. Do not force-push without the user's approval.
- **Spend state** lives in the HF ledger dataset and is not touched by a code rollback. Caps are in ledger state, so the ledger survives a rollback.

## 7. Open points for the user

- **No eval figure on the Space.** `.dockerignore` excludes `eval/results/` (finding 10), so the image has no `latest.json`. `load_eval_figure` then returns `None`, and the UI shows no eval figure (ARCHITECTURE 15). Decide whether to publish the figure another way.
- **Build tooling.** `pip install --no-deps .` builds with setuptools (the build backend) from build isolation. It is not in the hashed lock, because it is not a runtime dependency.
- **Docker unavailable** on 2026-10-26; the image has not been built or run yet (section 2).
