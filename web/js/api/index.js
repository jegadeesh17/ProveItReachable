/**
 * API adapter selection (ARCHITECTURE section 17).
 *
 * Both adapters implement exactly this shape:
 *
 *   getConfig():                    Promise<AppConfigResponse>
 *   listDemos():                    Promise<DemoSummary[]>
 *   startScan(repoUrl):             Promise<ScanAccepted>        rejects with ApiError
 *   startReplay(slug):              Promise<ScanAccepted>        rejects with ApiError
 *   subscribe(scanId, onEvent):     () => void                   returns an unsubscribe function
 *   getStatus(scanId):              Promise<ScanStatusResponse>
 *   getReport(scanId):              Promise<ScanReport>
 *   exportUrl(scanId, format):      string                       format is "json" or "md"
 *   requestProof(scanId, findingId, entry?): Promise<ScanAccepted>
 *
 * The mock adapter also exposes `mock` ({ scenario, jumpPlan(), sampleUrl }); the http adapter does not.
 *
 * @typedef {{ code: string, message: string, details?: object, retryAfterS?: number|null }} ApiError
 */
import { createHttpApi } from "./http.js";
import { createMockApi } from "./mock.js";

/**
 * @param {string} [search] the page query string; `?api=mock` selects the mock adapter
 */
export function createApi(search = window.location.search) {
  const params = new URLSearchParams(search);
  return params.get("api") === "mock" ? createMockApi(params) : createHttpApi();
}

export { ApiError } from "./errors.js";
