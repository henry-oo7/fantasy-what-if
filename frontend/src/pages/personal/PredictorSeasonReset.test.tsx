import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { AppRouter } from "../../router";
import { renderWithProviders } from "../../test/accountHarness";
import { mockPersonalApi } from "../../test/personalApi";
import type { PlayoffPredictorSnapshot } from "../../types/league";

/** One open week-3 matchup; team names carry the Season so any leak is visible. */
function predictor(seasonId: number, year: number): PlayoffPredictorSnapshot {
  const team = (id: number, name: string) => ({
    team_id: id, name: `${year} ${name}`, wins: 1, losses: 1, ties: 0, points_for: 200, points_against: 200,
  });

  return {
    league_id: 1,
    league_name: "Alpha League",
    season_id: seasonId,
    season: year,
    current_week: 3,
    regular_season_weeks: 3,
    playoff_team_count: 1,
    playoff_seed_tie_rule: "head_to_head",
    playoff_seed_tie_rule_source: "provider",
    allows_ties: false,
    teams: [team(1, "Rockets"), team(2, "Comets")],
    completed_matchups: [],
    remaining_matchups: [
      { matchup_id: seasonId * 100, week: 3, home_team_id: 1, away_team_id: 2, home_score: null, away_score: null, is_complete: false },
    ],
  };
}

function setup() {
  const api = mockPersonalApi();
  api.reply("GET /api/seasons/11/playoff-predictor", 200, predictor(11, 2025));
  api.reply("GET /api/seasons/10/playoff-predictor", 200, predictor(10, 2024));
  document.cookie = "fantasy_session_csrf=synthetic-session-csrf; path=/";

  return api;
}

afterEach(() => {
  document.cookie = "fantasy_session_csrf=; path=/; max-age=0";
});

function Back() {
  const navigate = useNavigate();

  return (
    <button type="button" onClick={() => void navigate(-1)}>
      history back
    </button>
  );
}

const picks = () => screen.getByText(/predictions? selected/);

async function pick(user: ReturnType<typeof userEvent.setup>, team: string) {
  await user.click(await screen.findByRole("button", { name: new RegExp(team) }));
}

async function switchTo(user: ReturnType<typeof userEvent.setup>, year: string) {
  const link = within(screen.getByRole("navigation", { name: "Leagues and Seasons" })).getAllByRole("link", {
    name: new RegExp(year),
  })[0];

  await user.click(link);
}

describe("Playoff Predictor across Seasons", () => {
  it("never carries local picks to another Season, in either direction", async () => {
    setup();
    const user = userEvent.setup();

    renderWithProviders(
      <>
        <AppRouter />
        <Back />
      </>,
      "/account/seasons/11/playoff-predictor",
    );

    // Season A: make a pick.
    await pick(user, "2025 Comets");
    expect(picks()).toHaveTextContent("1 predictions selected");

    // Season B: its own teams, no picks from A.
    await switchTo(user, "2024");
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/10/playoff-predictor");
    });
    expect(await screen.findByRole("button", { name: /2024 Rockets/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /2025/ })).not.toBeInTheDocument();
    expect(picks()).toHaveTextContent("0 predictions selected");

    // Make a different pick in B.
    await pick(user, "2024 Comets");
    expect(picks()).toHaveTextContent("1 predictions selected");

    // Back to A: rebuilt from A's own data, with neither A's old pick nor B's.
    await user.click(screen.getByRole("button", { name: "history back" }));
    expect(await screen.findByRole("button", { name: /2025 Rockets/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /2024/ })).not.toBeInTheDocument();
    expect(picks()).toHaveTextContent("0 predictions selected");
    expect(screen.getByRole("button", { name: /2025 Comets/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("gives the next session no predictor state after logout", async () => {
    const api = setup();
    api.reply("POST /api/auth/logout", 200, { logged_out: true });
    const user = userEvent.setup();

    const { queryClient, unmount } = renderWithProviders(<AppRouter />, "/account/seasons/11/playoff-predictor");
    await pick(user, "2025 Comets");
    expect(picks()).toHaveTextContent("1 predictions selected");

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/login");
    });
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(screen.queryByText(/predictions? selected/)).not.toBeInTheDocument();
    unmount();

    // A new session for the same Season starts with no picks.
    const second = renderWithProviders(<AppRouter />, "/account/seasons/11/playoff-predictor");
    expect(await screen.findByRole("button", { name: /2025 Rockets/ })).toBeInTheDocument();
    expect(picks()).toHaveTextContent("0 predictions selected");
    second.unmount();
  });

  it("drops predictor state when the session expires", async () => {
    const api = setup();
    const user = userEvent.setup();

    const { queryClient } = renderWithProviders(<AppRouter />, "/account/seasons/11/playoff-predictor");
    await pick(user, "2025 Comets");
    expect(picks()).toHaveTextContent("1 predictions selected");

    // The next protected read reports an expired session.
    api.reply("GET /api/seasons/11/playoff-predictor", 401, { detail: "Authentication is required." });
    await queryClient.refetchQueries({ queryKey: ["personal", "season", 11] });

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/login");
    });
    expect(screen.queryByText(/predictions? selected/)).not.toBeInTheDocument();
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });
});
