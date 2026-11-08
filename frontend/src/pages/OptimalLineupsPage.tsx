import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  RefreshCw,
} from "lucide-react";
import { Link } from "react-router-dom";

import {
  optimalLineupsQueryOptions,
} from "../api/analytics";
import { ApiError } from "../api/client";
import {
  currentLeagueQueryOptions,
} from "../api/league";
import "./OptimalLineupsPage.css";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to load optimal-lineup analysis.";
}

function efficiencyClass(
  efficiency: number,
): string {
  if (efficiency >= 90) {
    return "efficiency-value efficiency-elite";
  }

  if (efficiency >= 85) {
    return "efficiency-value efficiency-strong";
  }

  return "efficiency-value efficiency-improvable";
}

export function OptimalLineupsPage() {

  const leagueQuery = useQuery(
    currentLeagueQueryOptions(),
  );

  const lineupsQuery = useQuery(
    optimalLineupsQueryOptions(),
  );

  const mostMissedPoints = useMemo(() => {
    if (!lineupsQuery.data?.length) {
      return null;
    }

    return [...lineupsQuery.data].sort(
      (first, second) =>
        second.missed_points -
          first.missed_points ||
        first.team_name.localeCompare(
          second.team_name,
        ),
    )[0];
  }, [lineupsQuery.data]);

  if (
    leagueQuery.isPending ||
    lineupsQuery.isPending
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

        <h1>Calculating optimal lineups</h1>

        <p>
          Comparing actual starters with the
          highest-scoring legal lineup from each
          weekly roster.
        </p>
      </section>
    );
  }

  if (
    leagueQuery.isError ||
    lineupsQuery.isError
  ) {
    const currentError =
      leagueQuery.error ??
      lineupsQuery.error;

    return (
      <section
        className="app-state"
        aria-live="assertive"
      >
        <h1>
          Unable to load optimal-lineup analysis
        </h1>

        <p>{errorMessage(currentError)}</p>

        <button
          type="button"
          onClick={() => {
            void Promise.all([
              leagueQuery.refetch(),
              lineupsQuery.refetch(),
            ]);
          }}
        >
          Try again
        </button>
      </section>
    );
  }

  const league = leagueQuery.data;
  const summaries = lineupsQuery.data;

  const leader = summaries[0];

  return (
    <div className="optimal-lineups-page">
      <header className="optimal-lineups-header">
        <p className="page-kicker">
          Manager decision analysis
        </p>

        <h1>Optimal Lineups</h1>

        <p>
          Optimal Lineups compares the players each
          manager started with the highest-scoring
          legal lineup available from that
          manager&apos;s actual weekly roster. A
          higher efficiency percentage means fewer
          points were left on the bench.
        </p>
      </header>

      <section
        className="optimal-lineups-summary"
        aria-label="Optimal-lineup summary"
      >
        <article>
          <span>Efficiency leader</span>

          <strong>
            {leader?.team_name ?? "Unavailable"}
          </strong>

          <small>
            {leader
              ? `${leader.efficiency_percentage.toFixed(
                  2,
                )}% efficient`
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
        <span>Teams analyzed</span>

        <strong>{summaries.length}</strong>

        <small>
          Every available weekly roster is included
        </small>
      </article>

        <article>
          <span>Most missed points</span>

          <strong>
            {mostMissedPoints?.team_name ??
              "Unavailable"}
          </strong>

          <small>
            {mostMissedPoints
              ? `${mostMissedPoints.missed_points.toFixed(
                  2,
                )} points`
              : "No completed results"}
          </small>
        </article>
      </section>

      <section
        className="optimal-lineups-panel"
        aria-labelledby="efficiency-heading"
      >
        <div className="optimal-lineups-panel-heading">
          <div>
            <p className="panel-kicker">
              Season manager ranking
            </p>

            <h2 id="efficiency-heading">
              Lineup efficiency
            </h2>
          </div>

          <span>{league.season} season</span>
        </div>

        <div className="optimal-lineups-columns">
          <span>Rank</span>
          <span>Team</span>
          <span>Actual points</span>
          <span>Optimal points</span>
          <span>Missed points</span>
          <span>Efficiency</span>
          <span aria-hidden="true" />
        </div>

        <div className="optimal-lineups-ranking">
          {summaries.map(
            (summary, index) => {
              const rank = index + 1;

              return (
                <article
                key={summary.team_id}
                className="optimal-lineups-row"
              >
                  <div className="optimal-rank">
                    <strong>{rank}</strong>
                  </div>

                  <div className="optimal-team">
                  <strong>
                    {summary.team_name}
                  </strong>

                  <small>
                    {summary.efficiency_percentage.toFixed(2)}%
                    efficient
                  </small>
                </div>

                  <div className="optimal-stat">
                    <span className="mobile-field-label">
                      Actual
                    </span>

                    <strong>
                      {summary.actual_points.toFixed(
                        2,
                      )}
                    </strong>
                  </div>

                  <div className="optimal-stat">
                    <span className="mobile-field-label">
                      Optimal
                    </span>

                    <strong>
                      {summary.optimal_points.toFixed(
                        2,
                      )}
                    </strong>
                  </div>

                  <div className="optimal-stat optimal-missed">
                    <span className="mobile-field-label">
                      Missed
                    </span>

                    <strong>
                      {summary.missed_points.toFixed(
                        2,
                      )}
                    </strong>
                  </div>

                  <div className="optimal-efficiency">
                    <span className="mobile-field-label">
                      Efficiency
                    </span>

                    <strong
                      className={efficiencyClass(
                        summary.efficiency_percentage,
                      )}
                    >
                      {summary.efficiency_percentage.toFixed(
                        2,
                      )}
                      %
                    </strong>

                    <div
                      className="efficiency-track"
                      role="progressbar"
                      aria-label={`${summary.team_name} lineup efficiency`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={
                        summary.efficiency_percentage
                      }
                    >
                      <span
                        style={{
                          width: `${Math.min(
                            Math.max(
                              summary.efficiency_percentage,
                              0,
                            ),
                            100,
                          )}%`,
                        }}
                      />
                    </div>
                  </div>

                  <Link
                    className="optimal-detail-link"
                    to={`/optimal-lineups/${summary.team_id}`}
                    aria-label={`View weekly lineup details for ${summary.team_name}`}
                  >
                    <span>View details</span>
                    <ArrowRight aria-hidden="true" />
                  </Link>
                </article>
              );
            },
          )}
        </div>
      </section>

      <section className="optimal-lineups-explanation">
        <h2>What lineup efficiency measures</h2>

        <p>
          Efficiency divides actual starter points
          by the best legal lineup available from the
          players rostered that week. Injured-reserve
          players are excluded, players are never
          added from waivers, and an unavailable
          position remains unfilled. The analysis
          measures lineup decisions, not roster
          construction.
        </p>
      </section>
    </div>
  );
}
