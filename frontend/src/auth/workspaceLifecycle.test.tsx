import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppRouter } from "../router";
import {
  ADMIN_USER,
  GENERIC_UNAUTHENTICATED,
  SESSION_REPLACED_BODY,
  SPECTATOR_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  type ApiMock,
} from "../test/accountHarness";
import type { AuthenticatedUser, OnboardingStage } from "../types/auth";
import { SESSION_REPLACED_MESSAGE } from "./accountMessages";
import { onboardingStageMessage, workspaceAccessFor } from "./workspaceLifecycle";

vi.mock("../App", () => ({
  default: () => <h1>Application home</h1>,
}));

vi.mock("../pages/AdminPage", () => ({
  AdminPage: () => <h1>Administration</h1>,
}));

vi.mock("../components/layout/AppShell", async () => {
  const { Outlet } = await import("react-router-dom");

  return {
    AppShell: () => (
      <div data-testid="app-shell">
        <Outlet />
      </div>
    ),
  };
});

const SUSPENDED_HEADING = "This workspace is suspended";
const INACTIVE_HEADING = "This workspace is not available";
const ONBOARDING_HEADING = "Your workspace is reserved";

function expectNoLifecycleProbe(api: ApiMock) {
  expect(api.calls("GET /api/leagues/seasons")).toHaveLength(0);
  expect(
    api.requests.filter(
      (request) => request.path.startsWith("/api/leagues") || request.path.startsWith("/api/admin"),
    ),
  ).toHaveLength(0);
}

describe("GET /api/auth/me is the lifecycle source", () => {
  it("resolves an active administrator from one /me request and no league probe", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);

    renderWithProviders(<AppRouter />, "/admin");

    expect(await screen.findByRole("heading", { name: "Administration" })).toBeInTheDocument();
    expect(api.calls("GET /api/auth/me")).toHaveLength(1);
    expectNoLifecycleProbe(api);
  });

  it("routes an active spectator to the application and away from administration", async () => {
    const api = createApiMock();
    signInAs(api, SPECTATOR_USER);

    renderWithProviders(<AppRouter />, "/admin");

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Administration" })).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
    expectNoLifecycleProbe(api);
  });

  it.each<[OnboardingStage, string]>([
    ["provider_setup_pending", "Next step: connect your fantasy provider."],
    ["league_claim_pending", "Next step: confirm and claim your ESPN league."],
    ["initial_sync_failed", "Loading your league data did not finish."],
  ])("routes a provisional administrator at %s to onboarding with safe copy", async (stage, copy) => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER, "provisional", { onboardingStage: stage });

    renderWithProviders(<AppRouter />, "/");

    expect(await screen.findByRole("heading", { name: ONBOARDING_HEADING })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/onboarding");
    expect(screen.getByTestId("onboarding-stage")).toHaveTextContent(copy);
    expect(document.body.innerHTML).not.toContain(stage);
    expectNoLifecycleProbe(api);
  });

  it.each([
    ["administrator", ADMIN_USER, "suspended", SUSPENDED_HEADING],
    ["spectator", SPECTATOR_USER, "suspended", SUSPENDED_HEADING],
    ["administrator", ADMIN_USER, "inactive", INACTIVE_HEADING],
    ["spectator", SPECTATOR_USER, "inactive", INACTIVE_HEADING],
  ] as const)("shows the dedicated screen for a %s in a %s workspace", async (_role, account, access, heading) => {
    const api = createApiMock();
    signInAs(api, account, access);

    renderWithProviders(<AppRouter />, "/");

    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
    expectNoLifecycleProbe(api);
  });

  it("never sends a spectator to provisional onboarding", async () => {
    const api = createApiMock();
    // Defensive: the backend never returns this, but if it did the
    // spectator must be blocked rather than offered onboarding.
    api.reply("GET /api/auth/me", 200, {
      ...SPECTATOR_USER,
      workspace: { ...SPECTATOR_USER.workspace, workspace_access: "provisional" },
    });

    renderWithProviders(<AppRouter />, "/onboarding");

    expect(await screen.findByRole("heading", { name: INACTIVE_HEADING })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: ONBOARDING_HEADING })).not.toBeInTheDocument();
  });

  it("treats an unknown workspace_access value as inactive", async () => {
    const api = createApiMock();
    api.reply("GET /api/auth/me", 200, {
      ...ADMIN_USER,
      workspace: { ...ADMIN_USER.workspace, workspace_access: "frozen" },
    });

    renderWithProviders(<AppRouter />, "/");

    expect(await screen.findByRole("heading", { name: INACTIVE_HEADING })).toBeInTheDocument();
  });
});

describe("workspaceAccessFor and onboardingStageMessage", () => {
  it("maps malformed /me payloads to inactive", () => {
    const missing = { ...ADMIN_USER, workspace: undefined } as unknown as AuthenticatedUser;

    expect(workspaceAccessFor(missing)).toBe("inactive");
    expect(workspaceAccessFor(ADMIN_USER)).toBe("active");
  });

  it("never returns raw or unknown stage values", () => {
    expect(onboardingStageMessage("provider_setup_pending")).not.toContain("_");
    expect(onboardingStageMessage("not_a_stage")).toBeNull();
    expect(onboardingStageMessage("toString")).toBeNull();
    expect(onboardingStageMessage(null)).toBeNull();
  });
});

describe("authentication makes exactly one /me request and no probe", () => {
  it("administrator login", async () => {
    const api = createApiMock();
    api.on("POST /api/auth/admin/login", () => {
      signInAs(api, ADMIN_USER, "provisional");

      return jsonResponse(200, { user: { ...ADMIN_USER, workspace_id: 7 }, csrf_token: "c" });
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/login");
    await screen.findByRole("heading", { name: "Sign in to continue" });
    const initialMeCalls = api.calls("GET /api/auth/me").length;

    await user.type(screen.getByLabelText("Email"), "administrator@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: ONBOARDING_HEADING })).toBeInTheDocument();
    expect(api.calls("GET /api/auth/me").length - initialMeCalls).toBe(1);
    expectNoLifecycleProbe(api);
  });

  it("spectator login", async () => {
    const api = createApiMock();
    api.on("POST /api/auth/spectator/login", () => {
      signInAs(api, SPECTATOR_USER);

      return jsonResponse(200, {
        user: { ...SPECTATOR_USER, workspace_id: 7, workspace_slug: "alpha-league" },
        csrf_token: "c",
      });
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/login");
    await screen.findByRole("heading", { name: "Sign in to continue" });
    const initialMeCalls = api.calls("GET /api/auth/me").length;

    await user.click(screen.getByRole("radio", { name: "Spectator" }));
    await user.type(screen.getByLabelText("Workspace", { exact: false }), "alpha-league");
    await user.type(screen.getByLabelText("Spectator password"), "shared passphrase");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
    expect(api.calls("GET /api/auth/me").length - initialMeCalls).toBe(1);
    expectNoLifecycleProbe(api);
  });

  it("email verification", async () => {
    const api = createApiMock();
    api.on("POST /api/registration/verify-email", () => {
      signInAs(api, ADMIN_USER, "provisional");

      return jsonResponse(200, {
        workspace_slug: "alpha-league",
        display_name: ADMIN_USER.display_name,
        email: ADMIN_USER.email,
        role: "admin",
        registration_status: "provisional",
        onboarding_stage: "provider_setup_pending",
        csrf_token: "session-csrf",
      });
    });

    renderWithProviders(<AppRouter />, "/verify-email?token=example-token-value");

    expect(await screen.findByRole("heading", { name: ONBOARDING_HEADING })).toBeInTheDocument();
    // One initial session check, then one /me adopting the verified session.
    await waitFor(() => {
      expect(api.calls("GET /api/auth/me")).toHaveLength(2);
    });
    expect(api.calls("POST /api/registration/verify-email")).toHaveLength(1);
    expectNoLifecycleProbe(api);
  });
});

describe("session endings are unchanged", () => {
  it("session_replaced on /me shows the safe administrator sign-in notice", async () => {
    const api = createApiMock();
    api.reply("GET /api/auth/me", 401, SESSION_REPLACED_BODY);

    renderWithProviders(<AppRouter />, "/");

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(SESSION_REPLACED_MESSAGE);
    expectNoLifecycleProbe(api);
  });

  it("a generic 401 on /me signs out without the notice", async () => {
    const api = createApiMock();
    api.reply("GET /api/auth/me", 401, GENERIC_UNAUTHENTICATED);

    renderWithProviders(<AppRouter />, "/");

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.queryByText(SESSION_REPLACED_MESSAGE)).not.toBeInTheDocument();
    expectNoLifecycleProbe(api);
  });
});
