import {
  ApiError,
} from "../api/client";


const DEFAULT_POLL_INTERVAL_MS = 2_000;
const DEFAULT_POLL_TIMEOUT_MS = 300_000;


export function isAmbiguousLongRequestError(
  error: unknown,
): boolean {
  if (error instanceof ApiError) {
    return (
      error.status === 502 ||
      error.status === 504
    );
  }

  return error instanceof TypeError;
}


export interface PollUntilOptions<T> {
  readStatus: () => Promise<T>;
  isComplete: (status: T) => boolean;
  isFailed?: (status: T) => boolean;
  failureMessage?: (status: T) => string;
  intervalMs?: number;
  timeoutMs?: number;
}


export async function pollUntilComplete<T>({
  readStatus,
  isComplete,
  isFailed = () => false,
  failureMessage = () =>
    "The background operation failed.",
  intervalMs = DEFAULT_POLL_INTERVAL_MS,
  timeoutMs = DEFAULT_POLL_TIMEOUT_MS,
}: PollUntilOptions<T>): Promise<T> {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const status = await readStatus();

    if (isComplete(status)) {
      return status;
    }

    if (isFailed(status)) {
      throw new Error(
        failureMessage(status),
      );
    }

    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, intervalMs);
    });
  }

  throw new Error(
    "The operation is still running. Refresh the page to check its latest status before trying again.",
  );
}
