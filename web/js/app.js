/**
 * State and events for the single screen: idle -> running -> report, with errors beside their cause.
 * All data comes through the adapter from api/index.js, so the same code runs on the mock and the backend.
 */
import { createApi } from "./api/index.js";
import { renderExports } from "./export.js";
import {
  STAGES,
  banner,
  buildTrack,
  clear,
  el,
  formatDate,
  plural,
  renderDemoError,
  renderDemoSkeleton,
  renderDemos,
  renderMethod,
  renderNotExamined,
  renderReportFoot,
  renderReportSkeleton,
  renderResults,
  renderScanHead,
  renderSummary,
  secondsBetween,
  setDemosDisabled,
  setStage,
  stageLabel,
  syncTrackFromReport,
  trackProgress,
} from "./render.js";

/** Exact text of the `invalid_url` API message (section 8); used only for an empty field, with no round trip. */
const INVALID_URL_MESSAGE = "Enter a public GitHub repository URL like https://github.com/owner/repo.";
const INPUT_ERRORS = new Set(["invalid_url", "repo_not_found", "repo_too_large", "not_python_project"]);
const POLL_MS = 2000;
const REPORT_RETRIES = 5;
const REPORT_RETRY_MS = 1000;

const $ = (id) => document.getElementById(id);
const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const api = createApi();
const isMock = Boolean(api.mock);

const dom = {
  announcer: $("announcer"),
  banner: $("banner"),
  order: $("order"),
  orderToggle: $("order-toggle"),
  demoList: $("demo-list"),
  form: $("scan-form"),
  url: $("repo-url"),
  submit: $("scan-submit"),
  liveNotice: $("live-notice"),
  limits: $("limits"),
  run: $("run"),
  scanHead: $("scan-head"),
  exportSlot: $("export-slot"),
  runLabel: $("run-label"),
  runClock: $("run-clock"),
  runMeter: $("run-meter"),
  track: $("track"),
  busy: $("busy-box"),
  report: $("report"),
};

const state = {
  config: null,
  demos: [],
  busy: false,
  runId: 0,
  stop: null, // cancels the subscription or the poll of the current run
  clock: null, // interval id of the elapsed timer
  liveLock: null, // "paused" | "rate" | null: why the live form is switched off
  rateTimer: null,
  instant: Boolean(api.mock && api.mock.jump),
  stageStart: {}, // step -> ISO time of its running event, for per-stage durations
  run: null,
};

/* ----------------------------------------------------------------- helpers */

function announce(message) {
  dom.announcer.textContent = "";
  // A second tick makes screen readers repeat identical text.
  window.setTimeout(() => {
    dom.announcer.textContent = message;
  }, 30);
}

function scrollToNode(node, block = "start") {
  node.scrollIntoView({ block, behavior: prefersReducedMotion() || state.instant ? "auto" : "smooth" });
}

function setPhase(phase) {
  document.body.dataset.phase = phase;
}

function repoNameFromUrl(text) {
  try {
    const parts = new URL(text.trim()).pathname.split("/").filter(Boolean);
    if (parts.length >= 2) return `${parts[0]}/${parts[1].replace(/\.git$/, "")}`;
  } catch {
    /* fall through */
  }
  return text.trim();
}

function looksLikeRepoUrl(text) {
  try {
    const url = new URL(text.trim());
    return (
      url.protocol === "https:" &&
      ["github.com", "www.github.com"].includes(url.hostname) &&
      url.pathname.split("/").filter(Boolean).length >= 2
    );
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ notices */

function showSystemError(error, options) {
  clear(dom.banner).append(banner(error, options));
  dom.banner.hidden = false;
  scrollToNode(dom.banner, "nearest");
}

function clearSystemError() {
  clear(dom.banner);
  dom.banner.hidden = true;
}

function showLiveNotice(error, options) {
  clear(dom.liveNotice).append(banner(error, options));
  dom.liveNotice.hidden = false;
}

function clearLiveNotice() {
  clear(dom.liveNotice);
  dom.liveNotice.hidden = true;
  dom.url.removeAttribute("aria-invalid");
  dom.url.setAttribute("aria-describedby", "repo-url-hint");
}

function markUrlInvalid() {
  dom.url.setAttribute("aria-invalid", "true");
  dom.url.setAttribute("aria-describedby", "repo-url-hint live-notice");
}

/* --------------------------------------------------------------- live form */

function applyLiveLock() {
  const locked = state.liveLock !== null;
  dom.url.disabled = state.liveLock === "paused";
  dom.submit.disabled = state.busy || locked;
}

function lockForRate(seconds) {
  state.liveLock = "rate";
  window.clearTimeout(state.rateTimer);
  state.rateTimer = window.setTimeout(() => {
    state.liveLock = null;
    clearLiveNotice();
    applyLiveLock();
  }, Math.max(1, seconds) * 1000);
  applyLiveLock();
}

function setBusy(busy) {
  state.busy = busy;
  setDemosDisabled(dom.demoList, busy);
  applyLiveLock();
  dom.submit.textContent = busy && state.run && state.run.kind === "scan" && !state.run.accepted ? "Starting…" : "Scan repo";
}

function handleStartError(error, kind) {
  announce(error.message);
  if (kind === "scan" && (INPUT_ERRORS.has(error.code) || error.code === "rate_limited" || error.code === "live_scans_paused")) {
    showLiveNotice(error);
    if (INPUT_ERRORS.has(error.code)) markUrlInvalid();
    if (error.code === "rate_limited") lockForRate(error.retryAfterS || 60);
    if (error.code === "live_scans_paused") {
      state.liveLock = "paused";
      applyLiveLock();
    }
    openOrder(true);
    scrollToNode(dom.liveNotice, "nearest");
  } else {
    showSystemError(error);
  }
}

/* -------------------------------------------------------------- scan drawer */

function openOrder(open) {
  dom.order.dataset.collapsed = String(!open);
  dom.orderToggle.setAttribute("aria-expanded", String(open));
  dom.orderToggle.textContent = open ? "Hide scan form" : "New scan";
}

/* ------------------------------------------------------------ run progress */

function formatClock(totalSeconds) {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function startClock() {
  stopClock();
  const t0 = performance.now();
  const tick = () => {
    dom.runClock.textContent = formatClock(Math.floor((performance.now() - t0) / 1000));
  };
  tick();
  state.clock = window.setInterval(tick, 1000);
}

function stopClock() {
  if (state.clock !== null) window.clearInterval(state.clock);
  state.clock = null;
}

function refreshRunStatus() {
  const { total, settled, current } = trackProgress(dom.track);
  dom.runLabel.textContent = current
    ? `Step ${current.index + 1} of ${total}: ${current.label}`
    : settled === total
      ? "All steps finished"
      : settled > 0
        ? `${settled} of ${total} steps finished`
        : "Starting…";
  dom.runMeter.style.setProperty("--progress", String(settled / total));
  dom.runMeter.setAttribute("aria-valuenow", String(settled));
}

/* ------------------------------------------------------------------- runs */

async function begin(kind, payload) {
  if (state.busy) return;
  clearSystemError();
  clearLiveNotice();
  state.run = { kind, accepted: false };
  setBusy(true);

  let accepted;
  try {
    accepted = kind === "replay" ? await api.startReplay(payload.slug) : await api.startScan(payload.url);
  } catch (error) {
    state.run = null;
    setBusy(false);
    handleStartError(error, kind);
    return;
  }

  if (state.stop) state.stop();
  state.runId += 1;
  const runId = state.runId;
  const name = kind === "replay" ? payload.title : repoNameFromUrl(payload.url);
  state.run = {
    kind,
    accepted: true,
    runId,
    scanId: accepted.scan_id,
    name,
    source: kind === "replay" ? `Recorded demo, recorded on ${formatDate(payload.recorded_at)}` : "Live scan",
    commit: kind === "replay" ? payload.commit_sha.slice(0, 7) : null,
    delivered: 0,
  };
  state.stageStart = {};
  setBusy(true);
  openOrder(false);
  setPhase("run");

  // Fresh header and feed; the previous report goes away.
  clear(dom.report);
  dom.report.hidden = true;
  clear(dom.exportSlot);
  renderScanHead(dom.scanHead, { name, source: state.run.source, commit: state.run.commit });
  buildTrack(dom.track);
  refreshRunStatus();
  startClock();
  dom.run.hidden = false;
  if (!state.instant) $("scan-h").focus({ preventScroll: true });
  scrollToNode(dom.run);
  announce(`Scan started for ${name}. ${kind === "replay" ? "Replaying a recorded scan." : "Live scan."}`);

  if (state.config.features.progress_feed) {
    dom.track.hidden = false;
    dom.busy.hidden = true;
    state.stop = api.subscribe(accepted.scan_id, (event) => onEvent(runId, event));
  } else {
    dom.track.hidden = true;
    dom.busy.hidden = false;
    state.stop = pollStatus(runId, accepted.scan_id);
  }
}

function onEvent(runId, event) {
  if (runId !== state.runId) return;
  if (event.type === "step") {
    const index = STAGES.findIndex(([key]) => key === event.step);
    if (event.state === "running") state.stageStart[event.step] = event.at;
    const seconds = event.state === "running" ? null : secondsBetween(state.stageStart[event.step], event.at);
    setStage(dom.track, event.step, event.state, event.message, seconds, !state.instant, event.tools);
    refreshRunStatus();
    state.run.delivered += 1;
    announce(`Step ${index + 1} of ${STAGES.length}, ${stageLabel(event.step)}: ${event.state}.`);
  } else if (event.type === "done") {
    finish(runId);
  } else if (event.type === "error") {
    failRun(runId, { code: "internal_error", message: event.message || "Something went wrong." });
  }
}

function pollStatus(runId, scanId) {
  let stopped = false;
  const tick = async () => {
    if (stopped || runId !== state.runId) return;
    try {
      const status = await api.getStatus(scanId);
      if (stopped || runId !== state.runId) return;
      if (status.state === "completed") return finish(runId);
      if (status.state === "failed") return failRun(runId, { code: "internal_error", message: "The scan failed." });
    } catch (error) {
      return failRun(runId, error);
    }
    window.setTimeout(tick, POLL_MS);
  };
  window.setTimeout(tick, state.instant ? 0 : POLL_MS);
  return () => {
    stopped = true;
  };
}

function failRun(runId, error) {
  if (runId !== state.runId) return;
  if (state.stop) state.stop();
  state.stop = null;
  stopClock();
  showSystemError(error);
  announce(error.message);
  openOrder(false);
  setBusy(false);
}

/** The report may land a moment after the `done` event; retry `report_not_ready` a few times. */
async function fetchReport(scanId) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await api.getReport(scanId);
    } catch (error) {
      if (error.code !== "report_not_ready" || attempt >= REPORT_RETRIES) throw error;
      await new Promise((resolve) => window.setTimeout(resolve, state.instant ? 0 : REPORT_RETRY_MS));
    }
  }
}

async function finish(runId) {
  if (runId !== state.runId) return;
  const scanId = state.run.scanId;
  if (state.stop) state.stop();
  state.stop = null;
  stopClock();
  clear(dom.report).append(renderReportSkeleton());
  dom.report.hidden = false;
  dom.report.setAttribute("aria-busy", "true");

  let report;
  try {
    report = await fetchReport(scanId);
  } catch (error) {
    clear(dom.report);
    dom.report.hidden = true;
    dom.report.removeAttribute("aria-busy");
    failRun(runId, error);
    return;
  }
  if (runId !== state.runId) return;
  showReport(report);
}

/* ----------------------------------------------------------------- report */

function showReport(report) {
  const run = state.run;
  const name = `${report.repo.owner}/${report.repo.name}`;

  // The header now carries the facts the scan produced. Keep focus on the heading if it had it.
  const hadFocus = document.activeElement && document.activeElement.id === "scan-h";
  const resolution = report.resolution;
  renderScanHead(dom.scanHead, {
    name,
    source: run.source,
    commit: report.repo.commit_sha.slice(0, 7),
    branch: report.repo.default_branch,
    resolvedBy:
      resolution.method === "lockfile"
        ? `Locked in ${resolution.lockfile}`
        : resolution.method === "compiled"
          ? "Compiled, not locked"
          : "Could not be resolved",
    model: report.model,
  });
  if (hadFocus) $("scan-h").focus({ preventScroll: true });
  syncTrackFromReport(dom.track, report);
  dom.busy.hidden = true;
  // The URLs use the job id from ScanAccepted; for a replay it differs from the recording's report.scan_id.
  renderExports(dom.exportSlot, api, run.scanId, report, state.config);

  const summary = renderSummary(report, { isMock });
  clear(dom.report);
  dom.report.append(summary.node);
  if (report.findings.length > 0) dom.report.append(renderResults(report));
  const notExamined = renderNotExamined(report);
  if (notExamined) dom.report.append(notExamined);
  dom.report.append(renderMethod(report), renderReportFoot(report));
  dom.report.removeAttribute("aria-busy");
  dom.report.hidden = false;
  setPhase("report");

  summary.animate(prefersReducedMotion() || state.instant);
  setBusy(false);

  const by = report.summary.by_verdict;
  announce(
    report.outcome === "findings"
      ? `Report ready for ${name}. ${report.summary.findings_total} ${plural(report.summary.findings_total, "finding", "findings")}: ${by["likely reachable"]} likely reachable, ${by.uncertain} uncertain, ${by["no reachable path detected"]} no reachable path detected.`
      : report.outcome === "clean"
        ? `Report ready for ${name}. Clean: no known-vulnerable dependencies among ${report.summary.dependencies_checked} checked.`
        : `Report ready for ${name}. Incomplete: ${report.summary.dependencies_unresolved} ${plural(report.summary.dependencies_unresolved, "dependency", "dependencies")} could not be resolved.`,
  );
}

/* ------------------------------------------------------------------ startup */

function renderLimits(config) {
  const { max_repo_mb: mb, max_repo_files: files, live_scans_per_client_hour: perHour, live_scans_per_client_day: perDay } = config.limits;
  clear(dom.limits).append(
    el("p", {
      text: `Limits: public GitHub repositories only, up to ${mb} MB and ${files} files, with a Python dependency manifest.`,
    }),
    el("p", {
      text: `Live scans take minutes. Each visitor can start ${perHour} per hour and ${perDay} per day, and live scans stop when the spend limit is reached. Demo replays do not count toward these limits.`,
    }),
  );
  // An accuracy figure appears only when the API provides a scored eval run, with what it was measured on.
  if (config.eval) {
    const e = config.eval;
    dom.limits.append(
      el("p", {
        class: "eval-figure",
        text: `Precision of \u201clikely reachable\u201d verdicts: ${Math.round(e.precision * 100)}%, measured on ${e.n} labelled cases (run ${formatDate(e.run_date)}, model ${e.model}).`,
      }),
    );
  }
}

async function loadStartup() {
  renderDemoSkeleton(dom.demoList);
  let config;
  let demos;
  try {
    [config, demos] = await Promise.all([api.getConfig(), api.listDemos()]);
  } catch (error) {
    renderDemoError(dom.demoList, error.message, loadStartup);
    dom.url.disabled = true;
    dom.submit.disabled = true;
    return;
  }
  state.config = config;
  state.demos = demos;
  dom.url.disabled = false;
  renderLimits(config);
  renderDemos(dom.demoList, demos, (demo) => begin("replay", demo));

  if (!config.live_scans_enabled) {
    state.liveLock = "paused";
    showLiveNotice(
      { code: "live_scans_paused", message: config.live_scans_paused_reason || "Live scans are paused. Demo replays still work." },
      { kind: "info" },
    );
  }
  applyLiveLock();

  if (isMock && api.mock.sampleUrl) dom.url.value = api.mock.sampleUrl;

  const plan = isMock && api.mock.jump ? api.mock.jumpPlan() : null;
  if (plan && plan.type === "replay") {
    const demo = demos.find((d) => d.slug === plan.slug);
    if (demo) begin("replay", demo);
  } else if (plan && plan.type === "scan") {
    dom.url.value = plan.url;
    begin("scan", { url: plan.url });
  }
}

function init() {
  if (isMock) {
    const note = $("mock-notice-text");
    note.append(
      el("strong", { text: "Mock data." }),
      ` Scenario: ${api.mock.scenario}. Reports are fixtures, not scan results.`,
    );
    $("mock-notice").hidden = false;
  }

  dom.orderToggle.addEventListener("click", () => {
    const open = dom.order.dataset.collapsed === "true";
    openOrder(open);
    if (open) scrollToNode(dom.order);
  });

  dom.form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (state.busy || state.liveLock) return;
    const value = dom.url.value.trim();
    clearLiveNotice();
    if (!value) {
      showLiveNotice({ code: "invalid_url", message: INVALID_URL_MESSAGE });
      markUrlInvalid();
      dom.url.focus();
      return;
    }
    begin("scan", { url: value });
  });
  dom.url.addEventListener("blur", () => {
    const value = dom.url.value.trim();
    if (value && !looksLikeRepoUrl(value) && !state.busy && state.liveLock === null) {
      showLiveNotice({ code: "invalid_url", message: INVALID_URL_MESSAGE });
      markUrlInvalid();
    }
  });
  dom.url.addEventListener("input", () => {
    if (dom.url.getAttribute("aria-invalid") === "true" && state.liveLock === null) clearLiveNotice();
  });

  loadStartup();
}

init();
