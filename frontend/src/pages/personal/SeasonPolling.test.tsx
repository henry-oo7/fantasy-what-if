import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_POLL_ATTEMPTS, POLL_INTERVAL_MS } from "../../personal/useSeasonPolling";
import { AppRouter } from "../../router";
import { jsonResponse, renderWithProviders } from "../../test/accountHarness";
import { mockPersonalApi } from "../../test/personalApi";
import { seasonDetail, season, twoLeagues } from "../../test/personalFixtures";
import type { PersonalSeason } from "../../types/personal";

const STATUS = "GET /api/seasons/11";

function leaguesWith(state: Partial<PersonalSeason>) {
  const leagues = twoLeagues();
  leagues.leagues[0].seasons[0] = season(11, 2025, state);

  return leagues;
}

/** Advance fake time by whole poll ticks, letting the resulting fetches settle. */
async function tick(count = 1) {
  for (let index = 0; index < count; index += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
  }
}

async function mountSyncing(path = "/account/seasons/11", state: Partial<PersonalSeason> = { sync_state: "syncing" }) {
  const api = mockPersonalApi(leaguesWith(state));
  const view = renderWithProviders(<AppRouter />, path);
  await screen.findByRole("heading", { name: "Alpha League" });

  return { api, ...view };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("bounded Season status polling", () => {
  it.each(["queued", "syncing"] as const)("polls a %s Season every five seconds", async (syncState) => {
    const { api } = await mountSyncing("/account/seasons/11", { sync_state: syncState });
    const before = api.calls(STATUS).length;

    await tick();
    expect(api.calls(STATUS)).toHaveLength(before + 1);
    await tick(2);
    expect(api.calls(STATUS)).toHaveLength(before + 3);
  });

  it.each(["fresh", "stale", "failed"] as const)("does not poll a %s Season", async (syncState) => {
    const { api } = await mountSyncing("/account/seasons/11", { sync_state: syncState });
    const before = api.calls(STATUS).length;

    await tick(12);

    expect(api.calls(STATUS)).toHaveLength(before);
  });

  it("stops at the attempt limit and then shows the delayed message", async () => {
    const { api } = await mountSyncing();
    const before = api.calls(STATUS).length;

    await tick(MAX_POLL_ATTEMPTS - 1);
    expect(screen.queryByText(/taking longer than expected/)).not.toBeInTheDocument();

    await tick();
    expect(api.calls(STATUS)).toHaveLength(before + MAX_POLL_ATTEMPTS);
    expect(await screen.findByText(/taking longer than expected/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check again" })).toBeEnabled();

    await tick(10);
    expect(api.calls(STATUS)).toHaveLength(before + MAX_POLL_ATTEMPTS);
    // The message stays safe: no job, code or provider detail.
    expect(screen.getByText(/taking longer than expected/).parentElement).not.toHaveTextContent(
      /job|code|espn error|provider/i,
    );
  });

  it("Check again refreshes once at once, then restarts a fresh bounded window", async () => {
    const { api } = await mountSyncing();
    await tick(MAX_POLL_ATTEMPTS);
    await screen.findByText(/taking longer than expected/);
    const exhausted = api.calls(STATUS).length;
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    await user.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => {
      expect(api.calls(STATUS)).toHaveLength(exhausted + 1);
    });
    expect(screen.queryByText(/taking longer than expected/)).not.toBeInTheDocument();

    // Still syncing: polling resumes, one request per tick, bounded again.
    await tick(2);
    expect(api.calls(STATUS)).toHaveLength(exhausted + 3);
    await tick(MAX_POLL_ATTEMPTS - 2);
    expect(await screen.findByText(/taking longer than expected/)).toBeInTheDocument();
    expect(api.calls(STATUS)).toHaveLength(exhausted + 1 + MAX_POLL_ATTEMPTS);
  });

  it("Check again on a finished Season ends the notice and does not restart polling", async () => {
    const { api } = await mountSyncing();
    await tick(MAX_POLL_ATTEMPTS);
    await screen.findByText(/taking longer than expected/);
    api.reply(STATUS, 200, seasonDetail(leaguesWith({ sync_state: "fresh" }), 11));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    await user.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => {
      expect(screen.queryByText(/taking longer than expected/)).not.toBeInTheDocument();
    });
    const settled = api.calls(STATUS).length;
    await tick(5);

    expect(api.calls(STATUS)).toHaveLength(settled);
  });

  it("cancels the previous Season's polling on a Season switch", async () => {
    const { api } = await mountSyncing();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    await user.click(
      within(screen.getByRole("navigation", { name: "Leagues and Seasons" })).getByRole("link", { name: /2024/ }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/10");
    });
    const settled = api.calls(STATUS).length;
    await tick(6);

    expect(api.calls(STATUS)).toHaveLength(settled);
    expect(api.calls("GET /api/seasons/10").length).toBeLessThanOrEqual(2);
  });

  it("does not poll again after logout", async () => {
    const { api } = await mountSyncing();
    api.reply("POST /api/auth/logout", 200, { logged_out: true });
    document.cookie = "fantasy_session_csrf=synthetic-session-csrf; path=/";
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/login");
    });
    const settled = api.calls(STATUS).length;
    await tick(6);

    expect(api.calls(STATUS)).toHaveLength(settled);
    document.cookie = "fantasy_session_csrf=; path=/; max-age=0";
  });

  it("stops polling when a poll reports an expired session", async () => {
    const { api } = await mountSyncing();
    api.reply(STATUS, 401, { detail: "Authentication is required." });

    await tick();
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/login");
    });
    const settled = api.calls(STATUS).length;
    await tick(6);

    expect(api.calls(STATUS)).toHaveLength(settled);
  });

  it("stops polling on unmount", async () => {
    const { api, unmount } = await mountSyncing();
    unmount();
    const settled = api.calls(STATUS).length;

    await tick(6);

    expect(api.calls(STATUS)).toHaveLength(settled);
  });

  it("leaves no duplicate timers after the view is remounted", async () => {
    const { api } = await mountSyncing();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    for (let round = 0; round < 3; round += 1) {
      await user.click(screen.getByRole("link", { name: "Import another League" }));
      await screen.findByRole("heading", { name: "Import another League" });
      await user.click(
        within(screen.getByRole("navigation", { name: "Leagues and Seasons" })).getAllByRole("link", { name: /2025/ })[0],
      );
      await screen.findByRole("heading", { name: "Alpha League" });
    }
    const before = api.calls(STATUS).length;

    await tick();

    expect(api.calls(STATUS)).toHaveLength(before + 1);
  });

  it("refetches only the finished Season's own data when its sync completes", async () => {
    const leagues = leaguesWith({ sync_state: "syncing" });
    const { api } = await mountSyncing("/account/seasons/10", { sync_state: "syncing" });
    // Season 10 loaded (fresh); move to syncing Season 11 with data already stored.
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(
      within(screen.getByRole("navigation", { name: "Leagues and Seasons" })).getAllByRole("link", { name: /2025/ })[0],
    );
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/11");
    });
    await screen.findAllByText(/Alpha League 2025 Rockets/);
    const season10Snapshot = api.calls("GET /api/seasons/10/snapshot").length;
    const season11Snapshot = api.calls("GET /api/seasons/11/snapshot").length;
    const leaguesCalls = api.calls("GET /api/leagues").length;

    api.reply(STATUS, 200, seasonDetail(leaguesWith({ sync_state: "fresh" }), 11));
    api.on("GET /api/leagues", () => jsonResponse(200, leagues));
    await tick();

    await waitFor(() => {
      expect(api.calls("GET /api/seasons/11/snapshot").length).toBeGreaterThan(season11Snapshot);
    });
    expect(api.calls("GET /api/seasons/10/snapshot")).toHaveLength(season10Snapshot);
    expect(api.calls("GET /api/leagues").length).toBeGreaterThan(leaguesCalls);
  });
});
