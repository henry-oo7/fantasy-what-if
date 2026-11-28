const apiBasePath = `${import.meta.env.BASE_URL}api`;

export interface ApiErrorDetails {
  code?: string | null;
  retryable?: boolean | null;
  retryAfterSeconds?: number | null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code: string | null;
  readonly retryable: boolean | null;
  readonly retryAfterSeconds: number | null;

  constructor(
    message: string,
    status: number,
    detail: string,
    details: ApiErrorDetails = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = details.code ?? null;
    this.retryable = details.retryable ?? null;
    this.retryAfterSeconds = details.retryAfterSeconds ?? null;
  }
}

/**
 * Listeners observe failed requests that opted into error reporting.
 *
 * Authentication state uses this to react to session_replaced and
 * workspace lifecycle responses from any protected request. Public
 * account endpoints opt out so their expected 401/403 responses never
 * change global authentication state.
 */
type ApiErrorListener = (error: ApiError) => void;

const apiErrorListeners = new Set<ApiErrorListener>();

export function subscribeToApiErrors(
  listener: ApiErrorListener,
): () => void {
  apiErrorListeners.add(listener);

  return () => {
    apiErrorListeners.delete(listener);
  };
}

interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  csrfToken?: string | null;
  reportErrors?: boolean;
}

interface ErrorPayload {
  detail?: unknown;
  code?: unknown;
  retryable?: unknown;
}

const MAXIMUM_RETRY_AFTER_SECONDS = 24 * 60 * 60;

export function parseRetryAfter(
  value: string | null,
): number | null {
  if (value === null || !/^\d+$/.test(value.trim())) {
    return null;
  }

  const seconds = Number.parseInt(value.trim(), 10);

  if (!Number.isFinite(seconds) || seconds < 0) {
    return null;
  }

  return Math.min(seconds, MAXIMUM_RETRY_AFTER_SECONDS);
}

async function parseError(response: Response): Promise<ApiError> {
  let detail = `Request failed with status ${response.status}.`;
  let code: string | null = null;
  let retryable: boolean | null = null;

  try {
    const payload = (await response.json()) as ErrorPayload;

    if (typeof payload.detail === "string" && payload.detail.trim()) {
      detail = payload.detail;
    }

    if (typeof payload.code === "string" && payload.code.trim()) {
      code = payload.code;
    }

    if (typeof payload.retryable === "boolean") {
      retryable = payload.retryable;
    }
  } catch {
    // Keep the status-based fallback for non-JSON errors.
  }

  return new ApiError(detail, response.status, detail, {
    code,
    retryable,
    retryAfterSeconds: parseRetryAfter(
      response.headers.get("Retry-After"),
    ),
  });
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const {
    body,
    csrfToken,
    reportErrors = true,
    headers: providedHeaders,
    ...requestOptions
  } = options;

  const headers = new Headers(providedHeaders);

  headers.set("Accept", "application/json");

  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }

  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(`${apiBasePath}${path}`, {
    ...requestOptions,
    credentials: "include",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await parseError(response);

    if (reportErrors) {
      for (const listener of apiErrorListeners) {
        listener(error);
      }
    }

    throw error;
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

function withViewedSeason(path: string): string {
  // Personal Season routes carry the Season in the path, never in the query.
  if (typeof window === "undefined" || path.startsWith("/seasons/")) {
    return path;
  }

  const selectedSeasonId = new URLSearchParams(window.location.search).get(
    "season_id",
  );

  if (selectedSeasonId === null || !/^\d+$/.test(selectedSeasonId)) {
    return path;
  }

  const [pathname, existingQuery = ""] = path.split("?", 2);
  const search = new URLSearchParams(existingQuery);

  if (!search.has("season_id")) {
    search.set("season_id", selectedSeasonId);
  }

  const query = search.toString();

  return query ? `${pathname}?${query}` : pathname;
}

export function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiRequest<T>(withViewedSeason(path), {
    method: "GET",
    signal,
  });
}

export function postJson<T>(
  path: string,
  body: unknown,
  options: {
    csrfToken?: string | null;
    signal?: AbortSignal;
    reportErrors?: boolean;
  } = {},
): Promise<T> {
  return apiRequest<T>(path, {
    method: "POST",
    body,
    csrfToken: options.csrfToken,
    signal: options.signal,
    reportErrors: options.reportErrors,
  });
}

export function patchJson<T>(
  path: string,
  body: unknown,
  options: {
    csrfToken?: string | null;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  return apiRequest<T>(path, {
    method: "PATCH",
    body,
    csrfToken: options.csrfToken,
    signal: options.signal,
  });
}

export function putJson<T>(
  path: string,
  body: unknown,
  options: {
    csrfToken?: string | null;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  return apiRequest<T>(path, {
    method: "PUT",
    body,
    csrfToken: options.csrfToken,
    signal: options.signal,
  });
}
