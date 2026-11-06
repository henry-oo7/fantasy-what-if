const apiBasePath = `${import.meta.env.BASE_URL}api`;

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function getJson<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${apiBasePath}${path}`, {
    headers: {
      Accept: "application/json",
    },
    signal,
  });

  if (!response.ok) {
    throw new ApiError(
      `Request failed with status ${response.status}.`,
      response.status,
    );
  }

  return response.json() as Promise<T>;
}