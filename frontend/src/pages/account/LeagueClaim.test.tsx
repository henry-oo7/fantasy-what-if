import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CLAIM_ACTION_LABEL,
  LEAGUE_ALREADY_CLAIMED_MESSAGE,
  LEAGUE_CONNECTED_MESSAGE,
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

const TARGET = "GET /api/onboarding/provider/target";
const CLAIM = "POST /api/onboarding/provider/claim";
const SESSION_CSRF = "synthetic-session-csrf";
const ALLOWED_PATHS = new Set([
  "/api/setup/status",
  "/api/auth/me",
  "/api/auth/csrf",
  "/api/onboarding/provider/target",
  "/api/onboarding/provider/claim",
]);

const PRIVATE_TARGET = {
  provider: "espn",
  league_id: "0424242",
  season_year: 2024,
  access_mode: "private",
};

const CLAIMED = {
  status: "claimed",
  code: "provider_league_claimed",
  onboarding_stage: "initial_sync_pending",
  target: PRIVATE_TARGET,
};

const CONFLICT_BODY = {
  detail: LEAGUE_ALREADY_CLAIMED_MESSAGE,
  code: "provider_league_already_claimed",
  retryable: false,
};

beforeEach(() => {
  document.cookie = `fantasy_session_csrf=${SESSION_CSRF}; path=/`;
});

afterEach(() => {
  document.cookie = "fantasy_session_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

function atStage(api: ApiMock, stage: OnboardingStage) {
  signInAs(api, ADMIN_USER, "provisional", { onboardingStage: stage });
}

async function openClaimStep() {
  const api = createApiMock();
  atStage(api, "league_claim_pending");
  api.reply(TARGET, 200, PRIVATE_TARGET);
  const view = renderWithProviders(<AppRouter />, "/onboarding");

  await screen.findByRole("heading", { name: "Confirm your league" });
  await screen.findByText("0424242");

  return { api, ...view };
}

function claimButton() {
  return screen.getByRole("button", { name: /Connect this ESPN league|Connecting league/ });
}

function expectNoForeignRequests(api: ApiMock) {
  for (const request of api.requests) {
    expect(ALLOWED_PATHS.has(request.path)).toBe(true);
  }
}

describe("league claim confirmation", () => {
  it("shows the safe validated target and claims nothing before confirmation", async () => {
    const { api } = await openClaimStep();

    expect(screen.getByText("ESPN")).toBeInTheDocument();
    expect(screen.getByText("0424242")).toBeInTheDocument();
    expect(screen.getByText("2024")).toBeInTheDocument();
    expect(screen.getByText("Private league")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: CLAIM_ACTION_LABEL })).toBeEnabled();
    expect(document.body.textContent).not.toMatch(/swid|espn_s2|credential/i);
    expect(api.calls(CLAIM)).toHaveLength(0);
    expectNoForeignRequests(api);
  });

  it("claims once, refreshes /me exactly once, and shows the connected step", async () => {
    const user = userEvent.setup();
    const { api } = await openClaimStep();
    let release: (response: Response) => void = () => undefined;
    api.on(
      CLAIM,
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const meBefore = api.calls("GET /api/auth/me").length;

    await user.click(claimButton());
    await user.click(claimButton());

    expect(claimButton()).toBeDisabled();
    expect(claimButton()).toHaveAttribute("aria-busy", "true");

    atStage(api, "initial_sync_pending");
    release(jsonResponse(200, CLAIMED));

    expect(await screen.findByText(LEAGUE_CONNECTED_MESSAGE)).toBeInTheDocument();

    const claims = api.calls(CLAIM);
    expect(claims).toHaveLength(1);
    expect(claims[0].body).toEqual({});
    expect(claims[0].headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);
    expect(api.calls("GET /api/auth/me").length - meBefore).toBe(1);

    // The connected placeholder offers no synchronization action.
    expect(screen.queryByRole("button", { name: /sync|connect/i })).not.toBeInTheDocument();
    expectNoForeignRequests(api);
  });

  it("clears auth and cached data before showing the public conflict screen", async () => {
    const user = userEvent.setup();
    const { api, queryClient } = await openClaimStep();
    api.reply(CLAIM, 409, CONFLICT_BODY);
    // The server removed the session; any later /me would be anonymous.
    api.reply("GET /api/auth/me", 401, { detail: "Authentication is required." });
    const removeQueries = vi.spyOn(queryClient, "removeQueries");
    const conflictVisibleAtPurge: boolean[] = [];
    removeQueries.mockImplementation(function (this: typeof queryClient, ...args) {
      conflictVisibleAtPurge.push(
        screen.queryByRole("heading", { name: "This league already has a workspace" }) !== null,
      );
      removeQueries.mockRestore();

      return queryClient.removeQueries(...args);
    });

    await user.click(claimButton());

    expect(
      await screen.findByRole("heading", { name: "This league already has a workspace" }),
    ).toBeInTheDocument();
    expect(conflictVisibleAtPurge).toEqual([false]);
    expect(screen.getByTestId("location")).toHaveTextContent("/league-already-connected");
    expect(screen.getByText(LEAGUE_ALREADY_CLAIMED_MESSAGE)).toBeInTheDocument();
    expect(queryClient.getQueryData(["onboarding", "provider-target"])).toBeUndefined();
    expect(
      queryClient.getQueryCache().getAll().filter((query) => query.queryKey[0] !== "setup"),
    ).toHaveLength(0);

    // Not replaced by a generic sign-in error, and no workspace details.
    expect(screen.queryByRole("heading", { name: "Sign in to continue" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/alpha-league|0424242|@/);

    expect(screen.getByRole("link", { name: "Administrator sign in" })).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "Spectator sign in" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Spectator" })).toBeChecked();
    expect(api.calls(CLAIM)).toHaveLength(1);
    expectNoForeignRequests(api);
  });

  it("shows safe detail for a non-conflict claim failure and allows retry", async () => {
    const user = userEvent.setup();
    const { api } = await openClaimStep();
    api.reply(CLAIM, 409, {
      detail: "This league cannot be connected at this onboarding step.",
      code: "onboarding_stage_invalid",
      retryable: false,
    });

    await user.click(claimButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This league cannot be connected at this onboarding step.",
    );
    expect(screen.getByTestId("location")).toHaveTextContent("/onboarding");
    expect(claimButton()).toBeEnabled();
  });

  it("shows a safe message when the validated target is unavailable", async () => {
    const api = createApiMock();
    atStage(api, "league_claim_pending");
    api.reply(TARGET, 409, {
      detail: "Check your league access again before connecting the league.",
      code: "provider_target_unavailable",
      retryable: false,
    });

    renderWithProviders(<AppRouter />, "/onboarding");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Check your league access again before connecting the league.",
    );
    expect(screen.queryByRole("button", { name: CLAIM_ACTION_LABEL })).not.toBeInTheDocument();
    expect(api.calls(CLAIM)).toHaveLength(0);
  });
});

describe("initial_sync_pending", () => {
  it("shows the connected message with no claim or sync action", async () => {
    const api = createApiMock();
    atStage(api, "initial_sync_pending");

    renderWithProviders(<AppRouter />, "/onboarding");

    expect(await screen.findByText(LEAGUE_CONNECTED_MESSAGE)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: CLAIM_ACTION_LABEL })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sync/i })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(api.calls(TARGET)).toHaveLength(0);
    });
    expect(document.body.textContent).not.toContain("initial_sync_pending");
  });
});

describe("public conflict page", () => {
  it("is reachable anonymously and discloses nothing", async () => {
    createApiMock();

    renderWithProviders(<AppRouter />, "/league-already-connected");

    expect(
      await screen.findByRole("heading", { name: "This league already has a workspace" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Administrator sign in" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Spectator sign in" })).toBeInTheDocument();
  });
});
