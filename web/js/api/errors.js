/**
 * The one error type both adapters reject with.
 * `message` is shown to the user verbatim (AC-43); `code` drives the UI state.
 */
export class ApiError extends Error {
  /**
   * @param {{ code: string, message: string, details?: Record<string, string|number>, retryAfterS?: number|null }} init
   */
  constructor({ code, message, details = {}, retryAfterS = null }) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
    this.retryAfterS = retryAfterS;
  }
}

/**
 * Build an ApiError from an `ErrorResponse` body ({ error: { code, message, details, retry_after_s } }).
 * A body that is not an ErrorResponse (a proxy page, a dropped connection) becomes `internal_error`
 * with a generic client-side sentence; the API's own messages are never rewritten.
 * @param {unknown} body
 */
export function fromErrorBody(body) {
  const e = body && typeof body === "object" && body.error && typeof body.error === "object" ? body.error : {};
  return new ApiError({
    code: typeof e.code === "string" ? e.code : "internal_error",
    message: typeof e.message === "string" ? e.message : "Something went wrong.",
    details: e.details && typeof e.details === "object" ? e.details : {},
    retryAfterS: typeof e.retry_after_s === "number" ? e.retry_after_s : null,
  });
}

/**
 * Like fromErrorBody, but also reads the `Retry-After` header when the body carries no wait time.
 * @param {unknown} body
 * @param {string|null} retryAfterHeader
 */
export function fromResponse(body, retryAfterHeader) {
  const error = fromErrorBody(body);
  const seconds = Number(retryAfterHeader);
  if (error.retryAfterS === null && retryAfterHeader !== null && Number.isFinite(seconds) && seconds > 0) {
    error.retryAfterS = seconds;
  }
  return error;
}

/** The request never got an answer. `network_error` is client-only; it is not in the API's error table. */
export function networkError() {
  return new ApiError({
    code: "network_error",
    message: "Could not reach the server. Check your connection and try again.",
  });
}
