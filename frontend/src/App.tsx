import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CalendarDays,
  CircleDot,
  RefreshCw,
  Shield,
  Trophy,
} from "lucide-react";

import { leagueAverageQueryOptions } from "./api/analytics";
import { ApiError } from "./api/client";
import {
  currentLeagueQueryOptions,
  importedSeasonsQueryOptions,
} from "./api/league";
import { useAuth } from "./auth/AuthProvider";
import { seasonPresentation } from "./utils/seasonPresentation";
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
  const leagueAverageQuery = useQuery(leagueAverageQueryOptions());
  const importedSeasonsQuery = useQuery(importedSeasonsQueryOptions());

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

  const teamNames = new Map(
    league.teams.map((team) => [team.team_id, team.name]),
  );

  const latestCompletedWeek = league.matchups.reduce(
    (latestWeek, matchup) => Math.max(latestWeek, matchup.week),
    0,
  );

  const latestMatchups = league.matchups.filter(
    (matchup) => matchup.week === latestCompletedWeek,
  );

  const viewedSeason = importedSeasonsQuery.data?.seasons.find(
    (season) => season.year === league.season,
  );

  const seasonView = seasonPresentation(league, viewedSeason);

  const lastSyncedLabel = (() => {
    if (!viewedSeason?.last_synced_at) {
      if (importedSeasonsQuery.isPending) {
        return "Checking last sync";
      }

      return "Last sync unavailable";
    }

    const timestamp = new Date(viewedSeason.last_synced_at);

    if (Number.isNaN(timestamp.getTime())) {
      return "Last sync unavailable";
    }

    return `Last synced ${new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(timestamp)}`;
  })();

  const latestLeagueAverage = (() => {
    const weeklyResults = leagueAverageQuery.data?.[0]?.weekly_results ?? [];

    const latestResult = weeklyResults.reduce<
      (typeof weeklyResults)[number] | null
    >(
      (latest, result) =>
        latest === null || result.week > latest.week ? result : latest,
      null,
    );

    if (latestResult === null) {
      return null;
    }

    return {
      week: latestResult.week,
      average: latestResult.league_average,
    };
  })();

  return (
    <div className="dashboard-page">
      <header className="dashboard-header">
        <div>
          <p className="page-kicker">{seasonView.pageKicker}</p>

          <h1>Welcome back, {auth.user?.display_name.split(" ")[0]}</h1>

          <p>
            Here is the latest from <strong>{league.name}</strong>.
          </p>
        </div>

        <div className="season-status">
          <span className="live-indicator">
            <CircleDot aria-hidden="true" />
            {seasonView.statusLabel}
          </span>

          <strong>{league.season}</strong>
        </div>
      </header>

      <section className="scoreboard-hero" aria-labelledby="scoreboard-heading">
        <div className="scoreboard-heading">
          <div>
            <p className="team-label">Latest completed results</p>

            <h2 id="scoreboard-heading">
              Week {latestCompletedWeek} scoreboard
            </h2>
          </div>

          <span className="scoreboard-status">Final</span>
        </div>

        {latestMatchups.length > 0 ? (
          <div className="scoreboard-grid">
            {latestMatchups.map((matchup) => {
              const homeWon = matchup.home_score > matchup.away_score;
              const awayWon = matchup.away_score > matchup.home_score;

              return (
                <article
                  key={`${matchup.week}-${matchup.home_team_id}-${matchup.away_team_id}`}
                  className="scoreboard-matchup"
                >
                  <div
                    className={
                      homeWon
                        ? "scoreboard-team scoreboard-winner"
                        : "scoreboard-team"
                    }
                  >
                    <span>
                      {teamNames.get(matchup.home_team_id) ?? "Unknown team"}
                    </span>

                    <strong>{matchup.home_score.toFixed(2)}</strong>
                  </div>

                  <div
                    className={
                      awayWon
                        ? "scoreboard-team scoreboard-winner"
                        : "scoreboard-team"
                    }
                  >
                    <span>
                      {teamNames.get(matchup.away_team_id) ?? "Unknown team"}
                    </span>

                    <strong>{matchup.away_score.toFixed(2)}</strong>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <p className="scoreboard-empty">
            No completed matchup results are available yet.
          </p>
        )}

        <div className="scoreboard-footer">
          <span>
            {seasonView.completedWeeks} of {league.regular_season_weeks}{" "}
            regular-season weeks complete
          </span>

          <strong>{lastSyncedLabel}</strong>
        </div>
      </section>

      <section className="dashboard-summary-grid" aria-label="League summary">
        <article className="summary-card">
          <span className="summary-icon">
            <CalendarDays aria-hidden="true" />
          </span>

          <div>
            <p>{seasonView.periodLabel}</p>
            <strong>{seasonView.periodValue}</strong>
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
            <p>Latest weekly average</p>

            <strong>
              {latestLeagueAverage
                ? `${latestLeagueAverage.average.toFixed(2)} points`
                : "Unavailable"}
            </strong>

            <small className="summary-detail">
              {latestLeagueAverage
                ? `Week ${latestLeagueAverage.week}`
                : leagueAverageQuery.isError
                  ? "League-average data unavailable"
                  : "No completed results"}
            </small>
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

          <span>
            {seasonView.isActive
              ? `Through week ${seasonView.completedWeeks}`
              : seasonView.statusLabel}
          </span>
        </div>

        <div className="standings-list">
          {sortedStandings.map((team, index) => (
            <div key={team.team_id} className="standing-row">
              <span className="standing-rank">{index + 1}</span>

              <div className="standing-team">
                <strong>{team.name}</strong>
              </div>

              <div
                className="standing-scoring"
                aria-label={`${team.points_for.toFixed(
                  1,
                )} points for and ${team.points_against.toFixed(
                  1,
                )} points against`}
              >
                <span>
                  <small>PF</small>
                  <strong>{team.points_for.toFixed(1)}</strong>
                </span>

                <span>
                  <small>PA</small>
                  <strong>{team.points_against.toFixed(1)}</strong>
                </span>
              </div>

              <strong className="standing-record">
                {team.wins}-{team.losses}
                {team.ties > 0 ? `-${team.ties}` : ""}
              </strong>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
