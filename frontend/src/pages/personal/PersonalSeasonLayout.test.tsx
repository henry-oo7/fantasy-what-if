import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppRouter } from "../../router";
import {
  PERSONAL_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
} from "../../test/accountHarness";
import { seasonDetail, season, snapshot, twoLeagues } from "../../test/personalFixtures";
import type { PersonalLeagues } from "../../types/personal";

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
    }
  }

  return api;
}

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

const DATA = /Alpha League 2025 Rockets/;

describe("Season layout with data available", () => {
  it("warns about stale data and still shows it", async () => {
    mockPersonalApi(leaguesWith({ sync_state: "stale" }));
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findAllByText(DATA)).not.toHaveLength(0);
    expect(screen.getByRole("alert")).toHaveTextContent(/may be out of date/);
  });

  it.each(["queued", "syncing"] as const)("shows a polite refresh notice while %s", async (sync) => {
    mockPersonalApi(leaguesWith({ sync_state: sync }));
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findAllByText(DATA)).not.toHaveLength(0);
    const notice = screen
      .getAllByRole("status")
      .find((element) => /Refreshing League data/.test(element.textContent ?? ""));
    expect(notice).toBeDefined();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("warns when the latest refresh failed and still shows the data", async () => {
    mockPersonalApi(leaguesWith({ sync_state: "failed" }));
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findAllByText(DATA)).not.toHaveLength(0);
    expect(screen.getByRole("alert")).toHaveTextContent(/did not finish/);
  });

  it("shows no notice for fresh data", async () => {
    mockPersonalApi(leaguesWith({ sync_state: "fresh" }));
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findAllByText(DATA)).not.toHaveLength(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/out of date|Refreshing League data|did not finish/)).not.toBeInTheDocument();
  });
});

describe("Season layout without data", () => {
  it("prepares never-synced data without reading the snapshot", async () => {
    const api = mockPersonalApi(
      leaguesWith({ sync_state: "never_synced", data_available: false, last_successful_sync_at: null }),
    );
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findByRole("heading", { name: "Preparing your League data" })).toBeInTheDocument();
    expect(api.calls("GET /api/seasons/11/snapshot")).toHaveLength(0);
  });

  it("announces an import in progress in a status region", async () => {
    const api = mockPersonalApi(
      leaguesWith({ sync_state: "syncing", data_available: false, last_successful_sync_at: null }),
    );
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    const heading = await screen.findByRole("heading", { name: "Importing your League" });
    expect(heading.closest("[role=status]")).not.toBeNull();
    expect(api.calls("GET /api/seasons/11/snapshot")).toHaveLength(0);
  });

  it("offers a re-import link after a failed import", async () => {
    mockPersonalApi(
      leaguesWith({ sync_state: "failed", data_available: false, last_successful_sync_at: null }),
    );
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findByRole("link", { name: "Import this Season again" })).toHaveAttribute(
      "href",
      "/account/import",
    );
  });

  it("asks to reconnect when access needs reverification and reads no snapshot", async () => {
    const api = mockPersonalApi(
      leaguesWith({
        access_status: "reverification_required",
        data_available: false,
        last_successful_sync_at: null,
      }),
    );
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findByRole("heading", { name: "Reconnect this League" })).toBeInTheDocument();
    expect(api.calls("GET /api/seasons/11/snapshot")).toHaveLength(0);
  });
});

describe("default Season control", () => {
  beforeEach(() => {
    document.cookie = "fantasy_session_csrf=synthetic-session-csrf; path=/";
  });

  afterEach(() => {
    document.cookie = "fantasy_session_csrf=; path=/; max-age=0";
  });

  it("offers the action for a non-default Season", async () => {
    mockPersonalApi();
    renderWithProviders(<AppRouter />, "/account/seasons/10");

    expect(await screen.findByRole("button", { name: "Set as default Season" })).toBeEnabled();
    expect(screen.queryByText("Default Season")).not.toBeInTheDocument();
  });

  it("hides the action and shows the chip for the default Season", async () => {
    mockPersonalApi();
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findByText("Default Season")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set as default Season" })).not.toBeInTheDocument();
  });

  it("keeps the button and reports a failed save", async () => {
    const api = mockPersonalApi();
    api.reply("PUT /api/preferences/default-season", 500, { detail: "boom" });
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/seasons/10");

    await user.click(await screen.findByRole("button", { name: "Set as default Season" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The default Season could not be saved. Try again.",
    );
    expect(screen.getByRole("button", { name: "Set as default Season" })).toBeEnabled();
    expect(screen.queryByText("Default Season")).not.toBeInTheDocument();
  });

  it("disables the button and sets aria-busy while saving", async () => {
    const api = mockPersonalApi();
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    api.on("PUT /api/preferences/default-season", async () => {
      await gate;

      return jsonResponse(200, { default_season_id: 10 });
    });
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/seasons/10");

    await user.click(await screen.findByRole("button", { name: "Set as default Season" }));

    const busy = await screen.findByRole("button", { name: "Saving…" });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
    expect(api.calls("PUT /api/preferences/default-season")).toHaveLength(1);

    await act(async () => {
      release?.();
    });
    await waitFor(() => {
      expect(screen.getByText("Default Season")).toBeInTheDocument();
    });
  });
});

describe("Season status errors", () => {
  it("separates a server failure from the not-available state and can retry", async () => {
    const api = mockPersonalApi();
    api.reply("GET /api/seasons/11", 500, { detail: "boom" });
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    expect(await screen.findByRole("heading", { name: "Unable to load this Season" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Season not available" })).not.toBeInTheDocument();

    const leagues = twoLeagues();
    api.reply("GET /api/seasons/11", 200, seasonDetail(leagues, 11));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findAllByText(DATA)).not.toHaveLength(0);
  });

  it("shows the not-available state for a 404", async () => {
    const api = mockPersonalApi();
    api.reply("GET /api/seasons/11", 404, { detail: "League season not found." });
    renderWithProviders(<AppRouter />, "/account/seasons/11");

    const heading = await screen.findByRole("heading", { name: "Season not available" });
    expect(within(heading.closest("section") as HTMLElement).queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Unable to load this Season" })).not.toBeInTheDocument();
  });
});
