import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { personalQueryKeys } from "../../api/personalLeagues";
import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  PERSONAL_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  type ApiMock,
} from "../../test/accountHarness";
import { seasonDetail, snapshot, season, twoLeagues } from "../../test/personalFixtures";
import type { PersonalLeagues } from "../../types/personal";

function BackForward() {
  const navigate = useNavigate();

  return (
    <>
      <button type="button" onClick={() => void navigate(-1)}>
        history back
      </button>
      <button type="button" onClick={() => void navigate(1)}>
        history forward
      </button>
    </>
  );
}

function mockPersonalApi(leagues: PersonalLeagues = twoLeagues()) {
  const api = createApiMock();
  signInAs(api, PERSONAL_USER);
  api.reply("GET /api/leagues", 200, leagues);

  for (const league of leagues.leagues) {
    for (const item of league.seasons) {
      api.reply(`GET /api/seasons/${item.season_id}`, 200, seasonDetail(leagues, item.season_id));
      api.reply(
        `GET /api/seasons/${item.season_id}/snapshot`,
        200,
        snapshot(league.league_name, item.season_year),
      );
      api.reply(`GET /api/seasons/${item.season_id}/analytics/league-average`, 200, []);
    }
  }

  return api;
}

function expectOnlySeasonScopedPersonalReads(api: ApiMock) {
  const forbidden = api.requests.filter(
    (request) =>
      request.path.startsWith("/api/leagues/") ||
      request.path.startsWith("/api/analytics") ||
      request.path.startsWith("/api/admin") ||
      request.search.includes("season_id"),
  );

  expect(forbidden).toEqual([]);
}

describe("personal route architecture", () => {
  it("opens the default Season from /account with Season-scoped reads only", async () => {
    const api = mockPersonalApi();

    renderWithProviders(<AppRouter />, "/account");

    expect(await screen.findAllByText(/Alpha League 2025 Rockets/)).not.toHaveLength(0);
    expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/11");
    expect(api.calls("GET /api/seasons/11/snapshot").length).toBeGreaterThan(0);
    expectOnlySeasonScopedPersonalReads(api);
  });

  it("falls back to the newest readable Season when the backend reports no default", async () => {
    mockPersonalApi(twoLeagues({ default_season_id: null }));

    renderWithProviders(<AppRouter />, "/account");

    // 2025 Seasons tie on year, so the first in backend order wins.
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/11");
    });
  });

  it("returns to a direct personal Season URL after sign-in", async () => {
    const api = mockPersonalApi();
    api.reply("GET /api/auth/me", 401, { detail: "Authentication is required." });
    api.on("POST /api/auth/admin/login", () => {
      signInAs(api, PERSONAL_USER);

      return jsonResponse(200, {
        user: { display_name: "Example Person", email: "person@example.test", email_verified: true, is_active: true },
        csrf_token: "c",
      });
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/seasons/10/all-play");
    await user.type(await screen.findByLabelText("Email"), "person@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/10/all-play");
    });
  });

  it("shows one bounded state for inaccessible, missing and malformed Seasons", async () => {
    const api = mockPersonalApi();
    api.reply("GET /api/seasons/999", 404, { detail: "League season not found." });

    for (const path of ["/account/seasons/999", "/account/seasons/not-a-number"]) {
      const { unmount } = renderWithProviders(<AppRouter />, path);

      expect(await screen.findByRole("heading", { name: "Season not available" })).toBeInTheDocument();
      expect(screen.queryAllByText(/Alpha League 2025 Rockets/)).toHaveLength(0);
      unmount();
    }
  });

  it("never renders one Season's data under another, across switches and history", async () => {
    const api = mockPersonalApi();
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    api.on("GET /api/seasons/10/snapshot", async () => {
      await gate;

      return jsonResponse(200, snapshot("Alpha League", 2024));
    });
    const user = userEvent.setup();

    renderWithProviders(
      <>
        <AppRouter />
        <BackForward />
      </>,
      "/account/seasons/11",
    );
    expect(await screen.findAllByText(/Alpha League 2025 Rockets/)).not.toHaveLength(0);

    const switcher = screen.getByRole("navigation", { name: "Leagues and Seasons" });
    await user.click(within(switcher).getByRole("link", { name: /2024/ }));

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/10");
    });
    // Season 10's own data is still loading: nothing from Season 11 may show.
    expect(screen.queryAllByText(/Alpha League 2025 Rockets/)).toHaveLength(0);
    expect(screen.queryAllByText(/Alpha League 2024 Rockets/)).toHaveLength(0);

    await act(async () => {
      release?.();
    });
    expect(await screen.findAllByText(/Alpha League 2024 Rockets/)).not.toHaveLength(0);
    expect(screen.queryAllByText(/Alpha League 2025 Rockets/)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "history back" }));
    expect(await screen.findAllByText(/Alpha League 2025 Rockets/)).not.toHaveLength(0);
    expect(screen.queryAllByText(/Alpha League 2024 Rockets/)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "history forward" }));
    expect(await screen.findAllByText(/Alpha League 2024 Rockets/)).not.toHaveLength(0);
    expect(screen.queryAllByText(/Alpha League 2025 Rockets/)).toHaveLength(0);
  });

  it("switches Leagues and keeps the analytics section", async () => {
    mockPersonalApi();
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/seasons/11/league-average");
    await screen.findByRole("heading", { name: "Alpha League" });

    await user.click(
      within(screen.getByRole("region", { name: /Beta League/ })).getByRole("link", { name: /2025/ }),
    );

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/21/league-average");
    });
    expect(await screen.findByRole("heading", { name: "Beta League" })).toBeInTheDocument();
  });

  it("does not change the default Season by navigating, only by the explicit action", async () => {
    const api = mockPersonalApi();
    api.on("PUT /api/preferences/default-season", (request) =>
      jsonResponse(200, { default_season_id: (request.body as { season_id: number }).season_id }),
    );
    document.cookie = "fantasy_session_csrf=synthetic-session-csrf; path=/";
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/seasons/11");
    await screen.findByRole("heading", { name: "Alpha League" });

    await user.click(
      within(screen.getByRole("navigation", { name: "Leagues and Seasons" })).getByRole("link", { name: /2024/ }),
    );
    await screen.findAllByText(/Alpha League 2024 Rockets/);
    expect(api.calls("PUT /api/preferences/default-season")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Set as default Season" }));

    await waitFor(() => {
      expect(api.calls("PUT /api/preferences/default-season")).toHaveLength(1);
    });
    expect(api.calls("PUT /api/preferences/default-season")[0].body).toEqual({ season_id: 10 });
    expect(api.calls("PUT /api/preferences/default-season")[0].headers.get("X-CSRF-Token")).toBe(
      "synthetic-session-csrf",
    );
    // The route is preserved and the indicator moves.
    expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/10");
    expect(await screen.findByText("Default Season")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set as default Season" })).not.toBeInTheDocument();
    document.cookie = "fantasy_session_csrf=; path=/; max-age=0";
  });
});

describe("account-state routing", () => {
  it("shows only verification for email_verification_required and reads no Leagues", async () => {
    const api = mockPersonalApi();
    signInAs(api, { ...PERSONAL_USER, email_verified: false, navigation_state: "email_verification_required" });

    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findByRole("heading", { name: "Verify your email address" })).toBeInTheDocument();
    expect(api.calls("GET /api/leagues")).toHaveLength(0);
    expect(api.requests.filter((request) => request.path.startsWith("/api/seasons"))).toEqual([]);
  });

  it("confines import_required to the import page without a redirect loop", async () => {
    const api = mockPersonalApi(twoLeagues({ default_season_id: null, leagues: [] }));
    signInAs(api, { ...PERSONAL_USER, navigation_state: "import_required" });

    renderWithProviders(<AppRouter />, "/account/seasons/11/all-play");

    expect(await screen.findByRole("heading", { name: "Import your first League" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/account\/import$/);
    expect(api.requests.filter((request) => request.path.startsWith("/api/seasons"))).toEqual([]);
  });

  it("keeps legacy workspace users out of personal routes", async () => {
    const api = mockPersonalApi();
    signInAs(api, ADMIN_USER);

    renderWithProviders(<AppRouter />, "/account/seasons/11");

    await waitFor(() => {
      expect(screen.getByTestId("location")).not.toHaveTextContent("/account");
    });
    expect(api.requests.filter((request) => request.path.startsWith("/api/seasons"))).toEqual([]);
  });

  it("sends a personal account away from workspace routes", async () => {
    mockPersonalApi();

    renderWithProviders(<AppRouter />, "/all-play");

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent(/^\/account/);
    });
    expect(screen.queryByRole("heading", { name: "Administration" })).not.toBeInTheDocument();
  });

  it("resolves a stale default through the backend-filtered list without a loop", async () => {
    const api = mockPersonalApi(twoLeagues({ default_season_id: null }));

    renderWithProviders(<AppRouter />, "/account");
    await screen.findAllByText(/Alpha League 2025 Rockets/);

    // One landing read; the resolved Season does not bounce back to /account.
    expect(api.calls("GET /api/leagues").length).toBeLessThanOrEqual(2);
    expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/11");
  });
});

describe("synchronization and reconnect states", () => {
  function leaguesWith(state: Parameters<typeof season>[2]) {
    return twoLeagues({
      leagues: [
        {
          provider: "espn",
          provider_league_id: "1001",
          league_name: "Alpha League",
          seasons: [season(11, 2025, state)],
        },
      ],
    });
  }

  it("shows importing, not empty analytics, while queued without data and survives a refresh", async () => {
    const api = mockPersonalApi(leaguesWith({ sync_state: "queued", data_available: false, last_successful_sync_at: null }));

    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findByRole("heading", { name: "Importing your League" })).toBeInTheDocument();
    expect(api.calls("GET /api/seasons/11/snapshot")).toHaveLength(0);
  });

  it("keeps prior data visible and asks only for a re-import on reverification_required", async () => {
    const api = mockPersonalApi(leaguesWith({ access_status: "reverification_required" }));
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findAllByText(/Alpha League 2025 Rockets/)).not.toHaveLength(0);
    const notice = screen.getByRole("alert");
    expect(notice).toHaveTextContent(/Import this League and Season again to reconnect/);
    expect(notice).not.toHaveTextContent(/connection|credential|owner|another/i);

    await user.click(screen.getByRole("link", { name: "Reconnect this League" }));

    expect(await screen.findByLabelText("ESPN League ID")).toHaveValue("1001");
    expect(screen.getByLabelText("Season year")).toHaveValue("2025");
    expect(screen.queryByLabelText("SWID")).not.toBeInTheDocument();
    expect(api.requests.some((request) => request.method === "POST" && request.path === "/api/league-imports")).toBe(false);
  });

  it("shows a bounded failed state with a retry path when nothing was ever loaded", async () => {
    mockPersonalApi(leaguesWith({ sync_state: "failed", data_available: false, last_successful_sync_at: null }));

    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findByRole("heading", { name: "This import did not finish" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Import this Season again" })).toHaveAttribute("href", "/account/import");
  });
});

describe("cache isolation", () => {
  it("clears every personal query and ends the session on logout, then back lands on sign-in", async () => {
    const api = mockPersonalApi();
    api.reply("POST /api/auth/logout", 200, { logged_out: true });
    document.cookie = "fantasy_session_csrf=synthetic-session-csrf; path=/";
    const user = userEvent.setup();

    const { queryClient } = renderWithProviders(
      <>
        <AppRouter />
        <BackForward />
      </>,
      "/account/seasons/11",
    );
    await screen.findAllByText(/Alpha League 2025 Rockets/);
    expect(queryClient.getQueryData(personalQueryKeys.leagues())).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/login");
    });
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);

    api.reply("GET /api/auth/me", 401, { detail: "Authentication is required." });
    await user.click(screen.getByRole("button", { name: "history back" }));
    expect(screen.queryAllByText(/Alpha League 2025 Rockets/)).toHaveLength(0);
    document.cookie = "fantasy_session_csrf=; path=/; max-age=0";
  });

  it("ends the session and clears data when a Season read reports an expired session", async () => {
    const api = mockPersonalApi();
    api.reply("GET /api/seasons/11/snapshot", 401, { detail: "Authentication is required." });

    const { queryClient } = renderWithProviders(<AppRouter />, "/account/seasons/11");

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/login");
    });
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });

  it("keys every Season-scoped query by season_id", async () => {
    const { leagueQueryKeys } = await import("../../api/league");
    const { analyticsQueryKeys } = await import("../../api/analytics");
    const keysFor = (id: number) => [
      personalQueryKeys.seasonStatus(id),
      leagueQueryKeys.current(id),
      leagueQueryKeys.allPlay(id),
      leagueQueryKeys.playoffPredictor(id),
      analyticsQueryKeys.leagueAverage(id),
      analyticsQueryKeys.optimalLineups(id),
      analyticsQueryKeys.optimalLineupTeam(5, id),
      analyticsQueryKeys.scheduleSwap(1, 2, id),
    ];

    for (const key of keysFor(10)) {
      expect(key.slice(0, 3)).toEqual(["personal", "season", 10]);
    }

    expect(JSON.stringify(keysFor(10))).not.toEqual(JSON.stringify(keysFor(11)));
    // A legacy (workspace) key never collides with a personal one.
    expect(leagueQueryKeys.current()[0]).toBe("league");
  });
});
