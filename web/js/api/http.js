/**
 * HTTP adapter for the real backend (ARCHITECTURE section 8). Same interface as the mock.
 * Written against the documented API; not exercised until the backend exists.
 */
import { ApiError, fromErrorBody } from "./errors.js";

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
  } catch (cause) {
    throw new ApiError({
      code: "github_unavailable",
      message: "Could not reach the server. Check your connection and try again.",
    });
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const err = fromErrorBody(body);
    const retryHeader = Number(res.headers.get("Retry-After"));
    if (err.retryAfterS === null && Number.isFinite(retryHeader) && retryHeader > 0) {
      err.retryAfterS = retryHeader;
    }
    throw err;
  }
  return body;
}

export function createHttpApi() {
  return {
    getConfig: () => request("/api/config"),
    listDemos: () => request("/api/demos"),
    startScan: (repoUrl) =>
      request("/api/scans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_url: repoUrl }),
      }),
    startReplay: (slug) =>
      request(`/api/demos/${encodeURIComponent(slug)}/replay`, { method: "POST" }),
    getStatus: (scanId) => request(`/api/scans/${encodeURIComponent(scanId)}`),
    getReport: (scanId) => request(`/api/scans/${encodeURIComponent(scanId)}/report`),

    /**
     * Server-sent events. The server closes the stream after `done` or `error`.
     * @param {string} scanId
     * @param {(e: object) => void} onEvent
     */
    subscribe(scanId, onEvent) {
      const source = new EventSource(`/api/scans/${encodeURIComponent(scanId)}/events`);
      let finished = false;
      const handle = (message) => {
        let event;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        if (event.type === "done" || event.type === "error") {
          finished = true;
          source.close();
        }
        onEvent(event);
      };
      source.addEventListener("step", handle);
      source.addEventListener("done", handle);
      source.addEventListener("error", (message) => {
        if (message.data) {
          handle(message);
        } else if (source.readyState === EventSource.CLOSED && !finished) {
          finished = true;
          onEvent({
            seq: -1,
            scan_id: scanId,
            type: "error",
            step: null,
            state: null,
            message: "The progress feed closed before the scan finished.",
            at: new Date().toISOString(),
            outcome: null,
          });
        }
        // Otherwise the browser reconnects on its own, sending Last-Event-ID.
      });
      return () => source.close();
    },

    /** @param {string} scanId @param {"json"|"md"} format */
    exportUrl(scanId, format) {
      const id = encodeURIComponent(scanId);
      return format === "md"
        ? `/api/scans/${id}/report.md?download=1`
        : `/api/scans/${id}/report?download=1`;
    },
  };
}
