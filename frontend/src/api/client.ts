const apiBasePath = `${import.meta.env.BASE_URL}api`;

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(
    message: string,
    status: number,
    detail: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

interface ApiRequestOptions
  extends Omit<RequestInit, "body"> {
  body?: unknown;
  csrfToken?: string | null;
}

interface ErrorPayload {
  detail?: string;
}

async function parseError(
  response: Response,
): Promise<ApiError> {
  let detail = `Request failed with status ${response.status}.`;

  try {
    const payload =
      (await response.json()) as ErrorPayload;

    if (
      typeof payload.detail === "string" &&
      payload.detail.trim()
    ) {
      detail = payload.detail;
    }
  } catch {
    // Keep the status-based fallback for non-JSON errors.
  }

  return new ApiError(
    detail,
    response.status,
    detail,
  );
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const {
    body,
    csrfToken,
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

  const response = await fetch(
    `${apiBasePath}${path}`,
    {
      ...requestOptions,
      credentials: "include",
      headers,
      body:
        body === undefined
          ? undefined
          : JSON.stringify(body),
    },
  );

  if (!response.ok) {
    throw await parseError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export function getJson<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  return apiRequest<T>(path, {
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
  } = {},
): Promise<T> {
  return apiRequest<T>(path, {
    method: "POST",
    body,
    csrfToken: options.csrfToken,
    signal: options.signal,
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

