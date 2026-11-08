import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CalendarDays,
  CircleDot,
  RefreshCw,
  Shield,
  Trophy,
} from "lucide-react";

import { currentLeagueQueryOptions } from "./api/league";
import { ApiError } from "./api/client";
import { useAuth } from "./auth/AuthProvider";
import "./App.css";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to load the league dashboard.";
}

export default function App() {
  const auth = useAuth();
  const leagueQuery = useQuery(currentLeagueQueryOptions());

  const sortedStandings = useMemo(
    () =>
      [...(leagueQuery.data?.teams ?? [])].sort(
        (first, second) =>
          second.wins - first.wins ||
          first.losses - second.losses ||
          second.points_for - first.points_for,
      ),
    [leagueQuery.data?.teams],
  );

  if (leagueQuery.isPending) {
    return (
      <section className="app-state" aria-live="polite">
        <RefreshCw className="spinner" aria-hidden="true" />
        <h1>Loading your league</h1>
        <p>Preparing current standings and team information.</p>
      </section>
    );
  }

  if (leagueQuery.isError) {
    return (
      <section className="app-state" aria-live="assertive">
        <h1>Unable to load the dashboard</h1>
        <p>{errorMessage(leagueQuery.error)}</p>

        <button
          type="button"
          onClick={() => {
            void leagueQuery.refetch();
          }}
        >
          Try again
        </button>
      </section>
    );
  }

  const league = leagueQuery.data;

  const completedWeeks = Math.max(
    Math.min(league.current_week - 1, league.regular_season_weeks),
    0,
  );

  const leagueLeader = sortedStandings[0];

  return (
    <div className="dashboard-page">
      <header className="dashboard-header">
        <div>
          <p className="page-kicker">Week {league.current_week}</p>

          <h1>Welcome back, {auth.user?.display_name.split(" ")[0]}</h1>

          <p>
            Here is the latest from <strong>{league.name}</strong>.
          </p>
        </div>

        <div className="season-status">
          <span className="live-indicator">
            <CircleDot aria-hidden="true" />
            Season active
          </span>

          <strong>{league.season}</strong>
        </div>
      </header>

      <section
        className="team-hero"
        aria-labelledby="league-leader-heading"
      >
        <div className="team-hero-main">
          <p className="team-label">
            Current league leader
          </p>

          <h2 id="league-leader-heading">
            {leagueLeader?.name ?? "Unavailable"}
          </h2>

          <div className="team-record">
            <strong>
              {leagueLeader
                ? `${leagueLeader.wins}-${leagueLeader.losses}${
                    leagueLeader.ties > 0
                      ? `-${leagueLeader.ties}`
                      : ""
                  }`
                : "N/A"}
            </strong>

            <span>Current record</span>
          </div>
        </div>

        <div className="team-stat-grid">
          <div className="team-stat">
            <span>League rank</span>
            <strong>
              {leagueLeader ? "#1" : "N/A"}
            </strong>
          </div>

          <div className="team-stat">
            <span>Points for</span>
            <strong>
              {leagueLeader
                ? leagueLeader.points_for.toFixed(2)
                : "N/A"}
            </strong>
          </div>

          <div className="team-stat">
            <span>Points against</span>
            <strong>
              {leagueLeader
                ? leagueLeader.points_against.toFixed(2)
                : "N/A"}
            </strong>
          </div>
        </div>
      </section>

      <section className="dashboard-summary-grid" aria-label="League summary">
        <article className="summary-card">
          <span className="summary-icon">
            <CalendarDays aria-hidden="true" />
          </span>

          <div>
            <p>Completed weeks</p>
            <strong>
              {completedWeeks} of {league.regular_season_weeks}
            </strong>
          </div>
        </article>

        <article className="summary-card">
          <span className="summary-icon">
            <Shield aria-hidden="true" />
          </span>

          <div>
            <p>League teams</p>
            <strong>{league.teams.length}</strong>
          </div>
        </article>

        <article className="summary-card">
          <span className="summary-icon">
            <Trophy aria-hidden="true" />
          </span>

          <div>
            <p>Current leader</p>
            <strong>{leagueLeader?.name ?? "Unavailable"}</strong>
          </div>
        </article>
      </section>

      <section
        className="dashboard-panel standings-panel"
        aria-labelledby="standings-heading"
      >
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">Current results</p>
            <h2 id="standings-heading">League standings</h2>
          </div>

          <span>Through week {completedWeeks}</span>
        </div>

        <div className="standings-list">
          {sortedStandings.map((team, index) => (
          <div
            key={team.team_id}
            className="standing-row"
          >
            <span className="standing-rank">
              {index + 1}
            </span>

            <div className="standing-team">
              <strong>{team.name}</strong>

              <small>
                {team.points_against.toFixed(1)} PA
              </small>
            </div>

            <span className="standing-points">
              {team.points_for.toFixed(1)} PF
            </span>

            <strong className="standing-record">
              {team.wins}-{team.losses}
              {team.ties > 0
                ? `-${team.ties}`
                : ""}
            </strong>
          </div>
        ))}
        </div>
      </section>
    </div>
  );
}
