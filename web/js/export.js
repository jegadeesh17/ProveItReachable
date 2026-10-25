/**
 * Export controls (J4). JSON is always offered; Markdown only when the config feature flag is on (AC-64).
 * The download address comes from the adapter: a server URL for http, a blob: URL for the mock.
 */
import { clear, el, icon } from "./render.js";

const FORMATS = {
  json: { label: "Download JSON", extension: "json" },
  md: { label: "Download Markdown", extension: "md" },
};

/** proveitreachable-<repo>-<sha7>.<ext>, the name the API also uses. */
export function exportFilename(report, format) {
  const repo = String(report.repo.name).replace(/[^A-Za-z0-9._-]/g, "_");
  return `proveitreachable-${repo}-${report.repo.commit_sha.slice(0, 7)}.${FORMATS[format].extension}`;
}

/**
 * @param {HTMLElement} slot
 * @param {{ exportUrl(scanId: string, format: "json"|"md"): string }} api
 * @param {string} scanId the job id from ScanAccepted (a replay's report.scan_id is the recording's, not this)
 * @param {object} report a ScanReport
 * @param {{ features: { markdown_export: boolean } }} config
 */
export function renderExports(slot, api, scanId, report, config) {
  clear(slot);
  const formats = ["json"];
  if (config.features && config.features.markdown_export) formats.push("md");
  for (const format of formats) {
    slot.append(
      el(
        "a",
        {
          class: "btn",
          href: api.exportUrl(scanId, format),
          download: exportFilename(report, format),
        },
        icon("icon-download", "icon icon--sm"),
        FORMATS[format].label,
      ),
    );
  }
}
