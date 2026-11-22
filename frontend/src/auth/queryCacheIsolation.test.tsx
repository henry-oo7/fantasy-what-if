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
} from "../test/accountHarness";
import type { AuthenticatedUser } from "../types/auth";
import { SESSION_REPLACED_MESSAGE } from "./accountMessages";

const LEAGUE_KEY = ["league", "current"] as const;
const SETUP_KEY = ["setup", "status"] as const;

vi.mock("../App", async () => {
  const { useQuery } = await import("@tanstack/react-query");
  const { useNavigate } = await import("react-router-dom");
  const { getJson } = await import("../api/client");
  const { useAuth } = await import("./AuthProvider");

  function ApplicationHome() {
    const auth = useAuth();
    const navigate = useNavigate();
    const league = useQuery({
      queryKey: ["league", "current"],
      queryFn: ({ signal }) =>
        getJson<{ team: string }>("/leagues/current", signal),
    });

    return (
      <section>
        <h1>Application home</h1>
        <p data-testid="league">{league.data?.team ?? "no data"}</p>
        <button
          type="button"
          onClick={() => {
            void getJson("/leagues/standings").catch(() => undefined);
          }}
        >
          Probe protected
        </button>
        <button
          type="button"
          onClick={() => {
            void auth.refreshUser();
          }}
        >
          Refresh session
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

function serveLeague(api: ReturnType<typeof createApiMock>, team: string) {
  api.reply("GET /api/leagues/current", 200, { team });
}

async function openWithCachedLeague(
  user: AuthenticatedUser = ADMIN_USER,
  team = "Team Alpha",
) {
  const api = createApiMock();
  signInAs(api, user);
  serveLeague(api, team);

  const view = renderWithProviders(<AppRouter />, "/");

  await waitFor(() => {
    expect(screen.getByTestId("league")).toHaveTextContent(team);
  });
  expect(view.queryClient.getQueryData(LEAGUE_KEY)).toEqual({ team });

  return { api, queryClient: view.queryClient };
}

describe("authenticated query-cache isolation", () => {
  it.each([
    ["successful logout", 200, { logged_out: true }],
    ["already-invalid session", 401, GENERIC_UNAUTHENTICATED],
  ])("removes cached workspace data on %s", async (_label, status, body) => {
    const { api, queryClient } = await openWithCachedLeague();
    api.reply("POST /api/auth/logout", status, body);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(queryClient.getQueryData(LEAGUE_KEY)).toBeUndefined();
    // Public installation status survives, so the guard does not refetch.
    expect(queryClient.getQueryData(SETUP_KEY)).toBeDefined();
  });

  it("removes cached data on session_replaced before the sign-in page renders", async () => {
    const { api, queryClient } = await openWithCachedLeague();
    const removeQueries = vi.spyOn(queryClient, "removeQueries");
    const loginVisibleAtPurge: boolean[] = [];

    removeQueries.mockImplementation(function (this: typeof queryClient, ...args) {
      loginVisibleAtPurge.push(
        screen.queryByRole("heading", { name: "Sign in to continue" }) !== null,
      );
      removeQueries.mockRestore();

      return queryClient.removeQueries(...args);
    });

    api.reply("GET /api/leagues/standings", 401, SESSION_REPLACED_BODY);
    api.reply("GET /api/auth/me", 401, SESSION_REPLACED_BODY);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Probe protected" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(SESSION_REPLACED_MESSAGE);
    expect(loginVisibleAtPurge).toEqual([false]);
    expect(queryClient.getQueryData(LEAGUE_KEY)).toBeUndefined();
  });

  it("removes cached data on generic invalidation without the replacement notice", async () => {
    const { api, queryClient } = await openWithCachedLeague();
    api.reply("GET /api/leagues/standings", 401, GENERIC_UNAUTHENTICATED);
    api.reply("GET /api/auth/me", 401, GENERIC_UNAUTHENTICATED);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Probe protected" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.queryByText(SESSION_REPLACED_MESSAGE)).not.toBeInTheDocument();
    expect(queryClient.getQueryData(LEAGUE_KEY)).toBeUndefined();
  });

  it.each([
    ["workspace_suspended", "This workspace is suspended", ADMIN_USER],
    ["workspace_inactive", "This workspace is not available", SPECTATOR_USER],
  ] as const)(
    "removes cached data when %s blocks the session",
    async (code, heading, account) => {
      const { api, queryClient } = await openWithCachedLeague(account);
      api.reply("GET /api/leagues/standings", 403, {
        detail: "Workspace access is unavailable.",
        code,
        retryable: false,
      });
      const user = userEvent.setup();

      await user.click(screen.getByRole("button", { name: "Probe protected" }));

      expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
      expect(screen.queryByTestId("league")).not.toBeInTheDocument();
      expect(queryClient.getQueryData(LEAGUE_KEY)).toBeUndefined();
    },
  );

  it("never reuses a prior identity's data when a different identity takes over", async () => {
    const { api, queryClient } = await openWithCachedLeague(ADMIN_USER, "Team Alpha");

    // Another account's session now owns the browser cookie (for example
    // after verifying a different registration in this browser).
    const otherAdministrator: AuthenticatedUser = {
      ...ADMIN_USER,
      id: 99,
      display_name: "Other Administrator",
      email: "other@example.test",
    };
    let releaseBeta: (response: Response) => void = () => undefined;
    signInAs(api, otherAdministrator);
    api.on(
      "GET /api/leagues/current",
      () => new Promise<Response>((resolve) => {
        releaseBeta = resolve;
      }),
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Refresh session" }));

    await waitFor(() => {
      expect(queryClient.getQueryData(LEAGUE_KEY)).toBeUndefined();
    });
    expect(screen.getByTestId("league")).not.toHaveTextContent("Team Alpha");

    releaseBeta(jsonResponse(200, { team: "Team Beta" }));

    await waitFor(() => {
      expect(screen.getByTestId("league")).toHaveTextContent("Team Beta");
    });
  });

  it("signing in as another account after logout loads only that account's data", async () => {
    const { api } = await openWithCachedLeague(ADMIN_USER, "Team Alpha");
    api.reply("POST /api/auth/logout", 200, { logged_out: true });
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in to continue" });

    api.reply("GET /api/auth/me", 401, GENERIC_UNAUTHENTICATED);
    api.on("POST /api/auth/spectator/login", () => {
      signInAs(api, SPECTATOR_USER);
      serveLeague(api, "Team Beta");

      return jsonResponse(200, {
        user: { ...SPECTATOR_USER, workspace_id: 8, workspace_slug: "beta-league" },
        csrf_token: "c",
      });
    });

    await user.click(screen.getByRole("radio", { name: "Spectator" }));
    await user.type(screen.getByLabelText("Workspace", { exact: false }), "beta-league");
    await user.type(screen.getByLabelText("Spectator password"), "shared passphrase");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const league = await screen.findByTestId("league");
    expect(league).not.toHaveTextContent("Team Alpha");
    await waitFor(() => {
      expect(league).toHaveTextContent("Team Beta");
    });
  });

  it("does not clear valid cache on rerenders or successful re-verification", async () => {
    const { queryClient } = await openWithCachedLeague();
    const removeQueries = vi.spyOn(queryClient, "removeQueries");
    const user = userEvent.setup();

    // Same identity, successful workspace probe.
    await user.click(screen.getByRole("button", { name: "Refresh session" }));
    await user.click(screen.getByRole("button", { name: "Refresh session" }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Application home" })).toBeInTheDocument();
    });
    expect(removeQueries).not.toHaveBeenCalled();
    expect(queryClient.getQueryData(LEAGUE_KEY)).toEqual({ team: "Team Alpha" });
    expect(screen.getByTestId("league")).toHaveTextContent("Team Alpha");
  });
});
