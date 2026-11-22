import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppRouter } from "../router";
import {
  ADMIN_USER,
  GENERIC_UNAUTHENTICATED,
  SAAS_ONLY_SETUP,
  SESSION_REPLACED_BODY,
  SPECTATOR_USER,
  createApiMock,
  renderWithProviders,
  signInAs,
} from "../test/accountHarness";
import { SESSION_REPLACED_MESSAGE } from "./accountMessages";

vi.mock("../App", async () => {
  const { getJson } = await import("../api/client");
  const { useAuth } = await import("./AuthProvider");
  const { useNavigate } = await import("react-router-dom");

  function ApplicationHome() {
    const auth = useAuth();
    const navigate = useNavigate();

    return (
      <section>
        <h1>Application home</h1>
        <button
          type="button"
          onClick={() => {
            void getJson("/leagues/current").catch(() => undefined);
          }}
        >
          Load league
        </button>
        <button
          type="button"
          onClick={() => {
            void auth.logoutUser().finally(() => {
              navigate("/login", { replace: true });
            });
          }}
        >
          Sign out
        </button>
      </section>
    );
  }

  return { default: ApplicationHome };
});

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

async function openApplication() {
  renderWithProviders(<AppRouter />, "/");

  return screen.findByRole("heading", { name: "Application home" });
}

describe("session replacement", () => {
  it("clears auth state, routes to administrator login, and shows the exact message", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);
    const user = userEvent.setup();

    await openApplication();

    api.reply("GET /api/leagues/current", 401, SESSION_REPLACED_BODY);
    api.reply("GET /api/auth/me", 401, SESSION_REPLACED_BODY);
    await user.click(screen.getByRole("button", { name: "Load league" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/login");

    const notice = screen.getByRole("status");
    expect(notice).toHaveTextContent(SESSION_REPLACED_MESSAGE);
    expect(notice.textContent).toBe(SESSION_REPLACED_MESSAGE);
    expect(screen.getByRole("radio", { name: "Administrator" })).toBeChecked();
    expect(screen.queryByRole("heading", { name: "Application home" })).not.toBeInTheDocument();
  });

  it("shows the replacement message when the replaced session is discovered on load", async () => {
    const api = createApiMock();
    api.reply("GET /api/auth/me", 401, SESSION_REPLACED_BODY);

    renderWithProviders(<AppRouter />, "/");

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(SESSION_REPLACED_MESSAGE);
  });

  it("does not treat a generic 401 as session replacement", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);
    const user = userEvent.setup();

    await openApplication();

    api.reply("GET /api/leagues/current", 401, GENERIC_UNAUTHENTICATED);
    api.reply("GET /api/auth/me", 401, GENERIC_UNAUTHENTICATED);
    await user.click(screen.getByRole("button", { name: "Load league" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.queryByText(SESSION_REPLACED_MESSAGE)).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not show replacement wording for a revoked spectator session", async () => {
    const api = createApiMock();
    signInAs(api, SPECTATOR_USER);
    const user = userEvent.setup();

    await openApplication();

    api.reply("GET /api/leagues/current", 401, GENERIC_UNAUTHENTICATED);
    await user.click(screen.getByRole("button", { name: "Load league" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.queryByText(SESSION_REPLACED_MESSAGE)).not.toBeInTheDocument();
  });

  it("keeps logout normal, even if the logout response is unauthenticated", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);
    api.reply("POST /api/auth/logout", 401, SESSION_REPLACED_BODY);
    const user = userEvent.setup();

    await openApplication();
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.queryByText(SESSION_REPLACED_MESSAGE)).not.toBeInTheDocument();
  });
});

describe("workspace lifecycle", () => {
  it("shows a dedicated suspended screen", async () => {
    const api = createApiMock();
    signInAs(api, SPECTATOR_USER, "suspended");

    renderWithProviders(<AppRouter />, "/");

    expect(
      await screen.findByRole("heading", { name: "This workspace is suspended" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/unable to reach/i)).not.toBeInTheDocument();
  });

  it("shows a dedicated inactive screen instead of a network error", async () => {
    const api = createApiMock();
    signInAs(api, SPECTATOR_USER, "inactive");

    renderWithProviders(<AppRouter />, "/");

    expect(
      await screen.findByRole("heading", { name: "This workspace is not available" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/unable to reach|network/i)).not.toBeInTheDocument();
  });

  it("switches to the suspended screen when suspension happens mid-session", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);
    const user = userEvent.setup();

    await openApplication();

    api.reply("GET /api/leagues/current", 403, {
      detail: "Workspace access is unavailable.",
      code: "workspace_suspended",
      retryable: false,
    });
    await user.click(screen.getByRole("button", { name: "Load league" }));

    expect(
      await screen.findByRole("heading", { name: "This workspace is suspended" }),
    ).toBeInTheDocument();
  });

  it("routes a provisional administrator away from the application", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER, "provisional");

    renderWithProviders(<AppRouter />, "/");

    expect(
      await screen.findByRole("heading", { name: "Your workspace is reserved" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/onboarding");
    expect(screen.queryByRole("link", { name: /setup/i })).not.toBeInTheDocument();
  });

  it("keeps spectators and active administrators out of onboarding", async () => {
    const api = createApiMock();
    signInAs(api, SPECTATOR_USER);

    renderWithProviders(<AppRouter />, "/onboarding");

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
  });
});

describe("routing", () => {
  it.each(["/login", "/register", "/forgot-password", "/verify-email", "/reset-password"])(
    "serves %s publicly on a SaaS deployment without legacy setup",
    async (path) => {
      const api = createApiMock();
      api.reply("GET /api/setup/status", 200, SAAS_ONLY_SETUP);

      renderWithProviders(<AppRouter />, path);

      expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
      expect(screen.getByTestId("location")).toHaveTextContent(path);
    },
  );

  it("sends SaaS visitors from legacy /setup to sign in", async () => {
    const api = createApiMock();
    api.reply("GET /api/setup/status", 200, SAAS_ONLY_SETUP);

    renderWithProviders(<AppRouter />, "/setup");

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
  });

  it("lets an authenticated SaaS user reach the application", async () => {
    const api = createApiMock();
    api.reply("GET /api/setup/status", 200, SAAS_ONLY_SETUP);
    signInAs(api, ADMIN_USER);

    renderWithProviders(<AppRouter />, "/");

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
  });

  it("still sends a legacy installation in progress to /setup", async () => {
    const api = createApiMock();
    api.reply("GET /api/setup/status", 200, {
      ...SAAS_ONLY_SETUP,
      status: "setup_in_progress",
      installation_exists: true,
    });

    renderWithProviders(<AppRouter />, "/");

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/setup");
    });
  });

  it("redirects an authenticated user away from the sign-in page", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);

    renderWithProviders(<AppRouter />, "/login");

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
  });

  it("ignores unsafe return paths after sign-in", async () => {
    const api = createApiMock();
    api.on("POST /api/auth/admin/login", () => {
      signInAs(api, ADMIN_USER);

      return new Response(JSON.stringify({ user: { ...ADMIN_USER, workspace_id: 1 }, csrf_token: "c" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, {
      pathname: "/login",
      state: { from: "//attacker.example/steal" },
    });

    await user.type(await screen.findByLabelText("Email"), "administrator@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
    expect(screen.getByTestId("location").textContent).toBe("/");
  });
});
