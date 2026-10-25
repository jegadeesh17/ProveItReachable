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
 * @param {unknown} body
 */
export function fromErrorBody(body) {
  const e = body && typeof body === "object" && body.error ? body.error : {};
  return new ApiError({
    code: typeof e.code === "string" ? e.code : "internal_error",
    message: typeof e.message === "string" ? e.message : "Something went wrong.",
    details: e.details && typeof e.details === "object" ? e.details : {},
    retryAfterS: typeof e.retry_after_s === "number" ? e.retry_after_s : null,
  });
}
