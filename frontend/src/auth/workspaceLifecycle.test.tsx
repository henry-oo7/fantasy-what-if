import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppRouter } from "../router";
import {
  ADMIN_USER,
  GENERIC_UNAUTHENTICATED,
  PERSONAL_USER,
  SESSION_REPLACED_BODY,
  SPECTATOR_USER,
  createApiMock,
  jsonResponse,
  legacyWorkspaceOf,
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

/** Personal accounts legitimately read GET /api/leagues; the workspace routes stay unreachable. */
function expectNoWorkspaceProbe(api: ApiMock) {
  expect(
    api.requests.filter(
      (request) => request.path.startsWith("/api/leagues/") || request.path.startsWith("/api/admin"),
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
      legacy_workspace: {
        role: "spectator",
        workspace: { ...legacyWorkspaceOf(SPECTATOR_USER), workspace_access: "provisional" },
      },
    });

    renderWithProviders(<AppRouter />, "/onboarding");

    expect(await screen.findByRole("heading", { name: INACTIVE_HEADING })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: ONBOARDING_HEADING })).not.toBeInTheDocument();
  });

  it("treats an unknown workspace_access value as inactive", async () => {
    const api = createApiMock();
    api.reply("GET /api/auth/me", 200, {
      ...ADMIN_USER,
      legacy_workspace: {
        role: "admin",
        workspace: { ...legacyWorkspaceOf(ADMIN_USER), workspace_access: "frozen" },
      },
    });

    renderWithProviders(<AppRouter />, "/");

    expect(await screen.findByRole("heading", { name: INACTIVE_HEADING })).toBeInTheDocument();
  });
});

describe("workspaceAccessFor and onboardingStageMessage", () => {
  it("maps malformed /me payloads to inactive", () => {
    const missing = {
      ...ADMIN_USER,
      legacy_workspace: { role: "admin", workspace: undefined },
    } as unknown as AuthenticatedUser;

    expect(workspaceAccessFor(missing)).toBe("inactive");
    expect(workspaceAccessFor(ADMIN_USER)).toBe("active");
  });

  it("returns personal for an account without a legacy workspace", () => {
    expect(workspaceAccessFor(PERSONAL_USER)).toBe("personal");
  });

  it.each([
    ["active", ADMIN_USER, "active"],
    ["provisional", ADMIN_USER, "provisional"],
    ["suspended", ADMIN_USER, "suspended"],
    ["inactive", ADMIN_USER, "inactive"],
    ["provisional", SPECTATOR_USER, "inactive"],
  ] as const)("keeps legacy %s workspace access for the right role", (workspaceAccess, account, expected) => {
    const legacy = account.legacy_workspace;
    const user: AuthenticatedUser = {
      ...account,
      legacy_workspace: {
        role: legacy!.role,
        workspace: { ...legacyWorkspaceOf(account), workspace_access: workspaceAccess },
      },
    };

    expect(workspaceAccessFor(user)).toBe(expected);
  });

  it("routes a deletion-recovery administrator to deletion_recovery", () => {
    expect(
      workspaceAccessFor({ ...ADMIN_USER, session_purpose: "deletion_recovery" }),
    ).toBe("deletion_recovery");
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

      return jsonResponse(200, {
        user: {
          display_name: ADMIN_USER.display_name,
          email: ADMIN_USER.email,
          email_verified: true,
          is_active: true,
          workspace_id: 7,
          role: "admin",
        },
        csrf_token: "c",
      });
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
        user: {
          workspace_id: 7,
          workspace_slug: "alpha-league",
          display_name: SPECTATOR_USER.display_name,
          role: "spectator",
          is_active: true,
        },
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
        display_name: ADMIN_USER.display_name,
        email: ADMIN_USER.email,
        email_verified: true,
        navigation_state: "legacy_workspace",
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

describe("personal accounts", () => {
  const IMPORT_HEADING = "Import your first League";

  function expectNoWorkspaceSurface() {
    expect(screen.queryByRole("heading", { name: ONBOARDING_HEADING })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Application home" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Administration" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
  }

  it.each(["/", "/onboarding", "/login", "/register", "/admin", "/account"])(
    "sends a personal account at %s to /account without workspace pages",
    async (path) => {
      const api = createApiMock();
      signInAs(api, { ...PERSONAL_USER, navigation_state: "import_required" });

      renderWithProviders(<AppRouter />, path);

      expect(await screen.findByRole("heading", { name: IMPORT_HEADING })).toBeInTheDocument();
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/account\/import$/);
      expectNoWorkspaceSurface();
      expectNoWorkspaceProbe(api);
    },
  );

  it("shows the first-League import and a sign-out button for import_required", async () => {
    const api = createApiMock();
    signInAs(api, { ...PERSONAL_USER, navigation_state: "import_required" });
    api.reply("GET /api/leagues", 200, { default_season_id: null, leagues: [] });

    renderWithProviders(<AppRouter />, "/account");

    expect(await screen.findByRole("heading", { name: IMPORT_HEADING })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });

  it("sends an empty personal_dashboard account to the import page", async () => {
    const api = createApiMock();
    signInAs(api, PERSONAL_USER);
    api.reply("GET /api/leagues", 200, { default_season_id: null, leagues: [] });

    renderWithProviders(<AppRouter />, "/account");

    expect(await screen.findByRole("heading", { name: IMPORT_HEADING })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });

  it("keeps legacy users away from /account", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);

    renderWithProviders(<AppRouter />, "/account");

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/);
  });

  it("signs out from the personal page", async () => {
    const api = createApiMock();
    signInAs(api, PERSONAL_USER);
    api.reply("POST /api/auth/logout", 200, { logged_out: true });
    document.cookie = "fantasy_session_csrf=synthetic-session-csrf; path=/";
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account");
    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/login");
    });
    expect(api.calls("POST /api/auth/logout")).toHaveLength(1);
    document.cookie = "fantasy_session_csrf=; path=/; max-age=0";
  });

  it("login of a personal user with no workspace or role ends at /account", async () => {
    const api = createApiMock();
    api.reply("GET /api/leagues", 200, { default_season_id: null, leagues: [] });
    api.on("POST /api/auth/admin/login", () => {
      signInAs(api, PERSONAL_USER);

      return jsonResponse(200, {
        user: {
          display_name: PERSONAL_USER.display_name,
          email: PERSONAL_USER.email,
          email_verified: true,
          is_active: true,
        },
        csrf_token: "c",
      });
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/login");
    await screen.findByRole("heading", { name: "Sign in to continue" });
    const initialMeCalls = api.calls("GET /api/auth/me").length;

    await user.type(screen.getByLabelText("Email"), "person@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: IMPORT_HEADING })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/account\/import$/);
    expect(api.calls("GET /api/auth/me").length - initialMeCalls).toBe(1);
    expectNoWorkspaceSurface();
    expectNoWorkspaceProbe(api);
  });
});
