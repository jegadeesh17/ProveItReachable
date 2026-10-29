# Deployment

Hosting per ARCHITECTURE section 18 (ADR-0040, which supersedes ADR-0013; ADR-0030, ADR-0033 and ADR-0036 for the image; ADR-0020 for secrets). Part A (offline build) was done on 2026-10-26 (commit 9a013b7). Part B attempt 1 (Hugging Face Space) was blocked by HTTP 402 (a Docker Space needs a PRO subscription). Part B attempt 2 (Google Cloud Run) was run on 2026-10-26.

**Status (2026-10-29, revision proveitreachable-00005-fwx): DEPLOYED, PRIVATE, live scans enabled.** Nothing is public. The service has no `allUsers` or `allAuthenticatedUsers` binding. Going public is not done.

| Item | Value |
|---|---|
| Google Cloud project | `proveitreachable` (project number `760189264289`), on billing account `0156C0-992061-82318D` (billing currency INR). The requested ID was free, so the fallback ID was not needed. |
| Region | `us-central1` |
| Service | `proveitreachable` |
| Service URL | `https://proveitreachable-ynjbf5demq-uc.a.run.app` (the project-number form `https://proveitreachable-760189264289.us-central1.run.app` is the same service) |
| Live revision | `proveitreachable-00005-fwx` (100% of traffic) |
| Image | `us-central1-docker.pkg.dev/proveitreachable/cloud-run-source-deploy/proveitreachable@sha256:77726535f0f69ce38617356fb4e3a70cc62a022b704cb0b0186ec74b2f2c10de` (built by Cloud Build from `git archive HEAD`; commit `5f0c3d7`, stored as the `commit` label) |
| Runtime service account | `pir-run@proveitreachable.iam.gserviceaccount.com` |
| Spend ledger | Hugging Face dataset `jegadeesh17/pir-spend-ledger` (private), unchanged |

## 1. What is deployed

- **Image:** unchanged `Dockerfile` (`FROM python:3.13-slim@sha256:d98cc2...`, non-root uid 1000, `python -m pir serve --host 0.0.0.0 --port 7860`). Cloud Run listens on port 7860 (`--port 7860`).
- **Build input:** `git -c core.autocrlf=false archive HEAD` unpacked into an empty folder in the temp directory. The folder held no `.env*` file other than `.env.example` and no `data/`. Uncommitted work is not uploaded.
- **Service settings:** max instances 1, min instances 0 (no min annotation, the Cloud Run default), 1 vCPU, 1 GiB, concurrency 80, CPU throttling on (request-based billing), request timeout 1200 s, startup CPU boost on (`run.googleapis.com/startup-cpu-boost: true` on the service; the deploy command did not set it, so it is a platform default this run did not check), ingress `all` with IAM restricting invocation.
- **Secrets:** Secret Manager, one enabled version each, injected as environment variables with `--set-secrets NAME=NAME:latest`: `NEBIUS_API_KEY`, `NEBIUS_PROJECT_ID`, `TAVILY_API_KEY`, `HF_TOKEN`, `LANGSMITH_API_KEY`. `GITHUB_TOKEN` was not set in `.env`, so it was not stored. Each secret grants `roles/secretmanager.secretAccessor` to `pir-run` only.
- **Plain environment variables:** `SPEND_LEDGER_BACKEND`, `SPEND_LEDGER_HF_REPO`, `TRUSTED_PROXY_HOPS`, `NEBIUS_PRICE_INPUT_PER_MTOK`, `NEBIUS_PRICE_OUTPUT_PER_MTOK`, `TAVILY_PRICE_PER_CREDIT_USD`, `LANGSMITH_TRACING`, `LOG_LEVEL`, `LIVE_SCANS_ENABLED` and `SANDBOX_BASE_IMAGE` (section 9 explains the last one, which was added after the first deploy). Values are in ARCHITECTURE section 18 and in `.env.example`; none is a secret.
- **Logs:** the app's JSON lines go to Cloud Logging. The Cloud Logging exclusion `exclude-run-request-logs` (`log_id("run.googleapis.com/requests")`) on `_Default` drops Cloud Run request logs, which hold raw client IP addresses.
- **Cost guards:** budget `proveitreachable-100inr` (INR 100; it replaced `proveitreachable-5usd`, see section 13) on project `760189264289`, plus the kill switch function (section 13); Artifact Registry repository `cloud-run-source-deploy` with a cleanup policy (keep the 2 newest images, delete the rest).

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
| 16 | Keepalive `workflow_dispatch` | go-public step | **DEFERRED** (private URL needs a token; no workflow triggered). |
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

`python -m ruff check .`: all checks passed. `python -m ruff format --check .`: 133 files already formatted. `python -m pytest -q -m "not slow"`: 1730 passed, 3 skipped, 135 deselected.

## 9. Environment changes after the first deploy

- Revision `00002`: `TRUSTED_PROXY_HOPS=1` (XFF measurement). Revision `00003`: back to `0`.
- Revision `00004-5nx`: `LIVE_SCANS_ENABLED=true` and `SANDBOX_BASE_IMAGE` added. **`SANDBOX_BASE_IMAGE` is not in the approved command list.** The sandbox executor raises `SANDBOX_BASE_IMAGE is not set` when it is missing (`src/pir/executor/contree.py`), so enabling live scans without it would break every scan. Its value is the public `base_image` from `src/pir/executor/base_image_fingerprint.json` (the same value `.env` must hold, per `.env.example`). It is not a secret. To undo it, remove it with `gcloud run services update ... --remove-env-vars=SANDBOX_BASE_IMAGE` (with `LIVE_SCANS_ENABLED=false` first).
- Revision `00005-fwx` (2026-10-29): Redeployed current main (commit `5f0c3d7`) from clean `git archive HEAD` build context. Configured active judging-window profile env vars per ADR-0044 and ADR-0045: `LANGSMITH_SHARE_TRACES=true` (public LangSmith share trace links with secret redaction and 1-line errors) and `RATE_LIMIT_REPLAYS_PER_CLIENT_HOUR=300` (expanded replay rate limit for shared bucket under `TRUSTED_PROXY_HOPS=0`). Retained existing service configuration: runtime service account `pir-run@proveitreachable.iam.gserviceaccount.com`, max instances 1, min instances 0, concurrency 80, CPU throttling on, request timeout 1200 s, `TRUSTED_PROXY_HOPS=0`, `LIVE_SCANS_ENABLED=true`, `SANDBOX_BASE_IMAGE`, and all secrets from Secret Manager. Image integrity verified via Cloud Build (`IMAGE_CHECK_OK`, build ID `d0533914-1bef-4c43-bc36-93b5fbcb22a5`). Authenticated `/api/health` and `/?demo=pyrl` verified HTTP 200. Private IAM policy enforced (`--no-allow-unauthenticated`, no public bindings).

## 10. Rollback

- **Pause live spend:** `gcloud run services update proveitreachable --region=us-central1 --update-env-vars=LIVE_SCANS_ENABLED=false` (a new revision; replays keep working and need no keys).
- **Previous revision:** `gcloud run services update-traffic proveitreachable --region=us-central1 --to-revisions=<previous>=100` (for example `proveitreachable-00001-s9n`, which has `LIVE_SCANS_ENABLED=false`, `TRUSTED_PROXY_HOPS=0` and no `SANDBOX_BASE_IMAGE`).
- **Make private again (if ever public):** `gcloud run services remove-iam-policy-binding proveitreachable --region=us-central1 --member=allUsers --role=roles/run.invoker`.
- **Spend state** lives in the HF ledger dataset and is not touched by any of these. The caps are in ledger state.
- **Secret rotation:** add a new Secret Manager version, disable the old one, and redeploy (ADR-0040).

## 11. Go public: steps for later, after the user confirms

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
## 13. Real-money cap (₹100)

**Status (2026-10-26, after cost-cap attempt 2): ARMED.** The kill switch is live with `DRY_RUN=false` and `KILL_AT_COST=80`. Any budget message with `costAmount` of 80 or more unlinks billing on `proveitreachable`. Billing is still enabled. The unlink itself was **not tested**, because testing it would unlink billing (section 13.6).

### 13.1 Budget

| Item | Value |
|---|---|
| Name | `proveitreachable-100inr` (budget ID `d814a266-4b54-4ca8-aced-88995c04f0ae`, updated in place; it replaced `proveitreachable-5usd`) |
| Amount | INR 100 (the billing account's currency is INR) |
| Scope | project `proveitreachable` (`projects/760189264289`), calendar month, all credits included |
| Thresholds | 50%, 80%, 90% and 100% of `CURRENT_SPEND` (actual spend) |
| Notifications | Pub/Sub topic `projects/proveitreachable/topics/billing-kill`. Email alerts to billing admins stay on (the default recipients were not disabled). |

Commands run:
- `gcloud services enable cloudfunctions.googleapis.com eventarc.googleapis.com cloudbilling.googleapis.com --project=proveitreachable` (Pub/Sub, Cloud Build, Run and Artifact Registry were already on).
- `gcloud pubsub topics create billing-kill --project=proveitreachable`.
- `gcloud billing budgets update d814a266-4b54-4ca8-aced-88995c04f0ae --billing-account=0156C0-992061-82318D --billing-project=proveitreachable --display-name=proveitreachable-100inr --budget-amount=100INR --clear-threshold-rules --add-threshold-rule=percent=0.5,basis=current-spend --add-threshold-rule=percent=0.8,basis=current-spend --add-threshold-rule=percent=0.9,basis=current-spend --add-threshold-rule=percent=1.0,basis=current-spend --notifications-rule-pubsub-topic=projects/proveitreachable/topics/billing-kill`. Checked with `gcloud billing budgets describe`.
- `--billing-project=proveitreachable` is needed because the active application-default quota project (`32555940559`) does not have the Budgets API enabled.

### 13.2 Kill switch (Google's "Disable billing usage with notifications" pattern)

Pattern from `https://cloud.google.com/billing/docs/how-to/disable-billing-with-notifications` (last updated 2026-10-22). The IAM step differs from the doc, as described in 13.2.1.

| Item | Value |
|---|---|
| Function | `billing-kill-switch`, Cloud Run functions gen2, region `us-central1`, runtime `python313`, entry point `stop_billing`, revision `billing-kill-switch-00004-zav` (armed 2026-10-26 09:40:53 UTC) |
| Trigger | Pub/Sub topic `billing-kill` (budget notifications). Subscription `eventarc-us-central1-billing-kill-switch-164276-sub-684`. |
| Runtime service account | `billing-kill@proveitreachable.iam.gserviceaccount.com` |
| Project role | **`roles/billing.projectManager` on project `proveitreachable` only** (`gcloud projects add-iam-policy-binding proveitreachable --member=serviceAccount:billing-kill@proveitreachable.iam.gserviceaccount.com --role=roles/billing.projectManager`). The role contains two permissions: `resourcemanager.projects.createBillingAssignment` and `resourcemanager.projects.deleteBillingAssignment` (checked with `gcloud iam roles describe`). |
| Billing account role | **None.** Nothing was granted on billing account `0156C0-992061-82318D` (checked: no binding for the service account). |
| Invoker | `roles/run.invoker` on the function's own Cloud Run service `billing-kill-switch` only. The Pub/Sub push needs it, and the deploy did not grant it. |
| Scaling and resources | min 0, max 1, 256 MiB (128 MiB failed at startup, 130 MiB used), timeout 60 s, `--no-allow-unauthenticated`, retry policy from the trigger |
| Environment | `KILL_AT_COST=80` (INR), `DRY_RUN=false` (armed). A missing `DRY_RUN` means dry run. |
| Project ID | Read from the metadata server (`http://metadata.google.internal/computeMetadata/v1/project/project-id`, header `Metadata-Flavor: Google`). `GOOGLE_CLOUD_PROJECT` is the fallback. |
| Logic | Parse `costAmount` and `budgetAmount`. If cost is below `KILL_AT_COST`, log "no action". If billing reads as disabled, log "no action". If `DRY_RUN` is true, log "would disable billing". Otherwise call `update_project_billing_info` with an empty billing account. If the call is refused, log `ERROR failed to disable billing: permission denied` and re-raise. |
| Failure of the billing read | The function assumes billing is enabled (Google's doc fallback). The service account cannot read billing info, so this path is what runs today (see the DRY_RUN evidence). |
| Cost | Min 0 means no idle charge. Each budget message is one short invocation. Pub/Sub, Cloud Run functions, Cloud Build and Artifact Registry should stay inside their free tiers at this volume. |
| Image cleanup | Repository `gcf-artifacts` (us-central1): cleanup policy `keep-newest-2` (KEEP the 2 most recent) and `delete-rest` (DELETE, tag state any), applied with `--no-dry-run`, the same policy as `cloud-run-source-deploy`. |
| APIs enabled for this | `cloudfunctions`, `eventarc`, `cloudbilling`. Also `policytroubleshooter` (for the read-only IAM check in 13.6, which did not work). |

#### 13.2.1 Why the project role and not Google's billing.admin

Google's doc grants `roles/billing.admin` on the billing account. That is account-level admin over every project on the account. The coordinator and the user chose `roles/billing.projectManager` bound at project level only, so nothing is granted on the billing account. Whether this is enough to unlink is not verified (13.6).

Deploy command used for the armed state (the same source folder, `DRY_RUN=false`):

```
gcloud functions deploy billing-kill-switch --gen2 --project=proveitreachable --region=us-central1 --runtime=python313 --source=<folder with main.py and requirements.txt> --entry-point=stop_billing --trigger-topic=billing-kill --trigger-service-account=billing-kill@proveitreachable.iam.gserviceaccount.com --service-account=billing-kill@proveitreachable.iam.gserviceaccount.com --min-instances=0 --max-instances=1 --memory=256Mi --timeout=60s --no-allow-unauthenticated --set-env-vars=KILL_AT_COST=80,DRY_RUN=false --quiet
gcloud run services add-iam-policy-binding billing-kill-switch --region=us-central1 --project=proveitreachable --member=serviceAccount:billing-kill@proveitreachable.iam.gserviceaccount.com --role=roles/run.invoker
```

Source (`main.py`, the deployed version; it holds no secret). `requirements.txt` follows.

```python
"""Budget kill switch: disable billing on this project when spend reaches KILL_AT_COST.

Pattern: Google Cloud "Disable billing usage with notifications"
(cloud.google.com/billing/docs/how-to/disable-billing-with-notifications).
Trigger: Pub/Sub topic `billing-kill`, which receives the budget notifications.
"""

import base64
import json
import os
import urllib.request

import functions_framework
from cloudevents.http.event import CloudEvent
from google.api_core import exceptions
from google.cloud import billing_v1

KILL_AT_COST = float(os.environ.get("KILL_AT_COST", "80"))
# Fail safe: a missing DRY_RUN means dry run, so the function never acts by accident.
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() == "true"
METADATA_PROJECT_ID_URL = "http://metadata.google.internal/computeMetadata/v1/project/project-id"

billing_client = billing_v1.CloudBillingClient()


def _project_id() -> str:
    # Read the project ID from the metadata server (as in Google's doc sample).
    # Fall back to GOOGLE_CLOUD_PROJECT if the metadata server cannot be reached.
    try:
        request = urllib.request.Request(
            METADATA_PROJECT_ID_URL, headers={"Metadata-Flavor": "Google"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            project_id = response.read().decode("utf-8").strip()
        if project_id:
            return project_id
    except OSError as exc:
        print(f"WARNING metadata server unavailable ({type(exc).__name__})", flush=True)
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project_id:
        raise RuntimeError("project ID not found in metadata server or GOOGLE_CLOUD_PROJECT")
    return project_id


def _billing_enabled(project_name: str) -> bool:
    try:
        response = billing_client.get_project_billing_info(name=project_name)
        return response.billing_enabled
    except Exception as exc:  # same fallback as the Google doc: assume enabled
        print(
            f"WARNING could not read billing info ({type(exc).__name__}); "
            "assuming billing is enabled",
            flush=True,
        )
        return True


def _disable_billing(project_name: str) -> None:
    try:
        billing_client.update_project_billing_info(
            name=project_name,
            project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name=""),
        )
    except exceptions.PermissionDenied:
        print("ERROR failed to disable billing: permission denied", flush=True)
        raise
    print(f"billing disabled on {project_name}", flush=True)


@functions_framework.cloud_event
def stop_billing(cloud_event: CloudEvent) -> None:
    try:
        payload = json.loads(
            base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8")
        )
        cost = float(payload["costAmount"])
        budget = float(payload["budgetAmount"])
    except (KeyError, TypeError, ValueError) as exc:
        print(f"invalid budget message ({type(exc).__name__}): no action", flush=True)
        return

    print(
        f"costAmount={cost} budgetAmount={budget} "
        f"KILL_AT_COST={KILL_AT_COST} DRY_RUN={DRY_RUN}",
        flush=True,
    )
    if cost < KILL_AT_COST:
        print("no action: costAmount is below KILL_AT_COST", flush=True)
        return

    project_name = f"projects/{_project_id()}"
    if not _billing_enabled(project_name):
        print(f"no action: billing is already disabled on {project_name}", flush=True)
        return
    if DRY_RUN:
        print(f"would disable billing on {project_name} (DRY_RUN=true, nothing changed)", flush=True)
        return
    _disable_billing(project_name)
```

`requirements.txt`:

```
functions-framework==3.10.2
google-cloud-billing==1.21.0
```

### 13.3 DRY_RUN test evidence

**Attempt 1 (FAILED, kept as history):** the function read `GOOGLE_CLOUD_PROJECT`, which this python313 gen2 runtime does not set. The costAmount-85 message raised `KeyError: 'GOOGLE_CLOUD_PROJECT'` at `main.py` line 26 (`08:43:23.868251Z`, retry at `08:44:49`). "would disable billing" was never logged. The costAmount-10 test passed then (`08:43:58.850546Z no action: costAmount is below KILL_AT_COST`). Nothing was unlinked.

**Attempt 2 (PASS, metadata fix, DRY_RUN=true, project ID resolved from the metadata server):** the test messages were published with `gcloud pubsub topics publish billing-kill`. The log lines are from `gcloud logging read` (stdout and stderr):

| Test | Expected | Result |
|---|---|---|
| costAmount 10, budgetAmount 100 | "no action" | **PASS.** `2026-10-26T09:34:36.202051Z costAmount=10.0 budgetAmount=100.0 KILL_AT_COST=80.0 DRY_RUN=True`, then `2026-10-26T09:34:36.202127Z no action: costAmount is below KILL_AT_COST`. |
| costAmount 85, budgetAmount 100 | "would disable billing", project resolved | **PASS.** `2026-10-26T09:35:30.907462Z costAmount=85.0 budgetAmount=100.0 KILL_AT_COST=80.0 DRY_RUN=True`, then `2026-10-26T09:35:31.568971Z WARNING could not read billing info (PermissionDenied); assuming billing is enabled`, then `2026-10-26T09:35:31.569208Z would disable billing on projects/proveitreachable (DRY_RUN=true, nothing changed)`. |

The billing-info read returns PermissionDenied for this service account. The projectManager role does not include a billing-info read, so the function assumes billing is enabled. The unlink call would then be the first real billing call.

Another budget message with costAmount 0.0 arrived at 09:08:30 and 09:30:23 (real budget messages, not tests). Each logged "no action".

### 13.4 Backlog (subscription) evidence

Trigger subscription `eventarc-us-central1-billing-kill-switch-164276-sub-684` is a push subscription, so the backlog was purged by seeking it to a time:
- Seek to `2026-10-26T09:31:16Z`. Metric `pubsub.googleapis.com/subscription/num_undelivered_messages` read 0 for 09:30 to 09:33.
- DRY_RUN tests published at 09:34 and 09:35 (both acked and logged).
- Seek to `2026-10-26T09:37:09Z`. Metric read 0 for 09:34 to 09:38, and 0 for 09:39 to 09:42 after arming.

No message at or above 80 was pending when the switch was armed.

### 13.5 Final state (2026-10-26, about 09:43 UTC)

| Check | Result |
|---|---|
| Billing on `proveitreachable` | **Enabled.** `gcloud billing projects describe proveitreachable --format="value(billingAccountName,billingEnabled)"` gives `billingAccounts/0156C0-992061-82318D True`. |
| Project IAM for `billing-kill` | `roles/billing.projectManager` only (`gcloud projects get-iam-policy`). |
| Billing account IAM for `billing-kill` | None. |
| Function env | `DRY_RUN: 'false'`, `KILL_AT_COST: '80'`, state `ACTIVE`, service account `billing-kill@...`. |
| App | `GET /api/health` on the Cloud Run service, with an identity token: HTTP 200, `{"status":"ok","version":"0.1.0","live_scans":"enabled","budget_remaining":{"nebius":0.876351227,"tavily":1.0}}`. |
| Backlog | 0 (metric, 09:39 to 09:42). |
| Budget | INR 100, thresholds 50/80/90/100 (current spend), topic `billing-kill`. |

**Nebius `budget_remaining`.** The value was 1.0 in section 4 (check 4) and section 7. It was 0.923 at about 08:50 UTC and is 0.876 at 09:43 UTC. The 0.923 is explained by the eval run, which spent $0.7686 on the shared HF ledger: 1 − 0.7686/10 ≈ 0.923. The further drop to 0.876 is not covered by that figure (it implies about $1.24 spent on the $10 cap). This task ran no scan. Check the ledger before the next live scan.

### 13.6 Re-enabling billing after a trip

If the kill switch fires, billing must be re-linked manually. Services stay stopped until then:

```
gcloud billing projects link proveitreachable --billing-account=0156C0-992061-82318D
```

Caveat: budget data can lag a few hours behind actual spend, and spend keeps accruing until billing is actually disabled. That is why the kill threshold is INR 80 (not INR 100): the gap leaves INR 20 for the lag. The threshold alone does not guarantee the total stays under INR 100.

### 13.7 What is not verified

- **The unlink itself has not been tested.** Testing it would unlink billing on the real project. The read-only IAM check was attempted with Policy Troubleshooter (`policytroubleshooter.googleapis.com`, enabled on `proveitreachable`, quota project set in the request header). It returned HTTP 404 "Requested entity was not found" even for `resourcemanager.projects.get`, so it could not confirm `resourcemanager.projects.deleteBillingAssignment` or `updateBillingInfo`. The gcloud `policy-troubleshoot` command failed with the default ADC quota project.
- **Whether `roles/billing.projectManager` at project level is enough** for `projects.updateBillingInfo` (with an empty account) is not verified. The role's two permissions are the link and unlink assignment permissions. Google's doc grants billing-account-level admin instead.
- **Risk:** if the unlink is refused, billing stays on and the function logs `ERROR failed to disable billing: permission denied`. The kill switch then fails silently for the user, apart from the log line. Watch the logs after any budget alert at or above 80.
- Budget messages come from the budget service, not from the function. A cost reading that lags spend can still let spend pass INR 100 (see 13.6).
- To disarm: `gcloud functions deploy ... --set-env-vars=KILL_AT_COST=80,DRY_RUN=true` (same flags as above) or remove the project binding with `gcloud projects remove-iam-policy-binding proveitreachable --member=serviceAccount:billing-kill@proveitreachable.iam.gserviceaccount.com --role=roles/billing.projectManager`.

## 14. Active judging-window budget profile (ADR-0044, ADR-0045)

To support evaluation across the full hackathon judging window (December 1–15, 2026) within the remaining ~$8 Nebius promo credits (Tavily has ample credit surplus), the production budget profile is configured as follows:

| Dimension | Specification | Notes / Rationale |
|---|---|---|
| **Remaining credit pool** | ~$8.00 (Nebius) | Promo credits expiring 2026-12-15; Tavily has ample credit surplus |
| **Target end date** | **2026-12-15** (`SPEND_END_DATE=2026-12-15`) | Covers the complete active judging window (Dec 1–15, 2026) through credit expiration |
| **Minimum scan start threshold** | **$0.05** (`SCAN_MIN_START_USD=0.05`) | Admission threshold required before reservation to avoid premature lockout |
| **Daily ceiling with carry-forward** | `(allowance - start_spent) / days_left` | Calculated daily; unspent budget rolls forward daily into subsequent days |
| **Per-scan reservation** | **$0.75** (Nebius) / **$0.40** (Tavily) | Preserved unchanged; provides full scanning & proof-building capacity |
| **Global daily scan limit** | **10 live scans / day** (`RATE_LIMIT_GLOBAL_DAY=10`) | Enforces global daily quota; client limits 3/hr and 10/day |
| **Replay rate limit** | **300 replays / hr** (`RATE_LIMIT_REPLAYS_PER_CLIENT_HOUR=300`) | $0 cost; shared bucket under `TRUSTED_PROXY_HOPS=0` prevents judge lockout |
| **Trace sharing** | `LANGSMITH_SHARE_TRACES=true` (ADR-0045) | Public links via `usage.trace_url`; secrets redacted, 1-line errors, runtime info omitted |
| **Fail-safe fallback** | Graceful pause (`HTTP 429` / `daily_limit` / `paused`) | Zero-cost demo replays with real Sandbox proofs remain 100% accessible |

### 14.1 Dynamic daily ceiling & carry-forward pacing

Rather than a static daily ceiling that locks out scans on low-budget days or forfeits unspent funds on quiet days, the production profile uses a dynamic daily ceiling with automatic carry-forward:
1. **Dynamic ceiling formula**: At the first reservation of each UTC day, the meter computes:
   `ceiling = (allowance - start_spent) / days_left`
   where `days_left = max(1, (SPEND_END_DATE - today).days + 1)`, inclusive of 2026-12-15.
2. **Carry-forward mechanics**: On days with minimal judge traffic, unspent budget is not lost. It remains in `allowance - start_spent`, automatically expanding the daily ceiling for subsequent days as judging activity intensifies.
3. **Scan admission check**: A live scan is admitted only when:
   - In both pools, `spent + reserved + SCAN_MIN_START_USD <= allowance`; and
   - For `hosted`, today's `spent + reserved - start_spent + SCAN_MIN_START_USD <= ceiling`.
   Setting `SCAN_MIN_START_USD=0.05` ensures scans are not blocked unnecessarily when remaining budget is close to nominal per-scan reservations.
4. **Full fidelity scans preserved**: The per-scan reservation remains $0.75 (Nebius) and $0.40 (Tavily), ensuring that every admitted live scan runs AST indexing, deep model reasoning with Nemotron, and Sandbox verification at full fidelity.

### 14.2 Concurrency, rate limits, and reverse proxy interaction

1. **Global and per-client scan limits**:
   - `RATE_LIMIT_GLOBAL_DAY=10`: Caps global live scans at 10 per UTC day, acting alongside the dynamic spend ceiling to protect promo credits.
   - Per-client limits: 3 live scans per client per hour (`RATE_LIMIT_PER_CLIENT_HOUR=3`) and 10 per day (`RATE_LIMIT_PER_CLIENT_DAY=10`), with up to 2 concurrent scans (`MAX_CONCURRENT_SCANS=2`).
2. **Replay limit under `TRUSTED_PROXY_HOPS=0`**:
   - Google Cloud Run deployment retains `TRUSTED_PROXY_HOPS=0` as the fail-safe setting (section 5), meaning all incoming visitor requests share a single client rate-limit bucket.
   - To prevent multiple concurrent judges from encountering rate limiting while exploring demo replays, `RATE_LIMIT_REPLAYS_PER_CLIENT_HOUR=300` raises the client replay bucket to 300 starts per hour (up from 30).
   - Replays are 100% zero-cost: they read pre-recorded executions, execute no live LLM or Sandbox calls, require no spend reservations, and bypass the spend ledger entirely.

### 14.3 Public trace sharing & auditability (ADR-0045)

With `LANGSMITH_SHARE_TRACES=true` enabled on hosted Cloud Run:
1. **Public trace link**: Every live scan generates a public LangSmith share link attached to `report.usage.trace_url`, allowing judges to inspect the complete reasoning and tool-calling execution tree directly.
2. **Strict redaction via anonymizer**: The LangSmith client's `anonymizer` masks configured secrets (`NEBIUS_API_KEY`, `TAVILY_API_KEY`, `HF_TOKEN`, `LANGSMITH_API_KEY`, `GITHUB_TOKEN`) and any `Authorization` or `Bearer` headers across all run inputs, outputs, errors, and metadata.
3. **Single-line error formatting**: With trace sharing enabled, any public run error is truncated to its first line (maximum 500 characters, stack traces stripped), ensuring internal execution paths are not leaked.
4. **Runtime metadata omitted**: Host environment variables, internal server paths, and non-whitelisted runtime metadata are completely excluded from shared traces.
5. **Fail-soft timeout**: Flush and share operations run on a dedicated worker bounded by a 3-second timeout. If sharing times out or fails, `trace_url` falls back to `null` with a logged warning, without delaying or failing the scan report.

### 14.4 Fail-safe behavior for judges

When the daily scan ceiling (`daily_limit`) or hosted allowance (`paused`) is reached:
- **Live scans pause gracefully**: New live scan admission requests return `HTTP 429 Too Many Requests` (or `503 live_scans_paused` / `429 rate_limited`) with a `Retry-After` header indicating seconds until the next UTC midnight reset. Clear error messaging informs the judge that the daily quota has been reached.
- **Demo replays remain 100% available**: Pre-computed demo replays (`planesweeppose`, `pyrl`, `co2tools`) with authentic Token Factory Sandbox proof badges bypass the spend meter completely ($0 cost, 0 reservation, 0 wait time). Judges can test and inspect complete reachability analysis, interactive SVG call-path graphs, and verified exploit proofs at any time regardless of live budget limits.

