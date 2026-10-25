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
  formatDateTime,
  plural,
  renderDemoError,
  renderDemoSkeleton,
  renderDemos,
  renderMethod,
  renderNotExamined,
  renderReportSkeleton,
  renderResults,
  renderSignoff,
  renderSpecimen,
  renderSummary,
  setDemosDisabled,
  setStage,
  stageLabel,
  syncTrackFromReport,
} from "./render.js";

/** Exact text of the `invalid_url` API message (section 8); used only for an empty field, with no round trip. */
const INVALID_URL_MESSAGE = "Enter a public GitHub repository URL like https://github.com/owner/repo.";
const INPUT_ERRORS = new Set(["invalid_url", "repo_not_found", "repo_too_large", "not_python_project"]);
const POLL_MS = 2000;

const $ = (id) => document.getElementById(id);
const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const api = createApi();
const isMock = Boolean(api.mock);

const dom = {
  announcer: $("announcer"),
  banner: $("banner"),
  order: $("order"),
  orderBar: $("order-bar"),
  orderStatus: $("order-status"),
  orderToggle: $("order-toggle"),
  demoList: $("demo-list"),
  form: $("scan-form"),
  url: $("repo-url"),
  submit: $("scan-submit"),
  liveNotice: $("live-notice"),
  limits: $("limits"),
  specimen: $("specimen"),
  specimenFields: $("specimen-fields"),
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
  liveLock: null, // "paused" | "rate" | null: why the live form is switched off
  rateTimer: null,
  instant: Boolean(api.mock && api.mock.jump),
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
  dom.submit.textContent = busy && state.run && state.run.kind === "scan" && !state.run.accepted ? "Starting…" : "Run live assay";
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

/* -------------------------------------------------------------- order panel */

function openOrder(open) {
  dom.order.dataset.collapsed = String(!open);
  dom.orderToggle.setAttribute("aria-expanded", String(open));
  dom.orderToggle.textContent = open ? "Hide the order form" : "Order another assay";
}

function showOrderBar(status) {
  dom.orderBar.hidden = false;
  dom.orderStatus.textContent = status;
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
  setBusy(true);
  openOrder(false);
  document.body.dataset.phase = "run";
  showOrderBar(`Assay in progress for ${name}.`);

  // Fresh specimen and track; the previous report goes away.
  clear(dom.report);
  dom.report.hidden = true;
  renderSpecimen(dom.specimenFields, { name, source: state.run.source, commit: state.run.commit });
  buildTrack(dom.track);
  dom.specimen.hidden = false;
  const heading = $("specimen-h");
  if (!state.instant) heading.focus({ preventScroll: true });
  scrollToNode(dom.specimen);
  announce(`Assay started for ${name}. ${kind === "replay" ? "Replaying a recorded scan." : "Live scan."}`);

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
    setStage(dom.track, event.step, event.state, event.message, null, !state.instant);
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
  showSystemError(error);
  announce(error.message);
  showOrderBar(`The assay for ${state.run.name} did not finish.`);
  openOrder(false);
  setBusy(false);
}

async function finish(runId) {
  if (runId !== state.runId) return;
  const scanId = state.run.scanId;
  if (state.stop) state.stop();
  state.stop = null;
  clear(dom.report).append(renderReportSkeleton());
  dom.report.hidden = false;
  dom.report.setAttribute("aria-busy", "true");

  let report;
  try {
    report = await api.getReport(scanId);
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

  // Specimen now carries the facts the scan produced. Keep focus on the heading if it had it.
  const hadFocus = document.activeElement && document.activeElement.id === "specimen-h";
  const resolution = report.resolution;
  renderSpecimen(dom.specimenFields, {
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
    issued: formatDateTime(report.generated_at),
    model: report.model,
  });
  if (hadFocus) $("specimen-h").focus({ preventScroll: true });
  syncTrackFromReport(dom.track, report);
  dom.track.hidden = false;
  dom.busy.hidden = true;

  const summary = renderSummary(report, { isMock });
  renderExports(summary.exportSlot, api, report, state.config);

  clear(dom.report);
  dom.report.append(summary.node);
  if (report.findings.length > 0) dom.report.append(renderResults(report));
  const notExamined = renderNotExamined(report);
  if (notExamined) dom.report.append(notExamined);
  dom.report.append(renderMethod(report), renderSignoff(report));
  dom.report.removeAttribute("aria-busy");
  dom.report.hidden = false;
  dom.report.classList.add("stack");

  summary.animate(prefersReducedMotion() || state.instant);
  showOrderBar(`Report ready for ${name}.`);
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
  clear(dom.limits).append(
    el("p", {
      text: `Limits: public GitHub repositories only, up to ${config.limits.max_repo_mb} MB and ${config.limits.max_repo_files} files, with a Python dependency manifest.`,
    }),
    el("p", {
      text: "Live scans take minutes, are rate limited per visitor, and stop when the spend limit is reached. Demo replays are never limited.",
    }),
  );
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
      el("strong", { text: "Prototype on mock data." }),
      ` Scenario: ${api.mock.scenario}. Repositories, versions and figures are placeholders, not measurements.`,
    );
    $("mock-notice").hidden = false;
  }

  dom.orderToggle.addEventListener("click", () => openOrder(dom.order.dataset.collapsed === "true"));

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
