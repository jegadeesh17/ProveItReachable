# Deployment

Hosting per ARCHITECTURE section 18 (ADR-0040, which supersedes ADR-0013; ADR-0030, ADR-0033 and ADR-0036 for the image; ADR-0020 for secrets). Part A (offline build) was done on 2026-10-26 (M2-TASK-08, commit 9a013b7). Part B attempt 1 (Hugging Face Space) was blocked by HTTP 402 (a Docker Space needs a PRO subscription). Part B attempt 2 (Google Cloud Run) was run on 2026-10-26.

**Status (2026-10-26, part B attempt 2): DEPLOYED, PRIVATE, live scans enabled.** Nothing is public. The service has no `allUsers` or `allAuthenticatedUsers` binding. M2-TASK-11 (go public) is not done.

| Item | Value |
|---|---|
| Google Cloud project | `proveitreachable` (project number `760189264289`), on billing account `0156C0-992061-82318D` (billing currency INR). The requested ID was free, so the fallback ID was not needed. |
| Region | `us-central1` |
| Service | `proveitreachable` |
| Service URL | `https://proveitreachable-ynjbf5demq-uc.a.run.app` (the project-number form `https://proveitreachable-760189264289.us-central1.run.app` is the same service) |
| Live revision | `proveitreachable-00004-5nx` (100% of traffic) |
| Image | `us-central1-docker.pkg.dev/proveitreachable/cloud-run-source-deploy/proveitreachable@sha256:c6204caedb238a7d4d312194129253911453d7ad5dfbb0918c7e246917001b3f` (built by Cloud Build from `git archive HEAD`; commit `95a058b72459`, stored as the `commit` label) |
| Runtime service account | `pir-run@proveitreachable.iam.gserviceaccount.com` |
| Spend ledger | Hugging Face dataset `jegadeesh17/pir-spend-ledger` (private), unchanged |

## 1. What is deployed

- **Image:** unchanged `Dockerfile` (`FROM python:3.13-slim@sha256:d98cc2...`, non-root uid 1000, `python -m pir serve --host 0.0.0.0 --port 7860`). Cloud Run listens on port 7860 (`--port 7860`).
- **Build input:** `git -c core.autocrlf=false archive HEAD` unpacked into an empty folder in the temp directory. The folder held no `.env*` file other than `.env.example` and no `data/`. Uncommitted work is not uploaded.
- **Service settings:** max instances 1, min instances 0 (no min annotation, the Cloud Run default), 1 vCPU, 1 GiB, concurrency 80, CPU throttling on (request-based billing), request timeout 1200 s, startup CPU boost on (`run.googleapis.com/startup-cpu-boost: true` on the service; the deploy command did not set it, so it is a platform default this run did not check), ingress `all` with IAM restricting invocation.
- **Secrets:** Secret Manager, one enabled version each, injected as environment variables with `--set-secrets NAME=NAME:latest`: `NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID`, `TAVILY_API_KEY`, `HF_TOKEN`, `LANGSMITH_API_KEY`. `GITHUB_TOKEN` was not set in `.env`, so it was not stored. Each secret grants `roles/secretmanager.secretAccessor` to `pir-run` only.
- **Plain environment variables:** `SPEND_LEDGER_BACKEND`, `SPEND_LEDGER_HF_REPO`, `TRUSTED_PROXY_HOPS`, `NEBIUS_PRICE_INPUT_PER_MTOK`, `NEBIUS_PRICE_OUTPUT_PER_MTOK`, `TAVILY_PRICE_PER_CREDIT_USD`, `LANGSMITH_TRACING`, `LOG_LEVEL`, `LIVE_SCANS_ENABLED` and `SANDBOX_BASE_IMAGE` (section 9 explains the last one, which was added after the first deploy). Values are in ARCHITECTURE section 18 and in `.env.example`; none is a secret.
- **Logs:** the app's JSON lines go to Cloud Logging. The Cloud Logging exclusion `exclude-run-request-logs` (`log_id("run.googleapis.com/requests")`) on `_Default` drops Cloud Run request logs, which hold raw client IP addresses.
- **Cost guards:** budget `proveitreachable-5usd` on project `760189264289` (see section 3); Artifact Registry repository `cloud-run-source-deploy` with a cleanup policy (keep the 2 newest images, delete the rest).

## 2. Commands run

Run from the project root in Git Bash. Values captured from gcloud had `tr -d '\r'` applied. No command printed a secret or a token.

1. `gcloud projects create proveitreachable --name="ProveItReachable"`; `gcloud billing projects link proveitreachable --billing-account=0156C0-992061-82318D`.
2. `gcloud services enable run cloudbuild artifactregistry secretmanager billingbudgets` (`.googleapis.com`).
3. `gcloud billing budgets create --billing-account=0156C0-992061-82318D --billing-project=proveitreachable --display-name=proveitreachable-5usd --budget-amount=475INR --threshold-rule=percent=0.2 --threshold-rule=percent=1.0 --filter-projects=projects/760189264289` (the first attempt with `--budget-amount=5USD` returned HTTP 400; see section 3).
4. `gcloud artifacts repositories create cloud-run-source-deploy --repository-format=docker --location=us-central1`; `gcloud artifacts repositories set-cleanup-policies ... --policy=<JSON file in the temp folder> --no-dry-run`.
5. `gcloud logging sinks update _Default --add-exclusion='name=exclude-run-request-logs,filter=log_id("run.googleapis.com/requests")'`.
6. `gcloud iam service-accounts create pir-run`; `gcloud projects add-iam-policy-binding ... --member=serviceAccount:760189264289-compute@developer.gserviceaccount.com --role=roles/run.builder`.
7. `.venv/Scripts/python -I <temp>/put_secrets.py`: reads `.env` in-process with `python-dotenv`, pipes each value to `gcloud secrets create NAME --replication-policy=automatic --data-file=-` on stdin, prints only `NAME: created` or `NAME: not set`. Then `gcloud secrets add-iam-policy-binding NAME --role=roles/secretmanager.secretAccessor --member=serviceAccount:pir-run@proveitreachable.iam.gserviceaccount.com` for each of the five secrets.
8. Build folder: `git -c core.autocrlf=false archive --format=tar HEAD | tar -x -C <empty temp folder>`; assertions in section 4, check 1.
9. `gcloud run deploy proveitreachable --region=us-central1 --source=<build folder> --port=7860 --service-account=pir-run@proveitreachable.iam.gserviceaccount.com --no-allow-unauthenticated --min-instances=0 --max-instances=1 --cpu=1 --memory=1Gi --concurrency=80 --cpu-throttling --timeout=1200 --set-env-vars=<plain names above> --set-secrets=<five secrets> --update-labels=commit=95a058b72459 --quiet`. Result: revision `proveitreachable-00001-s9n`.
10. `gcloud builds submit --no-source --config=<temp>/imagecheck.yaml` (section 4, check 2).
11. `gcloud run services update proveitreachable --update-env-vars=TRUSTED_PROXY_HOPS=1` (revision `00002`), then `TRUSTED_PROXY_HOPS=0` (revision `00003`), for the XFF measurement (section 5).
12. `SPEND_LEDGER_BACKEND=hf_dataset .venv/Scripts/python -m pir ledger set-caps --nebius-balance 29.99 --tavily-balance 32.40 --nebius 10 --tavily 5` (exit 0; section 7).
13. `gcloud run services update proveitreachable --update-env-vars=LIVE_SCANS_ENABLED=true,SANDBOX_BASE_IMAGE=<base_image from src/pir/executor/base_image_fingerprint.json>` (revision `00004-5nx`, live).
14. Fast checks: `python -m ruff check .`, `python -m ruff format --check .`, `python -m pytest -q -m "not slow"` (in `.venv`). Results in section 8.

## 3. Budget and currency

- **Refused in USD.** The billing account's currency is INR (`gcloud billing accounts describe`), and the budget API returned HTTP 400 for `--budget-amount=5USD`. The existing budget `MonthlyBilling` on the same account is also in INR.
- **Used:** `--budget-amount=475INR`. $5 at the rate of 95.16 INR per USD (open.er-api.com, 2026-10-26 00:02 UTC) is about INR 476; INR 475 is about USD 4.99. The alerts are at 20% (INR 95, about USD 1.00) and 100% (INR 475, about USD 5.00), both on current spend for this project only.
- **Action for you:** the rate is an approximation. Adjust the budget in the console if you want an exact figure. Budget alerts email the billing account's administrators. They warn only and do not stop billing.

## 4. Deploy checks (part B, attempt 2)

| # | Check | How | Result and evidence |
|---|---|---|---|
| 1 | Build folder: no `.env*` except `.env.example`, no `data/` | `find` on the `git archive HEAD` folder | **PASS.** Only `./.env.example`; no `data/`. `eval/results/` held `latest.json` and `latest.md` in the upload (the Dockerfile copies only `latest.json`). |
| 2 | Deployed image: no `/app/.env`; `/app/data` empty; `/app/eval/results` holds only `latest.json`; no `.env*` under `/app` | One-off Cloud Build step on the deployed digest (`gcloud builds submit --no-source`), build ID `937e9572-8ee8-4f56-b42b-f60cc5d4c95e` | **PASS.** Step printed `IMAGE_CHECK_OK`, build status SUCCESS (14 s). |
| 3 | `GET /` returns 200 with the security headers | `curl` with the identity token as a Bearer header | **PASS.** HTTP 200. `content-security-policy` includes `frame-ancestors 'self' https://huggingface.co`; `x-content-type-options: nosniff`; `referrer-policy: no-referrer`. |
| 4 | `GET /api/health` reports ok | Same | **PASS.** `{"status":"ok","version":"0.1.0","live_scans":"enabled","budget_remaining":{"nebius":1.0,"tavily":1.0}}` (after section 7). |
| 5 | `GET /api/demos` lists the demos | Same | **PASS.** 3 demos: `planesweeppose`, `pyrl`, `co2tools`. |
| 6 | A demo replay streams to `event: done` | `POST /api/demos/co2tools/replay` (with `Content-Length: 0`), then `curl -N` the events URL | **PASS.** Scan `2d118c8015334df9bc5a097f0132c300`: 12 `step` events and `event: done`, stream 20 s. |
| 7 | Service config: max 1, min 0, CPU throttling, timeout 1200, service account `pir-run` | `gcloud run services describe` | **PASS.** `autoscaling.knative.dev/maxScale: 1`, minScale absent (0), `run.googleapis.com/cpu-throttling: true`, `timeoutSeconds: 1200`, `serviceAccountName: pir-run@proveitreachable.iam.gserviceaccount.com`. |
| 8 | No public access | `get-iam-policy` on the service and the project; anonymous `GET /` | **PASS.** No `allUsers` or `allAuthenticatedUsers` on either policy. Anonymous request: HTTP 403. |
| 9 | Cleanup policy present | `gcloud artifacts repositories list-cleanup-policies` | **PASS.** `keep-newest-2` (KEEP, 2 most recent) and `delete-rest` (DELETE, tag state any); dry run disabled. |
| 10 | Budget listed | `gcloud billing budgets describe` | **PASS.** `proveitreachable-5usd`, INR 475, project filter `projects/760189264289`, thresholds 0.2 and 1.0 (section 3). |
| 11 | XFF: `TRUSTED_PROXY_HOPS` measured | Section 5 | **Kept 0 (fail-safe).** The evidence did not show that the logged key follows the visitor. |
| 12 | `ledger set-caps` on the HF ledger (stale-parent gate) | Section 7 | **PASS.** Exit 0. |
| 13 | `/api/health` shows `budget_remaining` for both pools | Section 7 | **PASS.** `{"nebius":1.0,"tavily":1.0}`, with `LIVE_SCANS_ENABLED=true`. |
| 14 | Browser check through `gcloud run services proxy` | Optional in the plan | **NOT RUN** (optional; no browser session in this run). |
| 15 | Cold start after idle | Section 6 | **MEASURED:** about 20 s to the first byte after 16 minutes idle; warm requests about 0.5 s. |
| 16 | Keepalive `workflow_dispatch` | M2-TASK-11 | **DEFERRED** (private URL needs a token; no workflow triggered). |
| 17 | Live scan | Not requested | **NOT RUN** (the user asked for no live scan). |

## 5. XFF measurement and `TRUSTED_PROXY_HOPS`

With `TRUSTED_PROXY_HOPS=1` (revision `proveitreachable-00002-p68`), the app keys rate limits on the rightmost `X-Forwarded-For` entry (`src/pir/ratelimit.py`, `client_key`). The logged `client` is a salted hash per process (`replay_admitted` in `src/pir/api/routes.py`).

| Replay | Time (UTC) | `X-Forwarded-For` sent | Logged `client` |
|---|---|---|---|
| A | 07:49:05 | `203.0.113.7` | `c063a09b0f960065` |
| B | 07:49:31 (about 25 s later) | `198.51.100.9` | `c063a09b0f960065` |
| C, IPv6 (`curl -6`) | 07:50:35 | `203.0.113.7` | `c063a09b0f960065` |

- The spoofed leftmost value did not change the hash (A and B).
- The laptop has a global IPv6 address (`2409:40f4:...`) distinct from its IPv4 address, yet replay C (IPv6) logged the same hash as IPv4. So the key did not follow the visitor's address in this test.
- Two explanations fit: the rightmost entry is not the visitor address, or the key falls back to the connection address, which is the same for every request. The app does not log the header, so the two cannot be separated from the logs.
- A second-network comparison was not available to the agent (no Cloud Shell session). Under the rule to keep the fail-safe value when unsure, **`TRUSTED_PROXY_HOPS=0`** (set back on revision `proveitreachable-00003-d52`; the live revision keeps it).
- **Effect of 0:** every visitor shares one per-client rate-limit bucket. The replay bucket is 30 per hour (`RATE_LIMIT_REPLAYS_PER_CLIENT_HOUR`), so with many visitors the replays can hit `too_many_requests` (429) sooner. This is the trade-off of the fail-safe.
- **To set 1 safely:** with `TRUSTED_PROXY_HOPS=1` on one instance, POST one replay from this laptop and one from a second network (Cloud Shell or a phone hotspot) within the same instance lifetime. Compare the two `replay_admitted` hashes in `gcloud logging read`. Different hashes: keep 1. Equal hashes: keep 0.

## 6. Cold start

- **Measured 2026-10-26 (revision `proveitreachable-00004-5nx`), after at least 16 minutes with no request** (last request 07:57:29 UTC, next request 08:14:00 UTC):
  - First request `GET /api/health`: HTTP 200 in **21.5 s**. The log shows "Starting new instance" at 08:14:00.08 and the startup TCP probe on port 7860 succeeding at 08:14:20.89 (about 20.8 s from instance start to ready).
  - A second request, 2 s after the first response, also took **20.0 s**. Its log shows a second "Starting new instance" at 08:14:23.9 (ready at 08:14:43.25). The cause of the second start was not determined; the logs give only the reason "AUTOSCALING".
  - Warm requests then took 0.49 to 0.80 s (`/api/health`, `/api/demos`, `/`).
  - **Effect:** a visitor after an idle period waits about 20 s before the first byte. The keepalive check's 3 tries 20 s apart cover this. Reducing the start time would need a code or image change, which this task does not allow.

## 7. Spend caps (`ledger set-caps`)

Command: `SPEND_LEDGER_BACKEND=hf_dataset .venv/Scripts/python -m pir ledger set-caps --nebius-balance 29.99 --tavily-balance 32.40 --nebius 10 --tavily 5`. Exit 0.

Output (numbers only, from the run):
- Nebius cap $10.00; Tavily cap $5.00 (the approved values).
- Balances: Nebius $29.99 ($0.49 trial plus $29.50); Tavily $32.40 (4,050 credits at $0.008).
- Hosted allowances after the write: Nebius $5.00 (remaining $5.0000 of $5.00), Tavily $3.00 (remaining $3.0000 of $3.00). Local allowances: Nebius $5.00, Tavily $2.00. Recorded spend $0.0000 on both pools.
- The stale-parent gate passed. One commit returned HTTP 412 and the app's retry (up to 3 attempts) then succeeded, as designed.

Live scans were enabled after this, and `/api/health` reports `budget_remaining` `{"nebius":1.0,"tavily":1.0}`. No live scan was run.

## 8. Fast checks (local, on the current tree)

`python -m ruff check .`: all checks passed. `python -m ruff format --check .`: 133 files already formatted. `python -m pytest -q -m "not slow"`: 1730 passed, 3 skipped, 135 deselected. Recorded in `docs/QA_RESULTS.json` (M2-TASK-08, attempt 2, part B).

## 9. Environment changes after the first deploy

- Revision `00002`: `TRUSTED_PROXY_HOPS=1` (XFF measurement). Revision `00003`: back to `0`.
- Revision `00004-5nx`: `LIVE_SCANS_ENABLED=true` and `SANDBOX_BASE_IMAGE` added. **`SANDBOX_BASE_IMAGE` is not in the approved command list.** The sandbox executor raises `SANDBOX_BASE_IMAGE is not set` when it is missing (`src/pir/executor/contree.py`), so enabling live scans without it would break every scan. Its value is the public `base_image` from `src/pir/executor/base_image_fingerprint.json` (the same value `.env` must hold, per `.env.example`). It is not a secret. To undo it, remove it with `gcloud run services update ... --remove-env-vars=SANDBOX_BASE_IMAGE` (with `LIVE_SCANS_ENABLED=false` first).

## 10. Rollback

- **Pause live spend:** `gcloud run services update proveitreachable --region=us-central1 --update-env-vars=LIVE_SCANS_ENABLED=false` (a new revision; replays keep working and need no keys).
- **Previous revision:** `gcloud run services update-traffic proveitreachable --region=us-central1 --to-revisions=<previous>=100` (for example `proveitreachable-00001-s9n`, which has `LIVE_SCANS_ENABLED=false`, `TRUSTED_PROXY_HOPS=0` and no `SANDBOX_BASE_IMAGE`).
- **Make private again (if ever public):** `gcloud run services remove-iam-policy-binding proveitreachable --region=us-central1 --member=allUsers --role=roles/run.invoker`.
- **Spend state** lives in the HF ledger dataset and is not touched by any of these. The caps are in ledger state.
- **Secret rotation:** add a new Secret Manager version, disable the old one, and redeploy (ADR-0040).

## 11. M2-TASK-11 (go public): steps for later, after the user confirms

1. Redeploy from `git archive HEAD` after any change (the same build-folder procedure as section 2, step 8), then run the image check (section 4, check 2) on the new digest.
2. Grant public invoke access: `gcloud run services add-iam-policy-binding proveitreachable --region=us-central1 --member=allUsers --role=roles/run.invoker`. Only after the user says so.
3. Set the GitHub repository variable `PIR_HEALTH_URL` to `https://proveitreachable-ynjbf5demq-uc.a.run.app/api/health` (a variable, not a secret).
4. Dispatch the keepalive workflow once and check that it passes.
5. Resolve the `TRUSTED_PROXY_HOPS` question (section 5) before public traffic, since public traffic makes the shared rate-limit bucket matter.

## 12. Open points for the user

- Approve or change the INR budget amount (section 3).
- Decide the `TRUSTED_PROXY_HOPS` question with a second-network test (section 5). Until then, visitors share one per-client bucket.
- Live scans are enabled with the caps above. Confirm before the first live scan.
- The local `.env` should have `SPEND_LEDGER_BACKEND=hf_dataset` (see the attempt 1 notes in `docs/PLATFORM_FEEDBACK.md`). The agent did not edit `.env`.
- Nothing has been made public, and no GitHub setting or workflow was changed.
