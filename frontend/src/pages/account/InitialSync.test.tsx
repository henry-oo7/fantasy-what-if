import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LEAGUE_CONNECTED_MESSAGE,
  LEAGUE_DATA_READY_MESSAGE,
  RETRY_INITIAL_SYNC_LABEL,
  START_INITIAL_SYNC_LABEL,
} from "../../auth/accountMessages";
import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  type ApiMock,
} from "../../test/accountHarness";
import type { OnboardingStage } from "../../types/auth";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

const PROGRESS = "GET /api/onboarding/synchronization";
const START = "POST /api/onboarding/synchronization";
const SESSION_CSRF = "synthetic-session-csrf";
const ALLOWED_PATHS = new Set([
  "/api/auth/me",
  "/api/onboarding/synchronization",
  // The spectator setup step reads its safe state after success.
  "/api/onboarding/spectator",
]);

function job(status: string, detail: string, extra: Record<string, unknown> = {}) {
  return {
    id: 41,
    status,
    retryable: status === "failed",
    detail,
    created_at: "2026-11-23T10:00:00Z",
    started_at: null,
    completed_at: null,
    failed_at: null,
    ...extra,
  };
}

const RUNNING = {
  onboarding_stage: "initial_sync_running",
  job: job("running", "The first import is running. This can take several minutes."),
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  document.cookie = `fantasy_session_csrf=${SESSION_CSRF}; path=/`;
});

afterEach(() => {
  vi.useRealTimers();
  document.cookie = "fantasy_session_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

function atStage(api: ApiMock, stage: OnboardingStage) {
  signInAs(api, ADMIN_USER, "provisional", { onboardingStage: stage });
}

async function open(stage: OnboardingStage, setup: (api: ApiMock) => void = () => undefined) {
  const api = createApiMock();
  atStage(api, stage);
  setup(api);
  const view = renderWithProviders(<AppRouter />, "/onboarding");

  await screen.findByRole("heading", { name: "Your workspace is reserved" });

  return { api, ...view };
}

function user() {
  return userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
}

async function advance(milliseconds: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

function expectNoForeignRequests(api: ApiMock) {
  for (const request of api.requests) {
    expect(ALLOWED_PATHS.has(request.path)).toBe(true);
  }
}

describe("initial_sync_pending", () => {
  it("starts once, prevents duplicates, and refreshes /me exactly once", async () => {
    const { api } = await open("initial_sync_pending");
    let release: (response: Response) => void = () => undefined;
    api.on(
      START,
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    api.reply(PROGRESS, 200, RUNNING);

    expect(screen.getByText(LEAGUE_CONNECTED_MESSAGE)).toBeInTheDocument();
    expect(screen.getByText(/may take several minutes/)).toBeInTheDocument();
    expect(api.calls(PROGRESS)).toHaveLength(0);
    const meBefore = api.calls("GET /api/auth/me").length;

    const actor = user();
    await actor.click(screen.getByRole("button", { name: START_INITIAL_SYNC_LABEL }));
    await actor.click(screen.getByRole("button", { name: /Starting|Start initial/ }));

    expect(screen.getByRole("button", { name: /Starting/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Starting/ })).toHaveAttribute("aria-busy", "true");

    atStage(api, "initial_sync_running");
    release(jsonResponse(200, { ...RUNNING, job: job("pending", "The first import is queued.") }));

    expect(await screen.findByText(/first import is queued|first import is running/)).toBeInTheDocument();

    const starts = api.calls(START);
    expect(starts).toHaveLength(1);
    expect(starts[0].body).toEqual({});
    expect(starts[0].headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);
    expect(api.calls("GET /api/auth/me").length - meBefore).toBe(1);
    expect(screen.queryByRole("button", { name: START_INITIAL_SYNC_LABEL })).not.toBeInTheDocument();
    expectNoForeignRequests(api);
  });
});

describe("initial_sync_running", () => {
  it("polls at a bounded interval, then shows spectator setup and stops polling", async () => {
    const { api } = await open("initial_sync_running", (mock) => {
      mock.reply(PROGRESS, 200, RUNNING);
    });

    expect(await screen.findByText(/first import is running/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: START_INITIAL_SYNC_LABEL })).not.toBeInTheDocument();
    const firstPolls = api.calls(PROGRESS).length;

    await advance(3_000);
    await waitFor(() => {
      expect(api.calls(PROGRESS).length).toBeGreaterThan(firstPolls);
    });

    const meBefore = api.calls("GET /api/auth/me").length;
    api.reply(PROGRESS, 200, {
      onboarding_stage: "spectator_setup_pending",
      job: job("succeeded", "Your league data is ready."),
    });
    atStage(api, "spectator_setup_pending");

    await advance(3_000);

    expect(await screen.findByText(LEAGUE_DATA_READY_MESSAGE)).toBeInTheDocument();
    expect(api.calls("GET /api/auth/me").length - meBefore).toBe(1);

    const pollsAtTerminal = api.calls(PROGRESS).length;
    await advance(15_000);
    expect(api.calls(PROGRESS).length).toBe(pollsAtTerminal);
    expect(screen.getByRole("heading", { name: "Create shared spectator access" })).toBeInTheDocument();
    expectNoForeignRequests(api);
  });

  it("renders only the safe detail, never raw job or exception fields", async () => {
    await open("initial_sync_running", (mock) => {
      mock.reply(PROGRESS, 200, {
        ...RUNNING,
        job: job("retry_scheduled", "ESPN did not finish responding. The first import will retry automatically.", {
          safe_error_code: "unavailable",
          traceback: "SECRET-TRACEBACK ProviderUnavailableError",
          payload: { trigger: "onboarding" },
        }),
      });
    });

    expect(await screen.findByText(/will retry automatically/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(
      /SECRET-TRACEBACK|ProviderUnavailableError|unavailable"|trigger|initial_sync_running/,
    );
  });
});

describe("initial_sync_failed", () => {
  it("shows the safe failure and retries without asking for credentials", async () => {
    const { api } = await open("initial_sync_running", (mock) => {
      mock.reply(PROGRESS, 200, RUNNING);
    });
    await screen.findByText(/first import is running/);

    api.reply(PROGRESS, 200, {
      onboarding_stage: "initial_sync_failed",
      job: job("failed", "ESPN was unavailable during the first import. Try again in a few minutes."),
    });
    atStage(api, "initial_sync_failed");
    await advance(3_000);

    expect(await screen.findByRole("alert")).toHaveTextContent("ESPN was unavailable");
    const retry = screen.getByRole("button", { name: RETRY_INITIAL_SYNC_LABEL });
    expect(screen.queryByLabelText("SWID")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("espn_s2")).not.toBeInTheDocument();

    api.reply(START, 200, { ...RUNNING, job: job("pending", "The first import is queued.", { id: 42 }) });
    atStage(api, "initial_sync_running");
    api.reply(PROGRESS, 200, RUNNING);

    await user().click(retry);

    expect(await screen.findByText(/first import is (queued|running)/)).toBeInTheDocument();
    expect(api.calls(START)).toHaveLength(1);
    expect(api.calls(START)[0].body).toEqual({});

    // Polling resumes for the new attempt.
    const pollsAfterRetry = api.calls(PROGRESS).length;
    await advance(3_000);
    await waitFor(() => {
      expect(api.calls(PROGRESS).length).toBeGreaterThan(pollsAfterRetry);
    });
    expectNoForeignRequests(api);
  });

  it("shows the persisted failure after a reload", async () => {
    await open("initial_sync_failed", (mock) => {
      mock.reply(PROGRESS, 200, {
        onboarding_stage: "initial_sync_failed",
        job: job("failed", "The first import did not finish. Try again."),
      });
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("The first import did not finish.");
    expect(screen.getByRole("button", { name: RETRY_INITIAL_SYNC_LABEL })).toBeEnabled();
    expect(document.body.textContent).not.toContain("initial_sync_failed");
  });
});

describe("spectator_setup_pending", () => {
  it("shows the spectator setup step and no sync action", async () => {
    const { api } = await open("spectator_setup_pending");

    expect(screen.getByText(LEAGUE_DATA_READY_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /synchronization/i })).not.toBeInTheDocument();
    expect(api.calls(START)).toHaveLength(0);
    expect(document.body.textContent).not.toContain("spectator_setup_pending");
  });
});
