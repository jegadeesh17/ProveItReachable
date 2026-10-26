/**
 * Mock adapter: drives the whole UI from web/fixtures without a backend.
 * Select with ?api=mock&scenario=<name>; the name must be on the allowlist below, anything else is
 * `reachable` and never reaches a fixture path. `&jump=report` skips the feed animation.
 * Extra switches: `&feed=off` (AC-42), `&md=off` (AC-64), `&eval=on` (a labelled sample eval figure),
 * `&speed=<n>` (faster feed), `&proof=<mode>` (turns features.proof on and picks what a proof returns;
 * the mode must be on the PROOF_MODES allowlist, anything else leaves the feature off).
 */
import { fromErrorBody } from "./errors.js";

const FIXTURES = new URL("../../fixtures/", import.meta.url);

/** The scenario allowlist (ARCHITECTURE section 17). Report scenarios are the only names used in a fixture path. */
const REPORT_SCENARIOS = new Set(["reachable", "clean", "incomplete", "partial-failure"]);
/** Scenario name -> error code in fixtures/errors.json. These reject startScan (paused also locks the form). */
const SCAN_ERRORS = new Map([
  ["error-invalid-url", "invalid_url"],
  ["error-not-found", "repo_not_found"],
  ["error-too-large", "repo_too_large"],
  ["error-not-python", "not_python_project"],
  ["rate-limited", "rate_limited"],
  ["paused", "live_scans_paused"],
]);
/** These reject startReplay. */
const REPLAY_ERRORS = new Map([
  ["demo-not-found", "demo_not_found"],
  ["too-many-requests", "too_many_requests"],
]);
/**
 * `&proof=<mode>`: what `requestProof` answers. `on` replays the recorded outcomes in reports/proof.json
 * (one proven, one not proven); the others make every proof end with the reason the backend gives
 * (src/pir/jobs.py run_proof), `error` rejects the request like the API would. The package and version
 * in a reason come from the finding, as they do on the server.
 */
const PROOF_MODES = new Set(["on", "no-wheel", "build-failed", "timeout", "spend", "error"]);
const PROOF_REASONS = {
  "no-wheel": (f) => `uncertain: no wheel for ${f.package}==${f.version}; its source package could not be downloaded`,
  "build-failed": (f) => `uncertain: could not build ${f.package} ${f.version} from source`,
  timeout: () => "sandbox timeout",
  spend: () => "not run: the spend limit was reached",
};
/** How long the mock proof "runs" before its job is done. */
const PROOF_MS = 1800;
const DEFAULT_SCENARIO = "reachable";
/** With no explicit report scenario, each recorded demo replays one of the fixtures (the mock has no real recordings). */
const DEMO_SCENARIO = new Map([
  ["planesweeppose", "reachable"],
  ["pyrl", "partial-failure"],
  ["co2tools", "clean"],
]);
/** A labelled sample for `&eval=on`, so the eval line can be seen. Not a measurement. */
const SAMPLE_EVAL = { precision: 0.8, n: 25, run_date: "2026-10-20", model: "mock sample, not a measurement" };
const SAMPLE_URL = "https://github.com/placeholder-demo/flask-notes";
const BAD_SAMPLE_URL = "https://gitlab.com/placeholder-demo/flask-notes";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const clone = (value) => JSON.parse(JSON.stringify(value));

async function loadJson(path) {
  const res = await fetch(new URL(path, FIXTURES));
  if (!res.ok) throw new Error(`Fixture ${path} failed to load (${res.status}).`);
  return res.json();
}
async function loadText(path) {
  const res = await fetch(new URL(path, FIXTURES));
  if (!res.ok) throw new Error(`Fixture ${path} failed to load (${res.status}).`);
  return res.text();
}

/** A small stand-in for the server's parse_repo_url (section 12.1). Returns { owner, name } or null. */
function parseRepoUrl(input) {
  const text = String(input || "").trim();
  if (!text || text.length > 300) return null;
  let url;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || !["github.com", "www.github.com"].includes(url.hostname)) return null;
  if (url.username || url.password || url.port || url.search || url.hash) return null;
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts.length < 2) return null;
  if (parts.length > 2 && parts[2] !== "tree") return null;
  if (parts.length === 3 || parts.length > 4) return null;
  const owner = parts[0];
  const name = parts[1].replace(/\.git$/, "");
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(owner)) return null;
  if (!/^[A-Za-z0-9._-]{1,100}$/.test(name) || name === "." || name === "..") return null;
  return { owner, name };
}

export function createMockApi(params) {
  const requested = params.get("scenario") || "";
  const allowed = REPORT_SCENARIOS.has(requested) || SCAN_ERRORS.has(requested) || REPLAY_ERRORS.has(requested);
  if (requested && !allowed) {
    console.warn("Unknown mock scenario; using the default.");
  }
  const explicit = allowed ? requested : "";
  const scenario = explicit || DEFAULT_SCENARIO;
  const jump = params.get("jump") === "report";
  const speed = Math.max(0.25, Number(params.get("speed")) || 1);
  const feedOff = params.get("feed") === "off";
  const markdownOff = params.get("md") === "off";
  const evalOn = params.get("eval") === "on";
  const proofMode = PROOF_MODES.has(params.get("proof") || "") ? params.get("proof") : "";
  const latency = (ms) => (jump ? wait(0) : wait(ms));

  /** @type {Map<string, any>} */
  const jobs = new Map();
  /** Like the server, a job id is its own: it is not the report's scan_id. */
  const newJobId = () =>
    [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  let errorsCache = null;
  const errors = async () => (errorsCache ??= await loadJson("errors.json"));
  const rejectWith = async (code) => {
    const table = await errors();
    throw fromErrorBody(table[code]);
  };

  async function createJob(reportScenario, mode, repoOverride) {
    if (!REPORT_SCENARIOS.has(reportScenario)) throw new Error("Not a report scenario.");
    const [report, events, md] = await Promise.all([
      loadJson(`reports/${reportScenario}.json`),
      loadJson(`events/${reportScenario}.json`),
      loadText(`reports/${reportScenario}.md`),
    ]);
    let markdown = md;
    if (repoOverride) {
      const oldUrl = report.repo.url;
      const oldName = `${report.repo.owner}/${report.repo.name}`;
      const oldSha = report.repo.commit_sha;
      report.repo.url = `https://github.com/${repoOverride.owner}/${repoOverride.name}`;
      report.repo.owner = repoOverride.owner;
      report.repo.name = repoOverride.name;
      markdown = markdown.split(oldUrl).join(report.repo.url).split(oldName).join(`${repoOverride.owner}/${repoOverride.name}`);
      if (repoOverride.commit_sha) {
        report.repo.commit_sha = repoOverride.commit_sha;
        markdown = markdown.split(oldSha).join(repoOverride.commit_sha);
      }
    }
    const job = {
      scanId: newJobId(),
      mode,
      report,
      events: events.map((item) => item.event).map((event, i) => ({ ...event, offset: events[i].offset_ms })),
      markdown,
      startedAt: performance.now(),
      blobs: {},
    };
    jobs.set(job.scanId, job);
    return job;
  }

  function accepted(job) {
    const id = job.scanId;
    return {
      scan_id: id,
      mode: job.mode,
      status_url: `/api/scans/${id}`,
      events_url: `/api/scans/${id}/events`,
      report_url: `/api/scans/${id}/report`,
    };
  }

  const getJob = async (scanId) => {
    const job = jobs.get(scanId);
    if (!job) return rejectWith("scan_not_found");
    return job;
  };

  const stripOffset = ({ offset, ...event }) => event;

  return {
    mock: {
      scenario,
      explicit: Boolean(explicit),
      jump,
      /** A URL worth pre-filling for scenarios that are only reachable through the live form. */
      sampleUrl:
        scenario === "error-invalid-url"
          ? BAD_SAMPLE_URL
          : scenario.startsWith("error-") || scenario === "rate-limited"
            ? SAMPLE_URL
            : "",
      /** What `&jump=report` should do on load. */
      jumpPlan() {
        if (scenario === "paused") return null;
        if (REPLAY_ERRORS.has(scenario) || scenario === "reachable") return { type: "replay", slug: "planesweeppose" };
        if (scenario === "clean") return { type: "replay", slug: "co2tools" };
        if (scenario === "partial-failure") return { type: "replay", slug: "pyrl" };
        return { type: "scan", url: scenario === "error-invalid-url" ? BAD_SAMPLE_URL : SAMPLE_URL };
      },
    },

    async getConfig() {
      await latency(120);
      const config = await loadJson("config.json");
      if (feedOff) config.features.progress_feed = false;
      if (markdownOff) config.features.markdown_export = false;
      if (evalOn) config.eval = { ...SAMPLE_EVAL };
      if (proofMode) config.features.proof = true;
      if (scenario === "paused") {
        const table = await errors();
        config.live_scans_enabled = false;
        config.live_scans_paused_reason = table.live_scans_paused.error.message;
      }
      return config;
    },

    async listDemos() {
      await latency(650);
      return loadJson("demos.json");
    },

    async startScan(repoUrl) {
      await latency(500);
      if (SCAN_ERRORS.has(scenario)) return rejectWith(SCAN_ERRORS.get(scenario));
      const parsed = parseRepoUrl(repoUrl);
      if (!parsed) return rejectWith("invalid_url");
      const job = await createJob(REPORT_SCENARIOS.has(scenario) ? scenario : DEFAULT_SCENARIO, "live", parsed);
      return accepted(job);
    },

    async startReplay(slug) {
      await latency(250);
      if (REPLAY_ERRORS.has(scenario)) return rejectWith(REPLAY_ERRORS.get(scenario));
      const demos = await loadJson("demos.json");
      const demo = demos.find((d) => d.slug === slug);
      if (!demo) return rejectWith("demo_not_found");
      const reportScenario = REPORT_SCENARIOS.has(explicit) ? explicit : DEMO_SCENARIO.get(slug) || DEFAULT_SCENARIO;
      // The fixture stands in for the recording: show the demo's own repository and commit.
      const [owner, name] = new URL(demo.repo_url).pathname.split("/").filter(Boolean);
      const job = await createJob(reportScenario, "replay", { owner, name, commit_sha: demo.commit_sha });
      return accepted(job);
    },

    /**
     * Like POST /api/scans/{id}/findings/{fid}/proof: answers ScanAccepted for a proof job, which follows
     * like a scan (subscribe or getStatus) and, when done, has set `proof` on the scan's report. The
     * verdict is never touched (AC-67).
     */
    async requestProof(scanId, findingId, entry) {
      await latency(250);
      if (!proofMode) throw new Error("The mock proof is off; add &proof=on to the URL.");
      if (proofMode === "error") return rejectWith("rate_limited");
      const source = await getJob(scanId);
      const finding = source.report.findings.find((f) => f.finding_id === findingId);
      if (!finding) return rejectWith("finding_not_found");
      const steps = (finding.call_path || []).filter((s) => s.edge !== "vulnerable_call");
      if (finding.verdict !== "likely reachable" || steps.length === 0) return rejectWith("request_invalid");
      const chosen = entry === undefined || entry === null ? steps[0].symbol : entry;
      if (!steps.some((s) => s.symbol === chosen)) return rejectWith("request_invalid");

      let proof;
      if (proofMode === "on") {
        const recorded = (await loadJson("reports/proof.json")).findings.find((f) => f.finding_id === findingId);
        proof = recorded && recorded.proof ? clone(recorded.proof) : null;
      }
      const target = finding.call_path[finding.call_path.length - 1].symbol;
      if (!proof) {
        // Nothing recorded for this finding (or a mode that fixes the reason): the mock says it was not reached.
        const reason = PROOF_REASONS[proofMode] ? PROOF_REASONS[proofMode](finding) : `entry finished without reaching ${target}`;
        proof = { status: "not_proven", reason, ran_at: new Date().toISOString(), entry: chosen, target, recorded_call: null };
      }
      proof.entry = chosen;
      proof.ran_at = new Date().toISOString();
      source.report = { ...source.report, findings: source.report.findings.map((f) => (f.finding_id === findingId ? { ...f, proof } : f)) };
      source.blobs = {};

      const proofId = newJobId();
      const done = {
        seq: 1,
        scan_id: proofId,
        type: "done",
        step: null,
        state: null,
        message: `Proof: ${proof.status.replace("_", " ")}.`,
        at: proof.ran_at,
        outcome: source.report.outcome,
        tools: [],
        offset: PROOF_MS,
      };
      const job = { ...source, scanId: proofId, mode: "proof", events: [done], startedAt: performance.now(), blobs: {} };
      jobs.set(proofId, job);
      return accepted(job);
    },

    subscribe(scanId, onEvent) {
      const job = jobs.get(scanId);
      if (!job) {
        // The real adapter ends up here when the server refuses the stream and the status poll says scan_not_found.
        const timer = setTimeout(async () => {
          const table = await errors();
          onEvent({
            seq: 1,
            scan_id: scanId,
            type: "error",
            step: null,
            state: null,
            message: table.scan_not_found.error.message,
            at: new Date().toISOString(),
            outcome: null,
            tools: [],
          });
        }, 0);
        return () => clearTimeout(timer);
      }
      job.startedAt = performance.now();
      const timers = job.events.map((event, i) =>
        setTimeout(() => onEvent(stripOffset(event)), jump ? i : event.offset / speed),
      );
      return () => timers.forEach(clearTimeout);
    },

    async getStatus(scanId) {
      await latency(150);
      const job = await getJob(scanId);
      const elapsed = (performance.now() - job.startedAt) * speed;
      const finished = jump || elapsed >= job.events[job.events.length - 1].offset;
      const seen = job.events.filter((e) => jump || e.offset <= elapsed);
      const steps = job.report.steps.map((step) => {
        const latest = [...seen].reverse().find((e) => e.step === step.name);
        const state = finished ? step.state : latest ? latest.state : "pending";
        return {
          name: step.name,
          state,
          tools: ["done", "failed", "skipped"].includes(state) ? step.tools : [],
          started_at: null,
          finished_at: null,
          message: latest ? latest.message : null,
        };
      });
      return {
        scan_id: scanId,
        mode: job.mode,
        state: finished ? "completed" : "running",
        steps,
        outcome: finished ? job.report.outcome : null,
        created_at: job.report.generated_at,
        finished_at: finished ? job.report.generated_at : null,
      };
    },

    async getReport(scanId) {
      await latency(450);
      const job = await getJob(scanId);
      const elapsed = (performance.now() - job.startedAt) * speed;
      if (!jump && elapsed < job.events[job.events.length - 1].offset) return rejectWith("report_not_ready");
      return clone(job.report);
    },

    exportUrl(scanId, format) {
      const job = jobs.get(scanId);
      if (!job) return "#";
      if (!job.blobs[format]) {
        const blob =
          format === "md"
            ? new Blob([job.markdown], { type: "text/markdown" })
            : new Blob([JSON.stringify(job.report, null, 2) + "\n"], { type: "application/json" });
        job.blobs[format] = URL.createObjectURL(blob);
      }
      return job.blobs[format];
    },
  };
}
