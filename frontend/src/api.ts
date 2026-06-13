import type { ApiErrorBody, SearchResponse, StatsResponse } from "./types";

// Vite bakes import.meta.env.* in at build time. Falls back to localhost:8000 in dev
// when VITE_API_BASE_URL isn't set (see .env.example).
export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Calls GET /search?q=<query>&limit=<limit>.
 * Throws ApiError for non-2xx responses (including the 400 "bad query" case,
 * whose message is taken from the response body's `detail` field when present).
 * Accepts an optional AbortSignal so callers can cancel in-flight requests.
 */
export async function searchApi(
  query: string,
  limit = 10,
  signal?: AbortSignal
): Promise<SearchResponse> {
  const url = new URL("/search", API_BASE_URL);
  url.searchParams.set("q", query);
  url.searchParams.set("limit", String(limit));

  let response: Response;
  try {
    response = await fetch(url.toString(), { signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    throw new ApiError("Could not reach the search service. Please check your connection.", 0);
  }

  if (!response.ok) {
    let detail = `Search request failed (${response.status}).`;
    try {
      const body = (await response.json()) as ApiErrorBody;
      if (body?.detail) {
        detail = body.detail;
      }
    } catch {
      // response body wasn't JSON; fall back to the generic message above.
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as SearchResponse;
}

/** Calls GET /stats. Returns null on any failure since this is a nice-to-have. */
export async function statsApi(signal?: AbortSignal): Promise<StatsResponse | null> {
  try {
    const url = new URL("/stats", API_BASE_URL);
    const response = await fetch(url.toString(), { signal });
    if (!response.ok) return null;
    return (await response.json()) as StatsResponse;
  } catch {
    return null;
  }
}
