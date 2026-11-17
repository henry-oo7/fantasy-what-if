import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  ApiError,
} from "../api/client";
import {
  isAmbiguousLongRequestError,
  pollUntilComplete,
} from "./longRequestRecovery";


describe("long request recovery", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("recognizes proxy and gateway timeouts as ambiguous", () => {
    expect(
      isAmbiguousLongRequestError(
        new ApiError(
          "Bad gateway",
          502,
          "Bad gateway",
        ),
      ),
    ).toBe(true);

    expect(
      isAmbiguousLongRequestError(
        new ApiError(
          "Gateway timeout",
          504,
          "Gateway timeout",
        ),
      ),
    ).toBe(true);
  });

  it("recognizes a browser network failure as ambiguous", () => {
    expect(
      isAmbiguousLongRequestError(
        new TypeError("Failed to fetch"),
      ),
    ).toBe(true);
  });

  it("does not hide a confirmed application error", () => {
    expect(
      isAmbiguousLongRequestError(
        new ApiError(
          "Conflict",
          409,
          "Synchronization already running.",
        ),
      ),
    ).toBe(false);
  });

  it("polls until persisted completion is observed", async () => {
    vi.useFakeTimers();

    const readStatus = vi
      .fn()
      .mockResolvedValueOnce({
        status: "running",
      })
      .mockResolvedValueOnce({
        status: "succeeded",
      });

    const resultPromise = pollUntilComplete<{
      status: string;
    }>({
      readStatus,
      isComplete: (value) =>
        value.status === "succeeded",
      intervalMs: 10,
      timeoutMs: 100,
    });

    await vi.advanceTimersByTimeAsync(10);

    await expect(resultPromise).resolves.toEqual({
      status: "succeeded",
    });

    expect(readStatus).toHaveBeenCalledTimes(2);
  });

  it("reports a persisted failure", async () => {
    const status = {
      status: "failed",
      safe_error_message:
        "ESPN rejected the synchronization request.",
    };

    await expect(
      pollUntilComplete({
        readStatus: async () => status,
        isComplete: (value) =>
          value.status === "succeeded",
        isFailed: (value) =>
          value.status === "failed",
        failureMessage: (value) =>
          value.safe_error_message,
        intervalMs: 1,
        timeoutMs: 100,
      }),
    ).rejects.toThrow(
      "ESPN rejected the synchronization request.",
    );
  });
});
