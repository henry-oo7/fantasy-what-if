import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceDeletionRoute } from "../../auth/AccountRouteGuards";
import {
  formatDeletionCountdown,
  normalizeConfirmationSlug,
} from "../../auth/workspaceDeletionFormat";
import { WorkspaceDeletionSection } from "../../components/admin/WorkspaceDeletionSection";
import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  workspaceFor,
  type ApiMock,
} from "../../test/accountHarness";
import type { AuthenticatedUser } from "../../types/auth";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

const STATUS = "GET /api/admin/workspace/deletion";
const REQUEST = "POST /api/admin/workspace/deletion";
const CANCEL = "POST /api/admin/workspace/deletion/cancellation";
const SESSION_CSRF = "synthetic-session-csrf";
const PASSWORD = "correct horse battery staple";
const SCHEDULED_FOR = new Date(
  Date.now() + 29 * 24 * 60 * 60 * 1000 + 5 * 60 * 60 * 1000,
).toISOString();

const RECOVERY_USER: AuthenticatedUser = {
  ...ADMIN_USER,
  workspace: workspaceFor("inactive", { workspaceSlug: "alpha-league" }),
  session_purpose: "deletion_recovery",
};

const PENDING_STATUS = {
  status: "deletion_pending",
  requested_at: new Date().toISOString(),
  scheduled_for: SCHEDULED_FOR,
  cancellable: true,
};

let consoleSpies: ReturnType<typeof vi.spyOn>[] = [];

beforeEach(() => {
  document.cookie = `fantasy_session_csrf=${SESSION_CSRF}; path=/`;
  consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map(
    (method) => vi.spyOn(console, method),
  );
});

afterEach(() => {
  document.cookie =
    "fantasy_session_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  consoleSpies.forEach((spy) => spy.mockRestore());
  vi.restoreAllMocks();
});

type QueryClientLike = {
  getQueryCache: () => {
    getAll: () => { queryKey: unknown; state: { data: unknown } }[];
  };
};

function expectNoSecretLeaks(queryClient: QueryClientLike, secret: string) {
  expect(document.body.textContent).not.toContain(secret);
  expect(window.location.href).not.toContain(secret);
  expect(
    JSON.stringify(
      queryClient
        .getQueryCache()
        .getAll()
        .map((query) => [query.queryKey, query.state.data]),
    ),
  ).not.toContain(secret);

  for (const spy of consoleSpies) {
    expect(JSON.stringify(spy.mock.calls)).not.toContain(secret);
  }

  expect(window.localStorage.length).toBe(0);
  expect(window.sessionStorage.length).toBe(0);
}

/** No authenticated workspace data may survive a session end. */
function expectNoCachedData(queryClient: QueryClientLike) {
  expect(
    queryClient
      .getQueryCache()
      .getAll()
      .filter((query) => query.state.data !== undefined),
  ).toEqual([]);
}

function location() {
  return screen.getByTestId("location").textContent;
}

// --------------------------------------------------------------------------
// Danger zone
// --------------------------------------------------------------------------

function renderDangerZone(api: ApiMock) {
  signInAs(api, ADMIN_USER, "active");
  api.reply(STATUS, 200, {
    status: "active",
    grace_period_days: 30,
    confirmation_slug: "alpha-league",
    requested_at: null,
    scheduled_for: null,
    cancellable: null,
  });

  return renderWithProviders(
    <Routes>
      <Route path="/admin" element={<WorkspaceDeletionSection />} />
      <Route path="/workspace-deletion" element={<WorkspaceDeletionRoute />} />
      <Route path="/login" element={<h1>Sign in page</h1>} />
    </Routes>,
    "/admin",
  );
}

describe("danger zone", () => {
  it("requires the exact displayed workspace name and the current password", async () => {
    const api = createApiMock();
    renderDangerZone(api);
    const user = userEvent.setup();

    await screen.findByRole("heading", { name: "Danger zone" });
    expect(screen.getByText(/30 days/)).toBeInTheDocument();

    const submit = await screen.findByRole("button", { name: "Delete workspace" });
    const slug = screen.getByLabelText(/to confirm/);
    const password = screen.getByLabelText("Current password");

    expect(submit).toBeDisabled();

    await user.type(slug, "alpha-leagu");
    await user.type(password, PASSWORD);
    expect(submit).toBeDisabled();

    await user.clear(slug);
    await user.type(slug, "  ALPHA-league ");
    expect(submit).toBeEnabled();

    await user.clear(password);
    expect(submit).toBeDisabled();
    expect(api.calls(REQUEST)).toHaveLength(0);
  });

  it("does nothing when the confirmation dialog is dismissed", async () => {
    const api = createApiMock();
    renderDangerZone(api);
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);

    await user.type(await screen.findByLabelText(/to confirm/), "alpha-league");
    await user.type(screen.getByLabelText("Current password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Delete workspace" }));

    expect(api.calls(REQUEST)).toHaveLength(0);
  });

  it("submits once, clears auth and cache, and routes to the scheduled screen", async () => {
    const api = createApiMock();
    let release: () => void = () => undefined;
    api.on(REQUEST, async () => {
      await new Promise<void>((resolve) => {
        release = resolve;
      });

      return jsonResponse(202, {
        detail: "Workspace deletion was requested.",
        code: "workspace_deletion_requested",
        scheduled_for: SCHEDULED_FOR,
      });
    });
    const { queryClient } = renderDangerZone(api);
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    await user.type(await screen.findByLabelText(/to confirm/), "alpha-league");
    await user.type(screen.getByLabelText("Current password"), PASSWORD);

    const submit = screen.getByRole("button", { name: "Delete workspace" });
    await user.click(submit);
    await user.click(submit);

    await waitFor(() => {
      expect(api.calls(REQUEST)).toHaveLength(1);
    });
    expect(screen.getByLabelText("Current password")).toHaveValue("");

    release();

    await screen.findByRole("heading", { name: "Deletion is scheduled" });
    expect(location()).toBe("/workspace-deletion");
    expect(api.calls(REQUEST)).toHaveLength(1);

    const [request] = api.calls(REQUEST);
    expect(request.body).toEqual({
      current_password: PASSWORD,
      confirmation_slug: "alpha-league",
    });
    expect(request.headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);
    expectNoCachedData(queryClient);
    expect(
      screen.getByRole("link", { name: "Sign in to cancel deletion" }),
    ).toBeInTheDocument();
    expectNoSecretLeaks(queryClient, PASSWORD);
  });

  it("shows a safe error and clears the password on a wrong password", async () => {
    const api = createApiMock();
    api.reply(REQUEST, 400, {
      detail: "The current password is incorrect.",
      code: "invalid_current_password",
      retryable: true,
    });
    const { queryClient } = renderDangerZone(api);
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    await user.type(await screen.findByLabelText(/to confirm/), "alpha-league");
    await user.type(screen.getByLabelText("Current password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Delete workspace" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The current password is incorrect.",
    );
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(location()).toBe("/admin");
    expectNoSecretLeaks(queryClient, PASSWORD);
  });

  it("reports throttling with the retry time only", async () => {
    const api = createApiMock();
    api.reply(
      REQUEST,
      429,
      { detail: "Too many attempts.", code: "workspace_deletion_throttled" },
      { "Retry-After": "120" },
    );
    renderDangerZone(api);
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    await user.type(await screen.findByLabelText(/to confirm/), "alpha-league");
    await user.type(screen.getByLabelText("Current password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Delete workspace" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many deletion attempts. Try again in 2 minutes.",
    );
  });
});

// --------------------------------------------------------------------------
// Deletion-recovery session
// --------------------------------------------------------------------------

function recoverySignedIn(api: ApiMock, status: unknown = PENDING_STATUS) {
  api.reply("GET /api/auth/me", 200, RECOVERY_USER);
  api.reply(STATUS, 200, status);
}

describe("deletion recovery", () => {
  it("routes a recovery login only to the recovery screen", async () => {
    const api = createApiMock();
    let signedIn = false;
    api.on("GET /api/auth/me", () =>
      signedIn
        ? jsonResponse(200, RECOVERY_USER)
        : jsonResponse(401, { detail: "Authentication is required." }),
    );
    api.on("POST /api/auth/admin/login", () => {
      signedIn = true;

      return jsonResponse(200, {
        user: {
          id: ADMIN_USER.id,
          workspace_id: 7,
          display_name: ADMIN_USER.display_name,
          email: ADMIN_USER.email,
          role: "admin",
          is_active: true,
        },
        csrf_token: SESSION_CSRF,
        session_purpose: "deletion_recovery",
      });
    });
    api.reply(STATUS, 200, PENDING_STATUS);
    const { queryClient } = renderWithProviders(<AppRouter />, "/login");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Email"), "administrator@example.test");
    await user.type(screen.getByLabelText("Password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByRole("heading", { name: "Workspace scheduled for deletion" });
    expect(location()).toBe("/workspace-deletion");
    expect(await screen.findByText(/29 days, 5 hours remaining|29 days, 4 hours remaining/)).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Cancel workspace deletion" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/espn|provider|claim|job|username/i);
    expectNoSecretLeaks(queryClient, PASSWORD);
  });

  it.each(["/", "/admin", "/all-play", "/onboarding", "/login"])(
    "blocks the active application at %s",
    async (path) => {
      const api = createApiMock();
      recoverySignedIn(api);
      renderWithProviders(<AppRouter />, path);

      await screen.findByRole("heading", { name: "Workspace scheduled for deletion" });
      expect(location()).toBe("/workspace-deletion");
      expect(screen.queryByText("Application home")).not.toBeInTheDocument();
      expect(
        api.requests.filter(
          (request) =>
            request.path.startsWith("/api/") &&
            ![
              "/api/auth/me",
              "/api/auth/csrf",
              "/api/admin/workspace/deletion",
            ].includes(request.path),
        ),
      ).toEqual([]);
    },
  );

  it("cancels with the current password, clears auth, and requires sign-in", async () => {
    const api = createApiMock();
    recoverySignedIn(api);
    api.on(CANCEL, () => {
      api.reply("GET /api/auth/me", 401, { detail: "Authentication is required." });

      return jsonResponse(200, {
        detail: "Deletion canceled. Sign in again to continue.",
        code: "workspace_deletion_canceled",
      });
    });
    const { queryClient } = renderWithProviders(<AppRouter />, "/workspace-deletion");
    const user = userEvent.setup();

    const submit = await screen.findByRole("button", { name: "Cancel deletion" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("Current password"), PASSWORD);
    await user.click(submit);

    await screen.findByText("Deletion canceled. Sign in again to continue.");
    expect(location()).toBe("/login");
    expect(api.calls(CANCEL)).toHaveLength(1);
    expect(api.calls(CANCEL)[0].body).toEqual({ current_password: PASSWORD });
    expect(api.calls(CANCEL)[0].headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);
    expectNoCachedData(queryClient);
    expectNoSecretLeaks(queryClient, PASSWORD);
  });

  it("shows the unavailable state when cancellation is past the cutoff", async () => {
    const api = createApiMock();
    recoverySignedIn(api);
    api.reply(CANCEL, 409, {
      detail: "This workspace can no longer be restored.",
      code: "workspace_deletion_unavailable",
      retryable: false,
    });
    renderWithProviders(<AppRouter />, "/workspace-deletion");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Current password"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Cancel deletion" }));

    await screen.findByText("This workspace can no longer be restored.");
    expect(screen.queryByRole("form", { name: "Cancel workspace deletion" })).toBeNull();
    expect(document.body.textContent).not.toMatch(/cleanup|failed|started/i);
  });

  it("shows the unavailable state when the status is no longer cancellable", async () => {
    const api = createApiMock();
    recoverySignedIn(api, { ...PENDING_STATUS, cancellable: false });
    renderWithProviders(<AppRouter />, "/workspace-deletion");

    await screen.findByText("This workspace can no longer be restored.");
    expect(screen.queryByRole("form", { name: "Cancel workspace deletion" })).toBeNull();
  });

  it("reports a wrong password without leaving the recovery screen", async () => {
    const api = createApiMock();
    recoverySignedIn(api);
    api.reply(CANCEL, 400, {
      detail: "The current password is incorrect.",
      code: "invalid_current_password",
      retryable: true,
    });
    renderWithProviders(<AppRouter />, "/workspace-deletion");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Current password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Cancel deletion" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The current password is incorrect.",
    );
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(location()).toBe("/workspace-deletion");
  });

  it("signs out from the recovery screen", async () => {
    const api = createApiMock();
    recoverySignedIn(api);
    api.reply("POST /api/auth/logout", 200, { logged_out: true });
    renderWithProviders(<AppRouter />, "/workspace-deletion");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    await waitFor(() => {
      expect(location()).toBe("/login");
    });
    expect(api.calls("POST /api/auth/logout")).toHaveLength(1);
  });

  it("sends an anonymous visitor without a schedule to sign in", async () => {
    createApiMock();
    renderWithProviders(<AppRouter />, "/workspace-deletion");

    await waitFor(() => {
      expect(location()).toBe("/login");
    });
  });

  it("sends a standard session away from the recovery screen", async () => {
    const api = createApiMock();
    signInAs(api, { ...ADMIN_USER, session_purpose: "standard" }, "active");
    renderWithProviders(<AppRouter />, "/workspace-deletion");

    await screen.findByText("Application home");
    expect(location()).toBe("/");
  });
});

describe("deletion display helpers", () => {
  it("normalizes confirmation like the backend", () => {
    expect(normalizeConfirmationSlug("  Alpha-League ")).toBe("alpha-league");
  });

  it("formats the remaining time and stops at the cutoff", () => {
    const now = Date.parse("2026-11-01T00:00:00Z");

    expect(formatDeletionCountdown("2026-11-03T05:00:00Z", now)).toBe(
      "2 days, 5 hours remaining",
    );
    expect(formatDeletionCountdown("2026-11-01T01:01:00Z", now)).toBe(
      "1 hour, 1 minute remaining",
    );
    expect(formatDeletionCountdown("2026-11-01T00:00:30Z", now)).toBe(
      "Less than a minute remaining",
    );
    expect(formatDeletionCountdown("2026-11-01T00:00:00Z", now)).toBeNull();
    expect(formatDeletionCountdown("not a date", now)).toBeNull();
  });
});
