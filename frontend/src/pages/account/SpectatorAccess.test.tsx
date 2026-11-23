import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SPECTATOR_ACCESS_READY_MESSAGE } from "../../auth/accountMessages";
import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  type ApiMock,
} from "../../test/accountHarness";
import { AdminPage } from "../AdminPage";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

const SETUP = "POST /api/onboarding/spectator";
const STATE = "GET /api/onboarding/spectator";
const SESSION_CSRF = "synthetic-session-csrf";
const PASSWORD = "shared league phrase 0001";
const NEW_PASSWORD = "replacement league phrase 02";

let consoleSpies: ReturnType<typeof vi.spyOn>[] = [];

beforeEach(() => {
  document.cookie = `fantasy_session_csrf=${SESSION_CSRF}; path=/`;
  consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((method) =>
    vi.spyOn(console, method),
  );
});

afterEach(() => {
  document.cookie = "fantasy_session_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  consoleSpies.forEach((spy) => spy.mockRestore());
});

function expectNoSecretLeaks(
  queryClient: { getQueryCache: () => { getAll: () => { queryKey: unknown; state: { data: unknown } }[] } },
  ...secrets: string[]
) {
  for (const secret of secrets) {
    expect(document.body.textContent).not.toContain(secret);
    expect(window.location.href).not.toContain(secret);
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((query) => [query.queryKey, query.state.data]))).not.toContain(secret);

    for (const spy of consoleSpies) {
      expect(JSON.stringify(spy.mock.calls)).not.toContain(secret);
    }
  }

  expect(window.localStorage.length).toBe(0);
  expect(window.sessionStorage.length).toBe(0);
}

// --------------------------------------------------------------------------
// Onboarding setup
// --------------------------------------------------------------------------

async function openSetup() {
  const api = createApiMock();
  signInAs(api, ADMIN_USER, "provisional", { onboardingStage: "spectator_setup_pending" });
  api.reply(STATE, 200, {
    workspace_slug: "alpha-league",
    configured: false,
    enabled: false,
    onboarding_stage: "spectator_setup_pending",
  });
  const view = renderWithProviders(<AppRouter />, "/onboarding");

  await screen.findByRole("heading", { name: "Create shared spectator access" });
  await screen.findByText("alpha-league");

  return { api, ...view };
}

function passwordInput() {
  return screen.getByLabelText("Shared spectator password");
}

function confirmationInput() {
  return screen.getByLabelText("Confirm shared password");
}

function submitButton() {
  return screen.getByRole("button", { name: /shared spectator access|Saving shared access/ });
}

describe("onboarding shared spectator setup", () => {
  it("shows the workspace slug with only password and confirmation fields", async () => {
    await openSetup();

    const form = submitButton().closest("form");
    expect(form).not.toBeNull();
    const inputs = within(form as HTMLFormElement).queryAllByRole("textbox", { hidden: true });
    // Password inputs are not textboxes; there is no username or email field.
    expect(inputs).toHaveLength(0);
    expect(form?.querySelectorAll("input")).toHaveLength(2);
    expect(screen.queryByLabelText(/username/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument();
    expect(passwordInput()).toHaveAttribute("type", "password");
    expect(confirmationInput()).toHaveAttribute("type", "password");
    expect(screen.getByText(/Share both privately/)).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Show shared spectator password" }));
    expect(passwordInput()).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide shared spectator password" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("validates length and confirmation locally", async () => {
    const { api } = await openSetup();
    const user = userEvent.setup();

    await user.type(passwordInput(), "too short 13!");
    await user.type(confirmationInput(), "too short 13!");
    await user.click(submitButton());
    expect(await screen.findByText("Use at least 14 characters.")).toBeInTheDocument();

    await user.clear(passwordInput());
    await user.type(passwordInput(), PASSWORD);
    await user.clear(confirmationInput());
    await user.type(confirmationInput(), `${PASSWORD}x`);
    await user.click(submitButton());
    expect(await screen.findByText("The passwords do not match.")).toBeInTheDocument();
    expect(confirmationInput()).toHaveAttribute("aria-invalid", "true");
    expect(api.calls(SETUP)).toHaveLength(0);
  });

  it("submits once, clears fields, refreshes /me once, and stops before activation", async () => {
    const { api, queryClient } = await openSetup();
    let release: (response: Response) => void = () => undefined;
    api.on(
      SETUP,
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    const user = userEvent.setup();
    const meBefore = api.calls("GET /api/auth/me").length;

    await user.type(passwordInput(), PASSWORD);
    await user.type(confirmationInput(), PASSWORD);
    await user.click(submitButton());
    await user.click(submitButton());

    expect(submitButton()).toBeDisabled();
    expect(submitButton()).toHaveAttribute("aria-busy", "true");
    expect(passwordInput()).toHaveValue(PASSWORD);

    signInAs(api, ADMIN_USER, "provisional", { onboardingStage: "complete" });
    release(
      jsonResponse(200, {
        workspace_slug: "alpha-league",
        configured: true,
        enabled: true,
        onboarding_stage: "complete",
      }),
    );

    expect(await screen.findByText(SPECTATOR_ACCESS_READY_MESSAGE)).toBeInTheDocument();
    expect(api.calls(SETUP)).toHaveLength(1);
    expect(api.calls(SETUP)[0].body).toEqual({ password: PASSWORD });
    expect(api.calls(SETUP)[0].headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);
    expect(api.calls("GET /api/auth/me").length - meBefore).toBe(1);
    expect(screen.queryByLabelText("Shared spectator password")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(PASSWORD);

    // No activation, sync or claim mutation.
    for (const request of api.requests) {
      expect([
        "/api/auth/me",
        "/api/onboarding/spectator",
        // The complete-stage review is a safe read; nothing is activated.
        "/api/onboarding/activation",
      ]).toContain(
        request.path,
      );
    }

    expectNoSecretLeaks(queryClient, PASSWORD);
  });

  it("shows a safe server error and keeps the values for retry", async () => {
    const { api, queryClient } = await openSetup();
    api.reply(SETUP, 409, {
      detail:
        "Shared spectator access is already configured. Change the password from workspace administration.",
      code: "spectator_already_configured",
      retryable: false,
    });
    const user = userEvent.setup();

    await user.type(passwordInput(), PASSWORD);
    await user.type(confirmationInput(), PASSWORD);
    await user.click(submitButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("already configured");
    expect(passwordInput()).toHaveValue(PASSWORD);
    expect(submitButton()).toBeEnabled();
    expectNoSecretLeaks(queryClient, PASSWORD);
  });
});

// --------------------------------------------------------------------------
// Administrator management
// --------------------------------------------------------------------------

function adminApi(enabled: boolean): ApiMock {
  const api = createApiMock();
  signInAs(api, ADMIN_USER);
  api.reply("GET /api/admin/spectator", 200, {
    workspace_slug: "alpha-league",
    configured: true,
    enabled,
  });
  api.reply("GET /api/admin/sync-status", 200, {
    provider: "espn",
    season_id: 1,
    provider_league_id: "424242",
    provider_season_id: "2024",
    last_synced_at: null,
    latest_run: null,
  });
  api.reply("GET /api/admin/standings-tiebreaker", 200, {
    season_id: 1,
    provider_rule: null,
    provider_rule_supported: false,
    admin_fallback_rule: null,
    effective_rule: null,
    effective_rule_source: null,
  });

  return api;
}

async function openAdmin(enabled = true) {
  const api = adminApi(enabled);
  const view = renderWithProviders(<AdminPage />, "/admin");

  await screen.findByRole("heading", { name: "Shared spectator access" });

  return { api, ...view };
}

describe("administrator shared spectator management", () => {
  it("shows slug and status without any password or username", async () => {
    await openAdmin();

    expect(screen.getByText("alpha-league")).toBeInTheDocument();
    expect(screen.getByText("Access enabled")).toBeInTheDocument();
    expect(screen.getByText("Configured (never displayed)")).toBeInTheDocument();
    expect(screen.queryByText(/username/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument();
  });

  it("changes the password with confirmation and reports revoked sessions", async () => {
    const { api, queryClient } = await openAdmin();
    api.reply("POST /api/admin/spectator/reset-password", 200, {
      workspace_slug: "alpha-league",
      configured: true,
      enabled: true,
      revoked_sessions: 2,
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("New password"), "short pass");
    await user.type(screen.getByLabelText("Confirm password"), "short pass");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 14 characters");
    expect(api.calls("POST /api/admin/spectator/reset-password")).toHaveLength(0);

    await user.clear(screen.getByLabelText("New password"));
    await user.clear(screen.getByLabelText("Confirm password"));
    await user.type(screen.getByLabelText("New password"), NEW_PASSWORD);
    await user.type(screen.getByLabelText("Confirm password"), NEW_PASSWORD);
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText(/2 spectator session\(s\) were signed out/)).toBeInTheDocument();
    expect(api.calls("POST /api/admin/spectator/reset-password")[0].body).toEqual({
      password: NEW_PASSWORD,
    });
    expect(screen.getByLabelText("New password")).toHaveValue("");
    expectNoSecretLeaks(queryClient, NEW_PASSWORD);
  });

  it("disables access without a password", async () => {
    const { api } = await openAdmin();
    api.reply("PATCH /api/admin/spectator/access", 200, {
      workspace_slug: "alpha-league",
      configured: true,
      enabled: false,
      revoked_sessions: 3,
    });

    await userEvent.setup().click(screen.getByRole("button", { name: "Disable access" }));

    expect(await screen.findByText(/3 session\(s\) were signed out/)).toBeInTheDocument();
    expect(api.calls("PATCH /api/admin/spectator/access")[0].body).toEqual({ is_active: false });
  });

  it("re-enables access only with a new confirmed password", async () => {
    const { api, queryClient } = await openAdmin(false);
    api.reply("PATCH /api/admin/spectator/access", 200, {
      workspace_slug: "alpha-league",
      configured: true,
      enabled: true,
      revoked_sessions: 0,
    });
    const user = userEvent.setup();

    expect(screen.getByText("Access disabled")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change password" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Enable access with new password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("at least 14 characters");
    expect(api.calls("PATCH /api/admin/spectator/access")).toHaveLength(0);

    await user.type(screen.getByLabelText("New password"), NEW_PASSWORD);
    await user.type(screen.getByLabelText("Confirm password"), NEW_PASSWORD);
    await user.click(screen.getByRole("button", { name: "Enable access with new password" }));

    await waitFor(() => {
      expect(api.calls("PATCH /api/admin/spectator/access")).toHaveLength(1);
    });
    expect(api.calls("PATCH /api/admin/spectator/access")[0].body).toEqual({
      is_active: true,
      password: NEW_PASSWORD,
    });
    expect(await screen.findByText(/enabled with the new shared password/)).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toHaveValue("");
    expectNoSecretLeaks(queryClient, NEW_PASSWORD);
  });
});

// --------------------------------------------------------------------------
// Spectator sign-in compatibility
// --------------------------------------------------------------------------

describe("spectator sign-in", () => {
  it("submits only the workspace slug and shared password", async () => {
    const api = createApiMock();
    api.reply("POST /api/auth/spectator/login", 401, {
      detail: "The workspace or spectator credentials are incorrect.",
    });
    renderWithProviders(<AppRouter />, "/login");
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "Sign in to continue" });
    await user.click(screen.getByRole("radio", { name: "Spectator" }));
    await user.type(screen.getByLabelText("Workspace", { exact: false }), "alpha-league");
    await user.type(screen.getByLabelText("Spectator password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The workspace or spectator credentials are incorrect.",
    );
    expect(api.calls("POST /api/auth/spectator/login")[0].body).toEqual({
      workspace_slug: "alpha-league",
      password: PASSWORD,
    });
    expect(screen.queryByLabelText(/username|email/i)).not.toBeInTheDocument();
  });
});
