/**
 * HTTP adapter for the real backend (ARCHITECTURE section 8). Same interface as the mock; every
 * failure rejects with an ApiError built from the API's own ErrorResponse, so messages are shown verbatim.
 *
 * `subscribe` reads the server-sent event stream. When the stream is refused (a plain 429 or 404 JSON
 * answer, which EventSource only reports as an error) or keeps failing, it falls back to polling
 * `GET /api/scans/{id}` and turns each status change into the same ProgressEvent objects.
 */
import { fromResponse, networkError } from "./errors.js";

const POLL_MS = 2000;
/** Failed reconnects in a row, with no message in between, before the stream is given up for polling. */
const MAX_STREAM_FAILURES = 4;
const TERMINAL_STATES = new Set(["done", "failed", "skipped"]);

/**
 * @param {string} path
 * @param {RequestInit} [init]
 */
async function request(path, init = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...(init.headers || {}) },
    });
  } catch {
    throw networkError();
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) throw fromResponse(body, res.headers.get("Retry-After"));
  return body;
}

const scanPath = (scanId) => `/api/scans/${encodeURIComponent(scanId)}`;

/**
 * Turn polled statuses into ProgressEvents: one `step` event per step state change, then `done` or `error`.
 * @returns {() => void} stop
 */
function pollAsEvents(scanId, getStatus, emit) {
  const seen = new Map();
  let stopped = false;
  let timer = null;
  let seq = 0;
  const event = (fields) => ({
    seq: (seq += 1),
    scan_id: scanId,
    type: "step",
    step: null,
    state: null,
    message: null,
    at: new Date().toISOString(),
    outcome: null,
    tools: [],
    ...fields,
  });

  const tick = async () => {
    if (stopped) return;
    let status;
    try {
      status = await getStatus(scanId);
    } catch (error) {
      if (!stopped) emit(event({ type: "error", message: error.message }));
      return;
    }
    if (stopped) return;
    for (const step of status.steps) {
      if (step.state === "pending" || seen.get(step.name) === step.state) continue;
      seen.set(step.name, step.state);
      emit(
        event({
          step: step.name,
          state: step.state,
          message: step.message,
          at: (TERMINAL_STATES.has(step.state) ? step.finished_at : step.started_at) || new Date().toISOString(),
          tools: TERMINAL_STATES.has(step.state) ? step.tools : [],
        }),
      );
    }
    if (status.state === "completed") {
      emit(event({ type: "done", message: "Report ready.", outcome: status.outcome }));
      return;
    }
    if (status.state === "failed") {
      emit(event({ type: "error", message: "The scan failed before a report was produced." }));
      return;
    }
    timer = window.setTimeout(tick, POLL_MS);
  };
  tick();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
  };
}

export function createHttpApi() {
  const getStatus = (scanId) => request(scanPath(scanId));

  return {
    getConfig: () => request("/api/config"),
    listDemos: () => request("/api/demos"),
    startScan: (repoUrl) =>
      request("/api/scans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_url: repoUrl }),
      }),
    startReplay: (slug) => request(`/api/demos/${encodeURIComponent(slug)}/replay`, { method: "POST" }),
    getStatus,
    getReport: (scanId) => request(`${scanPath(scanId)}/report`),

    /**
     * Server-sent events, closed on `done` or `error` (else EventSource would reconnect forever).
     * @param {string} scanId
     * @param {(e: object) => void} onEvent
     * @returns {() => void} unsubscribe
     */
    subscribe(scanId, onEvent) {
      let source = null;
      let stopPolling = null;
      let finished = false;
      let lastSeq = 0;
      let failures = 0;

      const emit = (event) => {
        if (finished) return;
        if (event.type === "done" || event.type === "error") finished = true;
        onEvent(event);
      };

      const fallBackToPolling = () => {
        if (source) source.close();
        source = null;
        if (!finished && !stopPolling) stopPolling = pollAsEvents(scanId, getStatus, emit);
      };

      const handle = (message) => {
        let event;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        failures = 0;
        // After a reconnect the server resumes from Last-Event-ID; this guards against a repeat anyway.
        if (event.type === "step") {
          if (event.seq <= lastSeq) return;
          lastSeq = event.seq;
        }
        if (event.type === "done" || event.type === "error") source.close();
        emit(event);
      };

      source = new EventSource(`${scanPath(scanId)}/events`);
      source.addEventListener("step", handle);
      source.addEventListener("done", handle);
      source.addEventListener("error", (message) => {
        if (message.data) return handle(message); // a server `event: error` frame
        if (finished) return;
        failures += 1;
        // CLOSED: the browser gave up (a refused stream answers 429/404 JSON, not an event stream).
        // CONNECTING: it is retrying on its own with Last-Event-ID; allow a few attempts.
        if (source.readyState === EventSource.CLOSED || failures >= MAX_STREAM_FAILURES) fallBackToPolling();
      });

      return () => {
        finished = true;
        if (source) source.close();
        if (stopPolling) stopPolling();
      };
    },

    /** @param {string} scanId @param {"json"|"md"} format */
    exportUrl(scanId, format) {
      return `${scanPath(scanId)}/${format === "md" ? "report.md" : "report"}?download=1`;
    },
  };
}
