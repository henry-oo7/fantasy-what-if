import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  RefreshCw,
  Trophy,
} from "lucide-react";

import {
  leagueAverageQueryOptions,
} from "../api/analytics";
import { ApiError } from "../api/client";
import {
  currentLeagueQueryOptions,
} from "../api/league";
import type {
  AnalyticsRecord,
} from "../types/analytics";
import "./LeagueAveragePage.css";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to load league-average standings.";
}

function recordLabel(
  record: AnalyticsRecord,
): string {
  return `${record.wins}-${record.losses}-${record.ties}`;
}

export function LeagueAveragePage() {

  const leagueQuery = useQuery(
    currentLeagueQueryOptions(),
  );

  const standingsQuery = useQuery(
    leagueAverageQueryOptions(),
  );

  const latestLeagueAverage = useMemo(() => {
    const latestWeeklyResult =
      standingsQuery.data
        ?.flatMap(
          (standing) =>
            standing.weekly_results,
        )
        .sort(
          (first, second) =>
            second.week - first.week,
        )[0];

    if (latestWeeklyResult === undefined) {
      return null;
    }

    return {
      week: latestWeeklyResult.week,
      average:
        latestWeeklyResult.league_average,
    };
  }, [standingsQuery.data]);

  const seasonLeagueAverage = useMemo(() => {
    const weeklyResults =
      standingsQuery.data?.[0]?.weekly_results ??
      [];

    if (weeklyResults.length === 0) {
      return null;
    }

    const total = weeklyResults.reduce(
      (sum, weeklyResult) =>
        sum + weeklyResult.league_average,
      0,
    );

    return total / weeklyResults.length;
  }, [standingsQuery.data]);

  if (
    leagueQuery.isPending ||
    standingsQuery.isPending
  ) {
    return (
      <section
        className="app-state"
        aria-live="polite"
      >
        <RefreshCw
          className="spinner"
          aria-hidden="true"
        />

        <h1>Calculating league-average records</h1>

        <p>
          Comparing each weekly score against the
          full league average.
        </p>
      </section>
    );
  }

  if (
    leagueQuery.isError ||
    standingsQuery.isError
  ) {
    const currentError =
      leagueQuery.error ??
      standingsQuery.error;

    return (
      <section
        className="app-state"
        aria-live="assertive"
      >
        <h1>
          Unable to load league-average standings
        </h1>

        <p>{errorMessage(currentError)}</p>

        <button
          type="button"
          onClick={() => {
            void Promise.all([
              leagueQuery.refetch(),
              standingsQuery.refetch(),
            ]);
          }}
        >
          Try again
        </button>
      </section>
    );
  }

  const league = leagueQuery.data;
  const standings = standingsQuery.data;

  return (
    <div className="league-average-page">
      <header className="league-average-header">
        <p className="page-kicker">
          Weekly scoring benchmark
        </p>

        <h1>League Average</h1>

        <p>
          Each team keeps its scheduled result and
          also earns a second weekly decision against
          the league&apos;s average score. This
          rewards teams that consistently outperform
          the weekly field, even when the scheduled
          matchup goes against them.
        </p>
      </header>

      <section
        className="league-average-summary"
        aria-label="League-average summary"
      >
        <article>
          <span>Current leader</span>
          <strong>
            {standings[0]?.team_name ??
              "Unavailable"}
          </strong>
          <small>
            {standings[0]
              ? recordLabel(
                  standings[0].combined_record,
                )
              : "No completed results"}
          </small>
        </article>

        <article>
          <span>Current week</span>
          <strong>
            Week {league.current_week}
          </strong>
          <small>
            Through week{" "}
            {Math.max(league.current_week - 1, 0)}{" "}
            results included
          </small>
        </article>

        <article>
          <span>Latest league average</span>

          <strong>
            {latestLeagueAverage
              ? `${latestLeagueAverage.average.toFixed(
                  2,
                )} points`
              : "Unavailable"}
          </strong>

          <small>
            {latestLeagueAverage
              ? `Week ${latestLeagueAverage.week}`
              : "No completed weekly results"}
          </small>
        </article>

        <article>
          <span>Season scoring average</span>

          <strong>
            {seasonLeagueAverage !== null
              ? `${seasonLeagueAverage.toFixed(
                  2,
                )} points`
              : "Unavailable"}
          </strong>

          <small>
            Average league score across all completed
            weeks
          </small>
        </article>
      </section>

      <section
        className="league-average-panel"
        aria-labelledby="league-average-heading"
      >
        <div className="league-average-panel-heading">
          <div>
            <p className="panel-kicker">
              Combined weekly record
            </p>

            <h2 id="league-average-heading">
              Adjusted standings
            </h2>
          </div>

          <span>{league.season} season</span>
        </div>

        <div className="league-average-columns">
          <span>Rank</span>
          <span>Team</span>
          <span>Actual</span>
          <span>Vs. average</span>
          <span>Combined</span>
          <span>Weekly average</span>
        </div>

        <div className="league-average-standings">
          {standings.map((standing) => {

            return (
              <article
              key={standing.team_id}
              className="league-average-row"
            >
                <div className="league-average-rank">
                  {standing.rank === 1 ? (
                    <Trophy aria-hidden="true" />
                  ) : null}

                  <strong>{standing.rank}</strong>
                </div>

                <div className="league-average-team">
                  <strong>
                    {standing.team_name}
                  </strong>

                  <small>
                  {standing.points_for.toFixed(1)} total points
                </small>
                </div>

                <div className="league-average-record league-average-actual">
                  <span className="mobile-field-label">
                    Actual
                  </span>

                  <strong>
                    {recordLabel(
                      standing.actual_record,
                    )}
                  </strong>
                </div>

                <div className="league-average-record league-average-versus-average">
                  <span className="mobile-field-label">
                    Vs. average
                  </span>

                  <strong>
                    {recordLabel(
                      standing.average_record,
                    )}
                  </strong>
                </div>

                <div className="league-average-record league-average-combined">
                  <span className="mobile-field-label">
                    Combined
                  </span>

                  <strong>
                    {recordLabel(
                      standing.combined_record,
                    )}
                  </strong>
                </div>

                <div className="league-average-points">
                  <span className="mobile-field-label">
                    Weekly average
                  </span>

                  <strong>
                    {standing.average_points_per_week.toFixed(
                      2,
                    )}
                  </strong>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="league-average-explanation">
        <h2>How the combined record works</h2>

        <p>
          Each completed week counts twice. The first
          decision comes from the scheduled matchup.
          The second comes from scoring above or below
          the league average. A team therefore has two
          possible wins, losses, or ties per week.
        </p>
      </section>
    </div>
  );
}
