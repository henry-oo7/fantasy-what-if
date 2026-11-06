import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  GitCompareArrows,
  RefreshCw,
  Trophy,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  scheduleSwapQueryOptions,
} from "../api/analytics";
import {
  currentLeagueQueryOptions,
} from "../api/league";
import { ApiError } from "../api/client";
import { AppNavigation } from "../components/AppNavigation";
import { AuthenticatedUserMenu } from "../components/AuthenticatedUserMenu";
import type {
  MatchupResult,
  TeamScheduleSwapResult,
} from "../types/analytics";
import "./ScheduleSwapPage.css";

function parseTeamId(
  value: string | null,
): number {
  if (value === null) {
    return 0;
  }

  const parsed = Number(value);

  return Number.isInteger(parsed) && parsed > 0
    ? parsed
    : 0;
}

function recordLabel(
  record: {
    wins: number;
    losses: number;
    ties: number;
  },
): string {
  return `${record.wins}-${record.losses}-${record.ties}`;
}

function resultLabel(
  result: MatchupResult,
): string {
  if (result === "win") {
    return "W";
  }

  if (result === "loss") {
    return "L";
  }

  return "T";
}

function errorMessage(
  error: unknown,
): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to calculate the schedule swap.";
}

function TeamSwapSummary({
  result,
}: {
  result: TeamScheduleSwapResult;
}) {
  const change = result.equivalent_wins_change;
  const changeLabel =
    change > 0
      ? `+${change.toFixed(1)}`
      : change.toFixed(1);

  return (
    <article className="swap-summary-card">
      <div className="swap-summary-heading">
        <div>
          <span>Team</span>
          <h2>{result.team_name}</h2>
        </div>

        <div
          className={
            change > 0
              ? "wins-change wins-change-positive"
              : change < 0
                ? "wins-change wins-change-negative"
                : "wins-change"
          }
        >
          {changeLabel} equivalent wins
        </div>
      </div>

      <p className="borrowed-schedule">
        Using {result.borrowed_schedule_team_name}
        &apos;s opponents
      </p>

      <div className="record-comparison">
        <div>
          <span>Original record</span>
          <strong>
            {recordLabel(result.original_record)}
          </strong>
          <small>
            {(
              result.original_record.win_percentage *
              100
            ).toFixed(1)}
            % win rate
          </small>
        </div>

        <ArrowRight aria-hidden="true" />

        <div>
          <span>Swapped record</span>
          <strong>
            {recordLabel(result.swapped_record)}
          </strong>
          <small>
            {(
              result.swapped_record.win_percentage *
              100
            ).toFixed(1)}
            % win rate
          </small>
        </div>
      </div>

      <div className="weekly-results-table-wrapper">
        <table className="weekly-results-table">
          <thead>
            <tr>
              <th scope="col">Week</th>
              <th scope="col">Opponent</th>
              <th scope="col">Score</th>
              <th scope="col">Result</th>
            </tr>
          </thead>

          <tbody>
            {result.weekly_results.map(
              (week) => (
                <tr key={week.week}>
                  <td>{week.week}</td>
                  <td>
                    {
                      week.hypothetical_opponent_name
                    }
                  </td>
                  <td>
                    {week.team_score.toFixed(2)}
                    <span className="score-divider">
                      {" "}
                      vs{" "}
                    </span>
                    {week.opponent_score.toFixed(2)}
                  </td>
                  <td>
                    <span
                      className={`result-badge result-${week.result}`}
                      aria-label={
                        week.result
                      }
                    >
                      {resultLabel(week.result)}
                    </span>
                  </td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </article>
  );
}

export function ScheduleSwapPage() {
  const [searchParams, setSearchParams] =
    useSearchParams();

  const firstTeamId = parseTeamId(
    searchParams.get("first"),
  );
  const secondTeamId = parseTeamId(
    searchParams.get("second"),
  );

  const leagueQuery = useQuery(
    currentLeagueQueryOptions(),
  );

  const swapQuery = useQuery(
    scheduleSwapQueryOptions(
      firstTeamId,
      secondTeamId,
    ),
  );

  const selectedTeamsValid =
    firstTeamId > 0 &&
    secondTeamId > 0 &&
    firstTeamId !== secondTeamId;

  const teamOptions = useMemo(
    () =>
      [...(leagueQuery.data?.teams ?? [])].sort(
        (first, second) =>
          first.name.localeCompare(second.name),
      ),
    [leagueQuery.data?.teams],
  );

  function updateSelection(
    key: "first" | "second",
    value: string,
  ) {
    const updated = new URLSearchParams(
      searchParams,
    );

    if (value) {
      updated.set(key, value);
    } else {
      updated.delete(key);
    }

    setSearchParams(updated, {
      replace: true,
    });
  }

  if (leagueQuery.isPending) {
    return (
      <main
        className="app-state"
        aria-live="polite"
      >
        <RefreshCw
          className="spinner"
          aria-hidden="true"
        />
        <h1>Loading league teams</h1>
        <p>
          Preparing the schedule comparison.
        </p>
      </main>
    );
  }

  if (leagueQuery.isError) {
    return (
      <main
        className="app-state"
        aria-live="assertive"
      >
        <h1>Unable to load the league</h1>
        <p>
          {errorMessage(leagueQuery.error)}
        </p>
        <button
          type="button"
          onClick={() => {
            void leagueQuery.refetch();
          }}
        >
          Try again
        </button>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">
            Alternate schedule analysis
          </p>

          <h1>Schedule Swap</h1>

          <p className="header-copy">
            Keep each team&apos;s weekly scores,
            exchange opponents, and see how much
            schedule luck changed the standings.
          </p>
        </div>

        <div className="header-actions">
          <AppNavigation />
          <AuthenticatedUserMenu />
        </div>
      </header>

      <main className="dashboard">
        <section
          className="content-card swap-controls"
          aria-labelledby="swap-controls-heading"
        >
          <div>
            <p className="section-label">
              Choose two teams
            </p>
            <h2 id="swap-controls-heading">
              Exchange complete schedules
            </h2>
            <p>
              Head-to-head games between the two
              selected teams remain valid and are
              not converted into self-matchups.
            </p>
          </div>

          <div className="team-selectors">
            <div className="form-field">
              <label htmlFor="first-team">
                First team
              </label>
              <select
                id="first-team"
                value={
                  firstTeamId > 0
                    ? String(firstTeamId)
                    : ""
                }
                onChange={(event) => {
                  updateSelection(
                    "first",
                    event.target.value,
                  );
                }}
              >
                <option value="">
                  Select a team
                </option>

                {teamOptions.map((team) => (
                  <option
                    key={team.team_id}
                    value={team.team_id}
                    disabled={
                      team.team_id === secondTeamId
                    }
                  >
                    {team.name}
                  </option>
                ))}
              </select>
            </div>

            <div
              className="swap-direction"
              aria-hidden="true"
            >
              <GitCompareArrows />
            </div>

            <div className="form-field">
              <label htmlFor="second-team">
                Second team
              </label>
              <select
                id="second-team"
                value={
                  secondTeamId > 0
                    ? String(secondTeamId)
                    : ""
                }
                onChange={(event) => {
                  updateSelection(
                    "second",
                    event.target.value,
                  );
                }}
              >
                <option value="">
                  Select a team
                </option>

                {teamOptions.map((team) => (
                  <option
                    key={team.team_id}
                    value={team.team_id}
                    disabled={
                      team.team_id === firstTeamId
                    }
                  >
                    {team.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {firstTeamId === secondTeamId &&
          firstTeamId > 0 ? (
            <p
              className="selection-error"
              role="alert"
            >
              Select two different teams.
            </p>
          ) : null}
        </section>

        {!selectedTeamsValid ? (
          <section className="content-card swap-empty-state">
            <GitCompareArrows aria-hidden="true" />
            <h2>Select two different teams</h2>
            <p>
              The completed weekly schedules will
              be exchanged automatically.
            </p>
          </section>
        ) : null}

        {selectedTeamsValid &&
        swapQuery.isPending ? (
          <section
            className="content-card swap-empty-state"
            aria-live="polite"
          >
            <RefreshCw
              className="spinner"
              aria-hidden="true"
            />
            <h2>Calculating swapped schedules</h2>
          </section>
        ) : null}

        {selectedTeamsValid &&
        swapQuery.isError ? (
          <section
            className="content-card swap-empty-state"
            aria-live="assertive"
          >
            <h2>Unable to calculate the swap</h2>
            <p>{errorMessage(swapQuery.error)}</p>
            <button
              type="button"
              onClick={() => {
                void swapQuery.refetch();
              }}
            >
              Try again
            </button>
          </section>
        ) : null}

        {swapQuery.data ? (
          <>
            <section
              className="swap-meta"
              aria-label="Schedule swap summary"
            >
              <div>
                <Trophy aria-hidden="true" />
                <span>
                  {
                    swapQuery.data.completed_weeks
                      .length
                  }{" "}
                  completed weeks compared
                </span>
              </div>
            </section>

            <section className="swap-results-grid">
              <TeamSwapSummary
                result={
                  swapQuery.data.first_team
                }
              />

              <TeamSwapSummary
                result={
                  swapQuery.data.second_team
                }
              />
            </section>
          </>
        ) : null}
      </main>
    </div>
  );
}
