import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CalendarDays,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  RotateCcw,
  Trophy,
} from "lucide-react";

import { ApiError } from "../api/client";
import { playoffPredictorQueryOptions } from "../api/league";
import type { PlayoffPredictorMatchup } from "../types/league";
import {
  buildProjectedBracket,
  calculateProjectedStandings,
  predictionsForWeek,
  remainingWeeks,
} from "../utils/playoffPredictor";
import type {
  PredictedResult,
  PredictionSelections,
} from "../utils/playoffPredictor";
import "./PlayoffPredictorPage.css";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to load the playoff predictor.";
}

function recordLabel(record: {
  wins: number;
  losses: number;
  ties: number;
}): string {
  return record.ties > 0
    ? `${record.wins}-${record.losses}-${record.ties}`
    : `${record.wins}-${record.losses}`;
}

function tiebreakerLabel(
  tiebreaker: "record" | "head_to_head" | "points_for" | "current_standings",
): string {
  if (tiebreaker === "head_to_head") {
    return "Head-to-head";
  }

  if (tiebreaker === "points_for") {
    return "Points for";
  }

  if (tiebreaker === "current_standings") {
    return "Current order";
  }

  return "Record";
}

function selectionMatches(
  selection: PredictedResult | undefined,
  result: PredictedResult,
): boolean {
  return selection === result;
}

function predictedMatchupCount(predictions: PredictionSelections): number {
  return Object.keys(predictions).length;
}

function weekPredictionCount(
  matchups: readonly PlayoffPredictorMatchup[],
  predictions: PredictionSelections,
  week: number,
): number {
  return matchups.filter(
    (matchup) =>
      matchup.week === week && predictions[matchup.matchup_id] !== undefined,
  ).length;
}

export function PlayoffPredictorPage() {
  const predictorQuery = useQuery(playoffPredictorQueryOptions());

  const [selectedWeek, setSelectedWeek] = useState<number | null>(null);

  const [matchupsExpanded, setMatchupsExpanded] = useState(true);

  const [predictions, setPredictions] = useState<PredictionSelections>({});

  const weeks = useMemo(
    () => (predictorQuery.data ? remainingWeeks(predictorQuery.data) : []),
    [predictorQuery.data],
  );

  const activeWeek =
    selectedWeek !== null && weeks.includes(selectedWeek)
      ? selectedWeek
      : (weeks[0] ?? null);

  const standings = useMemo(
    () =>
      predictorQuery.data
        ? calculateProjectedStandings(predictorQuery.data, predictions)
        : [],
    [predictorQuery.data, predictions],
  );

  const bracket = useMemo(
    () =>
      predictorQuery.data
        ? buildProjectedBracket(
            standings,
            predictorQuery.data.playoff_team_count,
          )
        : [],
    [predictorQuery.data, standings],
  );

  const selectedMatchups = useMemo(() => {
    if (predictorQuery.data === undefined || activeWeek === null) {
      return [];
    }

    return predictorQuery.data.remaining_matchups
      .filter((matchup) => matchup.week === activeWeek)
      .sort((first, second) => first.matchup_id - second.matchup_id);
  }, [predictorQuery.data, activeWeek]);

  const teamNames = useMemo(
    () =>
      new Map(
        predictorQuery.data?.teams.map((team) => [team.team_id, team.name]) ??
          [],
      ),
    [predictorQuery.data],
  );

  function chooseResult(matchupId: number, result: PredictedResult) {
    setPredictions((current) => {
      if (selectionMatches(current[matchupId], result)) {
        const updated = {
          ...current,
        };

        delete updated[matchupId];

        return updated;
      }

      return {
        ...current,
        [matchupId]: result,
      };
    });
  }

  function resetSelectedWeek() {
    if (predictorQuery.data === undefined || activeWeek === null) {
      return;
    }

    setPredictions((current) =>
      predictionsForWeek(
        predictorQuery.data.remaining_matchups,
        current,
        activeWeek,
      ),
    );
  }

  function resetAll() {
    setPredictions({});
  }

  if (predictorQuery.isPending) {
    return (
      <main className="app-state" aria-live="polite">
        <RefreshCw className="spinner" aria-hidden="true" />

        <h1>Loading playoff predictor</h1>

        <p>Preparing the remaining schedule and playoff configuration.</p>
      </main>
    );
  }

  if (predictorQuery.isError) {
    const configurationUnavailable =
      predictorQuery.error instanceof ApiError &&
      predictorQuery.error.status === 409;

    return (
      <main className="app-state" aria-live="assertive">
        <Trophy aria-hidden="true" />

        <h1>
          {configurationUnavailable
            ? "Playoff setup needs a refresh"
            : "Unable to load the predictor"}
        </h1>

        <p>{errorMessage(predictorQuery.error)}</p>

        {configurationUnavailable ? (
          <p>
            An administrator can run a normal synchronization to import the
            league&apos;s playoff team count and seeding rule.
          </p>
        ) : null}

        <button
          type="button"
          onClick={() => {
            void predictorQuery.refetch();
          }}
        >
          Try again
        </button>
      </main>
    );
  }

  const predictor = predictorQuery.data;
  const selectedWeekPredictions =
    activeWeek === null
      ? 0
      : weekPredictionCount(
          predictor.remaining_matchups,
          predictions,
          activeWeek,
        );

  return (
    <div className="playoff-predictor-page">
      <header className="playoff-predictor-header">
        <p className="page-kicker">Regular-season scenario builder</p>

        <h1>Playoff Predictor</h1>

        <p>
          Choose winners for the remaining schedule. Projected standings and
          playoff seeds update immediately without changing imported league
          data.
        </p>

        <div className="predictor-header-status">
          <span>{predictedMatchupCount(predictions)} predictions selected</span>

          <span>{predictor.playoff_team_count} playoff teams</span>

          <button
            type="button"
            disabled={predictedMatchupCount(predictions) === 0}
            onClick={resetAll}
          >
            <RotateCcw aria-hidden="true" />
            Reset all
          </button>
        </div>
      </header>

      <main className="playoff-predictor-content">
        <section
          className="predictor-card predictor-weeks-card"
          aria-labelledby="predictor-weeks-heading"
        >
          <div className="predictor-section-heading">
            <div>
              <p className="section-label">Remaining schedule</p>

              <h2 id="predictor-weeks-heading">Choose a week</h2>
            </div>

            <span>Through week {Math.max(predictor.current_week - 1, 0)}</span>
          </div>

          {weeks.length > 0 ? (
            <div
              className="predictor-week-list"
              aria-label="Remaining regular-season weeks"
            >
              {weeks.map((week) => {
                const selected = week === activeWeek;

                const count = weekPredictionCount(
                  predictor.remaining_matchups,
                  predictions,
                  week,
                );

                const total = predictor.remaining_matchups.filter(
                  (matchup) => matchup.week === week,
                ).length;

                return (
                  <button
                    key={week}
                    type="button"
                    className={
                      selected
                        ? "predictor-week-button predictor-week-button-active"
                        : "predictor-week-button"
                    }
                    aria-pressed={selected}
                    onClick={() => {
                      setSelectedWeek(week);
                      setMatchupsExpanded(true);
                    }}
                  >
                    <span>Week {week}</span>
                    <small>
                      {count}/{total} picked
                    </small>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="predictor-empty-copy">
              No remaining regular-season matchups are available.
            </p>
          )}

          {activeWeek !== null ? (
            <div className="predictor-matchup-section">
              <div className="predictor-matchup-toolbar">
                <button
                  type="button"
                  className="predictor-expand-button"
                  aria-expanded={matchupsExpanded}
                  aria-controls="predictor-week-matchups"
                  onClick={() => {
                    setMatchupsExpanded((current) => !current);
                  }}
                >
                  <CalendarDays aria-hidden="true" />

                  <span>Week {activeWeek} matchups</span>

                  <small>
                    {selectedWeekPredictions}/{selectedMatchups.length} picked
                  </small>

                  {matchupsExpanded ? (
                    <ChevronUp aria-hidden="true" />
                  ) : (
                    <ChevronDown aria-hidden="true" />
                  )}
                </button>

                <button
                  type="button"
                  className="predictor-reset-week"
                  disabled={selectedWeekPredictions === 0}
                  onClick={resetSelectedWeek}
                >
                  Reset week
                </button>
              </div>

              {matchupsExpanded ? (
                <div
                  id="predictor-week-matchups"
                  className="predictor-matchup-list"
                >
                  {selectedMatchups.map((matchup) => {
                    const selection = predictions[matchup.matchup_id];

                    const homeName =
                      teamNames.get(matchup.home_team_id) ?? "Unknown team";

                    const awayName =
                      teamNames.get(matchup.away_team_id) ?? "Unknown team";

                    return (
                      <article
                        key={matchup.matchup_id}
                        className="predictor-matchup"
                      >
                        <p>Choose the projected winner</p>

                        <div className="predictor-team-picks">
                          <button
                            type="button"
                            className={
                              selection === matchup.home_team_id
                                ? "predictor-team-pick predictor-team-pick-selected"
                                : "predictor-team-pick"
                            }
                            aria-pressed={selection === matchup.home_team_id}
                            onClick={() => {
                              chooseResult(
                                matchup.matchup_id,
                                matchup.home_team_id,
                              );
                            }}
                          >
                            <span>{homeName}</span>
                            <small>Home</small>
                          </button>

                          <span className="predictor-versus">vs</span>

                          <button
                            type="button"
                            className={
                              selection === matchup.away_team_id
                                ? "predictor-team-pick predictor-team-pick-selected"
                                : "predictor-team-pick"
                            }
                            aria-pressed={selection === matchup.away_team_id}
                            onClick={() => {
                              chooseResult(
                                matchup.matchup_id,
                                matchup.away_team_id,
                              );
                            }}
                          >
                            <span>{awayName}</span>
                            <small>Away</small>
                          </button>
                        </div>

                        {predictor.allows_ties ? (
                          <button
                            type="button"
                            className={
                              selection === "tie"
                                ? "predictor-tie-pick predictor-tie-pick-selected"
                                : "predictor-tie-pick"
                            }
                            aria-pressed={selection === "tie"}
                            onClick={() => {
                              chooseResult(matchup.matchup_id, "tie");
                            }}
                          >
                            Project a tie
                          </button>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}
        </section>

        <div className="predictor-results-grid">
          <section
            className="predictor-card predictor-standings-card"
            aria-labelledby="projected-standings-heading"
          >
            <div className="predictor-section-heading">
              <div>
                <p className="section-label">Live projection</p>

                <h2 id="projected-standings-heading">Projected standings</h2>
              </div>

              <span>Top {predictor.playoff_team_count} qualify</span>
            </div>

            <div className="predictor-standings">
              {standings.map((standing) => {
                const qualifies = standing.seed <= predictor.playoff_team_count;

                return (
                  <div
                    key={standing.team_id}
                    className={
                      qualifies
                        ? "predictor-standing-row predictor-standing-row-playoff"
                        : "predictor-standing-row"
                    }
                  >
                    <span className="predictor-seed">{standing.seed}</span>

                    <div className="predictor-standing-team">
                      <strong>{standing.name}</strong>

                      <small>{tiebreakerLabel(standing.tiebreaker)}</small>
                    </div>

                    <div className="predictor-standing-metrics">
                      <strong className="predictor-record">
                        {recordLabel(standing)}
                      </strong>
                      <small>{standing.points_for.toFixed(2)} PF</small>
                    </div>

                    <span
                      className={
                        qualifies
                          ? "predictor-status predictor-status-in"
                          : "predictor-status"
                      }
                    >
                      {qualifies ? "IN" : "OUT"}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>

          <section
            className="predictor-card predictor-bracket-card"
            aria-labelledby="projected-bracket-heading"
          >
            <div className="predictor-section-heading">
              <div>
                <p className="section-label">Live playoff field</p>

                <h2 id="projected-bracket-heading">Projected bracket</h2>
              </div>

              <Trophy aria-hidden="true" />
            </div>

            <div className="predictor-bracket">
              {bracket.map((matchup) => (
                <article
                  key={matchup.position}
                  className={
                    matchup.is_bye
                      ? "predictor-bracket-matchup predictor-bracket-bye"
                      : "predictor-bracket-matchup"
                  }
                >
                  <p>
                    {matchup.is_bye
                      ? "First-round bye"
                      : `Matchup ${matchup.position}`}
                  </p>

                  <div className="predictor-bracket-team">
                    <span>{matchup.first.seed}</span>

                    <div className="predictor-bracket-team-details">
                      <strong>{matchup.first.team_name}</strong>
                      <small>{matchup.first.points_for.toFixed(2)} PF</small>
                    </div>
                  </div>

                  {matchup.second ? (
                    <>
                      <span className="predictor-bracket-versus">vs</span>

                      <div className="predictor-bracket-team">
                        <span>{matchup.second.seed}</span>

                        <div className="predictor-bracket-team-details">
                          <strong>{matchup.second.team_name}</strong>
                          <small>
                            {matchup.second.points_for.toFixed(2)} PF
                          </small>
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="predictor-bye-label">
                      Advances automatically
                    </div>
                  )}
                </article>
              ))}
            </div>

            <p className="predictor-bracket-note">
              {predictor.playoff_seed_tie_rule === "H2H_RECORD"
                ? "Tied projected records use head-to-head record, followed by points already scored, then the current standings order."
                : "Tied projected records use points already scored, followed by the current standings order."}
            </p>
          </section>
        </div>
      </main>
    </div>
  );
}
