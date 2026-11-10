import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, RefreshCw } from "lucide-react";
import { Link, useLocation, useParams } from "react-router-dom";

import { teamOptimalLineupQueryOptions } from "../api/analytics";
import { ApiError } from "../api/client";
import type {
  AnalyticsRecord,
  LineupPlayer,
  MatchupResult,
  TeamWeekOptimalLineupBreakdown,
} from "../types/analytics";
import "./OptimalLineupBreakdownPage.css";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to load the optimal-lineup breakdown.";
}

function recordLabel(record: AnalyticsRecord): string {
  return `${record.wins}-${record.losses}-${record.ties}`;
}

function resultLabel(result: MatchupResult): string {
  if (result === "win") {
    return "Win";
  }

  if (result === "loss") {
    return "Loss";
  }

  return "Tie";
}

function resultClass(result: MatchupResult): string {
  return `breakdown-result breakdown-result-${result}`;
}

function outcomeMessage(week: TeamWeekOptimalLineupBreakdown): string {
  const actual = resultLabel(week.actual_result).toLowerCase();
  const optimal = resultLabel(week.optimal_result).toLowerCase();

  if (week.result_flipped) {
    return (
      `The actual lineup recorded a ${actual}. ` +
      `The optimal lineup would have changed ` +
      `the result to a ${optimal}.`
    );
  }

  return (
    `The matchup remained a ${actual}, even ` +
    `with the highest-scoring legal lineup.`
  );
}

function playerSecondaryLabel(player: LineupPlayer): string {
  const values = [player.default_position, player.pro_team].filter(
    (value): value is string => value !== null && value.trim().length > 0,
  );

  return values.length > 0 ? values.join(" · ") : "No player information";
}

interface LineupTableProps {
  title: string;
  lineup: LineupPlayer[];
  tone: "actual" | "optimal";
  changedPlayerIds: Set<number>;
}

function LineupTable({
  title,
  lineup,
  tone,
  changedPlayerIds,
}: LineupTableProps) {
  return (
    <section
      className={`breakdown-lineup breakdown-lineup-${tone}`}
      aria-label={title}
    >
      <div className="breakdown-lineup-heading">
        <h4>{title}</h4>

        <span>
          {lineup
            .reduce((total, player) => total + player.points, 0)
            .toFixed(2)}{" "}
          points
        </span>
      </div>

      <div className="breakdown-lineup-columns">
        <span>Slot</span>
        <span>Player</span>
        <span>Points</span>
      </div>

      <div className="breakdown-lineup-rows">
        {lineup.map((player, index) => (
          <div
            className={[
              "breakdown-player-row",
              player.is_unfilled ? "breakdown-player-unfilled" : "",
              player.player_id !== null &&
              changedPlayerIds.has(player.player_id)
                ? `breakdown-player-changed breakdown-player-changed-${tone}`
                : "",
            ]
              .filter(Boolean)
              .join(" ")}
            key={`${player.lineup_slot}-${player.player_id ?? "empty"}-${index}`}
          >
            <span className="breakdown-slot">{player.lineup_slot}</span>

            <div className="breakdown-player">
              <strong>{player.player_name ?? "Unfilled slot"}</strong>

              <small>
                {player.is_unfilled
                  ? "No eligible player was available"
                  : playerSecondaryLabel(player)}
              </small>
            </div>

            <strong className="breakdown-player-points">
              {player.points.toFixed(2)}
            </strong>
          </div>
        ))}
      </div>
    </section>
  );
}

interface WeekPanelProps {
  week: TeamWeekOptimalLineupBreakdown;
  expanded: boolean;
  onToggle: () => void;
}

function WeekPanel({ week, expanded, onToggle }: WeekPanelProps) {
  const panelId = `optimal-week-${week.week}-content`;
  const buttonId = `optimal-week-${week.week}-button`;

  const playersOutIds = new Set(
    week.players_out.map((player) => player.player_id),
  );

  const playersInIds = new Set(
    week.players_in.map((player) => player.player_id),
  );

  return (
    <article
      className={
        week.result_flipped
          ? "breakdown-week breakdown-week-flipped"
          : "breakdown-week"
      }
    >
      <button
        id={buttonId}
        type="button"
        className="breakdown-week-toggle"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={onToggle}
      >
        <div className="breakdown-week-identity">
          <span>Week</span>
          <strong>{week.week}</strong>
        </div>

        <div className="breakdown-week-opponent">
          <span>Opponent</span>
          <strong>{week.opponent_team_name}</strong>
        </div>

        <div className="breakdown-week-stat">
          <span>Actual</span>
          <strong>{week.actual_points.toFixed(2)}</strong>
        </div>

        <div className="breakdown-week-stat">
          <span>Optimal</span>
          <strong>{week.optimal_points.toFixed(2)}</strong>
        </div>

        <div className="breakdown-week-stat">
          <span>Missed</span>
          <strong>+{week.missed_points.toFixed(2)}</strong>
        </div>

        <div className="breakdown-week-outcome">
          <span className={resultClass(week.actual_result)}>
            {resultLabel(week.actual_result)}
          </span>

          {week.result_flipped ? (
            <span className="breakdown-flip-label">Result flips</span>
          ) : null}
        </div>

        <ChevronDown
          className={
            expanded
              ? "breakdown-chevron breakdown-chevron-open"
              : "breakdown-chevron"
          }
          aria-hidden="true"
        />
      </button>

      {expanded ? (
        <div
          id={panelId}
          className="breakdown-week-content"
          role="region"
          aria-labelledby={buttonId}
        >
          <section className="breakdown-matchup">
            <div className="breakdown-matchup-heading">
              <div>
                <p className="panel-kicker">Matchup comparison</p>

                <h3>Actual result versus optimal result</h3>
              </div>

              <span
                className={
                  week.result_flipped
                    ? "breakdown-impact breakdown-impact-flipped"
                    : "breakdown-impact"
                }
              >
                {week.result_flipped ? "Outcome changed" : "Outcome unchanged"}
              </span>
            </div>

            <div className="breakdown-scoreboard">
              <article>
                <span>Actual lineup</span>

                <strong>{week.actual_points.toFixed(2)}</strong>

                <small className={resultClass(week.actual_result)}>
                  {resultLabel(week.actual_result)}
                </small>
              </article>

              <article className="breakdown-score-optimal">
                <span>Optimal lineup</span>

                <strong>{week.optimal_points.toFixed(2)}</strong>

                <small className={resultClass(week.optimal_result)}>
                  {resultLabel(week.optimal_result)}
                </small>
              </article>

              <article>
                <span>{week.opponent_team_name}</span>

                <strong>{week.opponent_points.toFixed(2)}</strong>

                <small>Opponent score</small>
              </article>
            </div>

            <p className="breakdown-outcome-message">{outcomeMessage(week)}</p>
          </section>

          <section className="breakdown-substitutions">
            <div className="breakdown-section-heading">
              <div>
                <p className="panel-kicker">What would change</p>

                <h3>Lineup changes</h3>
              </div>

              <div className="breakdown-net-gain">
                <span>Net lineup gain</span>

                <strong>+{week.net_point_gain.toFixed(2)}</strong>
              </div>
            </div>

            {week.players_out.length > 0 ||
            week.players_in.length > 0 ||
            week.retained_player_moves.length > 0 ? (
              <div className="breakdown-change-groups">
                <section
                  className="breakdown-change-group"
                  aria-label="Players removed from the lineup"
                >
                  <div className="breakdown-change-title">
                    <span>Players out</span>

                    <strong>{week.players_out.length}</strong>
                  </div>

                  {week.players_out.length > 0 ? (
                    <div className="breakdown-change-list">
                      {week.players_out.map((player) => (
                        <article key={player.player_id}>
                          <span className="breakdown-swap-slot">
                            {player.lineup_slot}
                          </span>

                          <div>
                            <strong>{player.player_name}</strong>

                            <small>
                              {[player.default_position, player.pro_team]
                                .filter(
                                  (value): value is string =>
                                    value !== null && value.length > 0,
                                )
                                .join(" · ") || "Rostered player"}
                            </small>
                          </div>

                          <strong className="breakdown-change-points breakdown-points-out">
                            {player.points.toFixed(2)}
                          </strong>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <p className="breakdown-change-empty">
                      No actual starter leaves the lineup.
                    </p>
                  )}
                </section>

                <section
                  className="breakdown-change-group breakdown-change-group-in"
                  aria-label="Players added to the lineup"
                >
                  <div className="breakdown-change-title">
                    <span>Players in</span>

                    <strong>{week.players_in.length}</strong>
                  </div>

                  {week.players_in.length > 0 ? (
                    <div className="breakdown-change-list">
                      {week.players_in.map((player) => (
                        <article key={player.player_id}>
                          <span className="breakdown-swap-slot">
                            {player.lineup_slot}
                          </span>

                          <div>
                            <strong>{player.player_name}</strong>

                            <small>
                              {[player.default_position, player.pro_team]
                                .filter(
                                  (value): value is string =>
                                    value !== null && value.length > 0,
                                )
                                .join(" · ") || "Rostered player"}
                            </small>
                          </div>

                          <strong className="breakdown-change-points breakdown-points-in">
                            {player.points.toFixed(2)}
                          </strong>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <p className="breakdown-change-empty">
                      No replacement enters the lineup. A negative-scoring slot
                      is left empty.
                    </p>
                  )}
                </section>

                {week.retained_player_moves.length > 0 ? (
                  <section
                    className="breakdown-movements"
                    aria-label="Retained starter slot movements"
                  >
                    <div className="breakdown-change-title">
                      <span>Slot movements</span>

                      <strong>{week.retained_player_moves.length}</strong>
                    </div>

                    <div className="breakdown-movement-list">
                      {week.retained_player_moves.map((movement) => (
                        <article key={movement.player_id}>
                          <div>
                            <strong>{movement.player_name}</strong>

                            <small>
                              Remains in the optimal starting lineup
                            </small>
                          </div>

                          <div className="breakdown-slot-move">
                            <span>{movement.from_slot}</span>

                            <span aria-hidden="true">→</span>

                            <strong>{movement.to_slot}</strong>
                          </div>

                          <strong className="breakdown-move-points">
                            {movement.points.toFixed(2)}
                          </strong>
                        </article>
                      ))}
                    </div>
                  </section>
                ) : null}
              </div>
            ) : (
              <p className="breakdown-no-changes">
                The actual starters were already the highest-scoring legal
                lineup for this roster.
              </p>
            )}

            <p className="breakdown-change-note">
              Players who remain starters but move between positions are shown
              separately. Slot movements do not add or subtract points from the
              net gain.
            </p>
          </section>

          <div className="breakdown-lineup-grid">
            <LineupTable
              title="Actual lineup"
              lineup={week.actual_lineup}
              tone="actual"
              changedPlayerIds={playersOutIds}
            />

            <LineupTable
              title="Optimal lineup"
              lineup={week.optimal_lineup}
              tone="optimal"
              changedPlayerIds={playersInIds}
            />
          </div>

          {week.unfilled_slots.length > 0 ? (
            <p className="breakdown-unfilled-note">
              Unfilled optimal slots: {week.unfilled_slots.join(", ")}. No
              eligible rostered player was available for those positions.
            </p>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export function OptimalLineupBreakdownPage() {
  const location = useLocation();
  const { teamId: teamIdParameter } = useParams();

  const optimalLineupsLocation = {
    pathname: "/optimal-lineups",
    search: location.search,
  };

  const parsedTeamId = Number(teamIdParameter);

  const teamId =
    Number.isInteger(parsedTeamId) && parsedTeamId > 0 ? parsedTeamId : 0;

  const breakdownQuery = useQuery(teamOptimalLineupQueryOptions(teamId));

  const [selectedWeek, setSelectedWeek] = useState<number | null>(null);

  if (teamId === 0) {
    return (
      <section className="app-state" aria-live="assertive">
        <h1>Invalid team</h1>

        <p>The optimal-lineup detail link does not contain a valid team ID.</p>

        <Link to={optimalLineupsLocation}>Return to optimal lineups</Link>
      </section>
    );
  }

  if (breakdownQuery.isPending) {
    return (
      <section className="app-state" aria-live="polite">
        <RefreshCw className="spinner" aria-hidden="true" />

        <h1>Loading weekly breakdown</h1>

        <p>
          Comparing actual starters, optimal starters, and completed matchup
          results.
        </p>
      </section>
    );
  }

  if (breakdownQuery.isError) {
    return (
      <section className="app-state" aria-live="assertive">
        <h1>Unable to load the lineup breakdown</h1>

        <p>{errorMessage(breakdownQuery.error)}</p>

        <button
          type="button"
          onClick={() => {
            void breakdownQuery.refetch();
          }}
        >
          Try again
        </button>

        <Link to={optimalLineupsLocation}>Return to optimal lineups</Link>
      </section>
    );
  }

  const breakdown = breakdownQuery.data;

  return (
    <div className="optimal-breakdown-page">
      <Link className="optimal-breakdown-back" to={optimalLineupsLocation}>
        <ArrowLeft aria-hidden="true" />
        <span>All optimal lineups</span>
      </Link>

      <header className="optimal-breakdown-header">
        <p className="page-kicker">Week-by-week manager analysis</p>

        <h1>Optimal Lineup Breakdown</h1>

        <h2>{breakdown.team_name}</h2>

        <p>
          Compare the lineup that was started each week with the highest-scoring
          legal lineup available from the same roster, then see whether those
          decisions changed the matchup result.
        </p>
      </header>

      <section
        className="optimal-breakdown-summary"
        aria-label="Season optimal-lineup summary"
      >
        <article>
          <span>Actual points</span>

          <strong>{breakdown.actual_points.toFixed(2)}</strong>

          <small>{recordLabel(breakdown.actual_record)} actual record</small>
        </article>

        <article>
          <span>Optimal points</span>

          <strong>{breakdown.optimal_points.toFixed(2)}</strong>

          <small>{recordLabel(breakdown.optimal_record)} optimal record</small>
        </article>

        <article>
          <span>Points left on bench</span>

          <strong>{breakdown.missed_points.toFixed(2)}</strong>

          <small>
            {breakdown.efficiency_percentage.toFixed(2)}% lineup efficiency
          </small>
        </article>

        <article>
          <span>Matchup impact</span>

          <strong>
            {breakdown.equivalent_wins_gained >= 0 ? "+" : ""}
            {breakdown.equivalent_wins_gained.toFixed(1)} wins
          </strong>

          <small>
            {breakdown.results_flipped}{" "}
            {breakdown.results_flipped === 1
              ? "result changed"
              : "results changed"}
          </small>
        </article>
      </section>

      <section
        className="optimal-breakdown-records"
        aria-labelledby="record-comparison-heading"
      >
        <div className="breakdown-section-heading">
          <div>
            <p className="panel-kicker">Season outcome</p>

            <h2 id="record-comparison-heading">Record comparison</h2>
          </div>

          <span>
            {breakdown.weeks_played} completed{" "}
            {breakdown.weeks_played === 1 ? "week" : "weeks"}
          </span>
        </div>

        <div className="breakdown-record-grid">
          <article>
            <span>Actual record</span>

            <strong>{recordLabel(breakdown.actual_record)}</strong>

            <small>
              {(breakdown.actual_record.win_percentage * 100).toFixed(1)}%
              equivalent win rate
            </small>
          </article>

          <article className="breakdown-record-optimal">
            <span>With optimal lineups</span>

            <strong>{recordLabel(breakdown.optimal_record)}</strong>

            <small>
              {(breakdown.optimal_record.win_percentage * 100).toFixed(1)}%
              equivalent win rate
            </small>
          </article>
        </div>
      </section>

      <section
        className="optimal-breakdown-weeks"
        aria-labelledby="weekly-breakdown-heading"
      >
        <div className="breakdown-section-heading">
          <div>
            <p className="panel-kicker">Weekly decisions</p>

            <h2 id="weekly-breakdown-heading">Breakdown by week</h2>
          </div>

          <span>Select a week to view every lineup decision</span>
        </div>

        {breakdown.weekly_results.length > 0 ? (
          <div className="breakdown-week-list">
            {breakdown.weekly_results.map((week) => (
              <WeekPanel
                key={week.week}
                week={week}
                expanded={selectedWeek === week.week}
                onToggle={() => {
                  setSelectedWeek(
                    selectedWeek === week.week ? null : week.week,
                  );
                }}
              />
            ))}
          </div>
        ) : (
          <div className="breakdown-empty">
            <h3>No completed weeks</h3>

            <p>
              Weekly optimal-lineup results will appear after completed roster
              and matchup data is synchronized.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
