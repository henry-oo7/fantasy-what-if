import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ACTIVATE_WORKSPACE_LABEL } from "../../auth/accountMessages";
import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  type ApiMock,
} from "../../test/accountHarness";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

vi.mock("../../components/layout/AppShell", async () => {
  const { Outlet } = await import("react-router-dom");

  return {
    AppShell: () => (
      <div data-testid="app-shell">
        <Outlet />
      </div>
    ),
  };
});

const REVIEW = "GET /api/onboarding/activation";
const ACTIVATE = "POST /api/onboarding/activate";
const SESSION_CSRF = "synthetic-session-csrf";
const ALLOWED_PATHS = new Set([
  "/api/auth/me",
  "/api/onboarding/activation",
  "/api/onboarding/activate",
]);

const READY = {
  workspace_slug: "alpha-league",
  provider: "espn",
  league_id: "424242",
  season_year: 2026,
  access_mode: "private",
  synchronization_complete: true,
  spectator_access_ready: true,
  ready: true,
  blocking_category: null,
};

beforeEach(() => {
  document.cookie = `fantasy_session_csrf=${SESSION_CSRF}; path=/`;
});

afterEach(() => {
  document.cookie = "fantasy_session_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

async function openReview(review: Record<string, unknown> = READY) {
  const api = createApiMock();
  signInAs(api, ADMIN_USER, "provisional", { onboardingStage: "complete" });
  api.reply(REVIEW, 200, review);
  const view = renderWithProviders(<AppRouter />, "/onboarding");

  await screen.findByRole("heading", { name: "Review and activate" });
  await screen.findByText("alpha-league");

  return { api, ...view };
}

function activateButton() {
  return screen.getByRole("button", { name: /Activate workspace|Activating/ });
}

function expectOnlyActivationRequests(api: ApiMock) {
  for (const request of api.requests) {
    expect(ALLOWED_PATHS.has(request.path)).toBe(true);
  }
}

describe("activation review", () => {
  it("shows a safe summary and one activate action", async () => {
    const { api } = await openReview();

    expect(screen.getByText("ESPN")).toBeInTheDocument();
    expect(screen.getByText("424242")).toBeInTheDocument();
    expect(screen.getByText("2026")).toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(screen.getByText(/opens your synchronized workspace/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: ACTIVATE_WORKSPACE_LABEL })).toBeEnabled();
    expect(document.body.textContent).not.toMatch(/swid|espn_s2|password hash|spectator-|payload/i);
    expect(api.calls(ACTIVATE)).toHaveLength(0);
  });

  it("blocks activation with a bounded corrective message when not ready", async () => {
    const { api } = await openReview({
      ...READY,
      synchronization_complete: false,
      ready: false,
      blocking_category: "synchronization",
    });

    expect(screen.getByText(/Initial synchronization has not completed/)).toBeInTheDocument();
    expect(activateButton()).toBeDisabled();
    expect(api.calls(ACTIVATE)).toHaveLength(0);
  });
});

describe("activation", () => {
  it.each(["activated", "already_active"])(
    "activates once, purges provisional data, refreshes /me once, and opens the app (%s)",
    async (resultStatus) => {
      const { api, queryClient } = await openReview();
      let release: (response: Response) => void = () => undefined;
      api.on(
        ACTIVATE,
        () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }),
      );
      const user = userEvent.setup();
      const meBefore = api.calls("GET /api/auth/me").length;
      expect(queryClient.getQueryData(["onboarding", "activation"])).toBeDefined();

      await user.click(activateButton());
      await user.click(activateButton());

      expect(activateButton()).toBeDisabled();
      expect(activateButton()).toHaveAttribute("aria-busy", "true");

      signInAs(api, ADMIN_USER, "active");
      release(
        jsonResponse(200, {
          status: resultStatus,
          workspace_slug: "alpha-league",
          workspace_access: "active",
          onboarding_stage: "complete",
        }),
      );

      expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
      expect(api.calls(ACTIVATE)).toHaveLength(1);
      expect(api.calls(ACTIVATE)[0].body).toEqual({});
      expect(api.calls(ACTIVATE)[0].headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);
      expect(api.calls("GET /api/auth/me").length - meBefore).toBe(1);

      // No provisional onboarding data survives into the active application.
      expect(
        queryClient.getQueryCache().getAll().filter((query) => query.queryKey[0] === "onboarding"),
      ).toHaveLength(0);
      expectOnlyActivationRequests(api);
    },
  );

  it("stays in onboarding with a safe message when activation is not ready", async () => {
    const { api } = await openReview();
    api.reply(ACTIVATE, 409, {
      detail: "Shared spectator access is not configured and enabled.",
      code: "activation_not_ready",
      category: "spectator_access",
      retryable: false,
    });
    const user = userEvent.setup();
    const reviewsBefore = api.calls(REVIEW).length;

    await user.click(activateButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("not ready to activate");
    expect(screen.getByTestId("location")).toHaveTextContent("/onboarding");
    await waitFor(() => {
      expect(api.calls(REVIEW).length).toBeGreaterThan(reviewsBefore);
    });
    expect(screen.queryByRole("heading", { name: "Application home" })).not.toBeInTheDocument();
    expectOnlyActivationRequests(api);
  });
});
