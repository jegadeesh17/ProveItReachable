/**
 * Mock adapter: drives the whole UI from web/fixtures without a backend.
 * Select with ?api=mock&scenario=<name>. `&jump=report` skips the feed animation.
 * Extra switches for the cut-fallbacks: `&feed=off` (AC-42), `&md=off` (AC-64), `&speed=<n>` (faster feed).
 */
import { ApiError, fromErrorBody } from "./errors.js";

const FIXTURES = new URL("../../fixtures/", import.meta.url);

const REPORT_SCENARIOS = ["reachable", "clean", "incomplete", "partial-failure"];
/** Scenario name -> error code in fixtures/errors.json */
const ERROR_SCENARIOS = {
  "error-invalid-url": "invalid_url",
  "error-not-found": "repo_not_found",
  "error-too-large": "repo_too_large",
  "error-not-python": "not_python_project",
  "rate-limited": "rate_limited",
  paused: "live_scans_paused",
};
/** With no explicit scenario, each demo replays its own recorded fixture. */
const DEMO_SCENARIO = {
  "placeholder-flask-notes": "reachable",
  "placeholder-data-pipeline": "partial-failure",
  "placeholder-tiny-cli": "clean",
};
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
  const explicit = REPORT_SCENARIOS.includes(requested) || requested in ERROR_SCENARIOS ? requested : "";
  if (requested && !explicit) {
    console.warn(`Unknown mock scenario "${requested}"; using "reachable".`);
  }
  const scenario = explicit || "reachable";
  const jump = params.get("jump") === "report";
  const speed = Math.max(0.25, Number(params.get("speed")) || 1);
  const feedOff = params.get("feed") === "off";
  const markdownOff = params.get("md") === "off";
  const latency = (ms) => (jump ? wait(0) : wait(ms));

  /** @type {Map<string, any>} */
  const jobs = new Map();
  let errorsCache = null;
  const errors = async () => (errorsCache ??= await loadJson("errors.json"));
  const rejectWith = async (code) => {
    const table = await errors();
    throw fromErrorBody(table[code]);
  };

  async function createJob(reportScenario, mode, repoOverride) {
    const [report, events, md] = await Promise.all([
      loadJson(`reports/${reportScenario}.json`),
      loadJson(`events/${reportScenario}.json`),
      loadText(`reports/${reportScenario}.md`),
    ]);
    let markdown = md;
    if (repoOverride) {
      const oldUrl = report.repo.url;
      const oldName = `${report.repo.owner}/${report.repo.name}`;
      report.repo.url = `https://github.com/${repoOverride.owner}/${repoOverride.name}`;
      report.repo.owner = repoOverride.owner;
      report.repo.name = repoOverride.name;
      markdown = markdown.split(oldUrl).join(report.repo.url).split(oldName).join(`${repoOverride.owner}/${repoOverride.name}`);
    }
    const job = {
      scanId: report.scan_id,
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

  const getJob = (scanId) => {
    const job = jobs.get(scanId);
    if (!job) {
      throw new ApiError({ code: "scan_not_found", message: "That scan does not exist." });
    }
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
        if (scenario === "reachable") return { type: "replay", slug: "placeholder-flask-notes" };
        if (scenario === "clean") return { type: "replay", slug: "placeholder-tiny-cli" };
        if (scenario === "partial-failure") return { type: "replay", slug: "placeholder-data-pipeline" };
        return { type: "scan", url: scenario === "error-invalid-url" ? BAD_SAMPLE_URL : SAMPLE_URL };
      },
    },

    async getConfig() {
      await latency(120);
      const config = await loadJson("config.json");
      if (feedOff) config.features.progress_feed = false;
      if (markdownOff) config.features.markdown_export = false;
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
      if (scenario in ERROR_SCENARIOS) return rejectWith(ERROR_SCENARIOS[scenario]);
      const parsed = parseRepoUrl(repoUrl);
      if (!parsed) return rejectWith("invalid_url");
      const job = await createJob(scenario, "live", parsed);
      return accepted(job);
    },

    async startReplay(slug) {
      await latency(250);
      const demos = await loadJson("demos.json");
      if (!demos.some((demo) => demo.slug === slug)) {
        throw new ApiError({ code: "scan_not_found", message: "That demo does not exist." });
      }
      const reportScenario = REPORT_SCENARIOS.includes(explicit) ? explicit : DEMO_SCENARIO[slug];
      const job = await createJob(reportScenario, "replay", null);
      return accepted(job);
    },

    subscribe(scanId, onEvent) {
      const job = getJob(scanId);
      job.startedAt = performance.now();
      const timers = job.events.map((event, i) =>
        setTimeout(() => onEvent(stripOffset(event)), jump ? i : event.offset / speed),
      );
      return () => timers.forEach(clearTimeout);
    },

    async getStatus(scanId) {
      await latency(150);
      const job = getJob(scanId);
      const elapsed = (performance.now() - job.startedAt) * speed;
      const finished = jump || elapsed >= job.events[job.events.length - 1].offset;
      const seen = job.events.filter((e) => jump || e.offset <= elapsed);
      const steps = job.report.steps.map((step) => {
        const latest = [...seen].reverse().find((e) => e.step === step.name);
        return {
          name: step.name,
          state: finished ? step.state : latest ? latest.state : "pending",
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
      const job = getJob(scanId);
      const elapsed = (performance.now() - job.startedAt) * speed;
      if (!jump && elapsed < job.events[job.events.length - 1].offset) {
        throw new ApiError({ code: "report_not_ready", message: "The report is not ready yet." });
      }
      return clone(job.report);
    },

    exportUrl(scanId, format) {
      const job = getJob(scanId);
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
