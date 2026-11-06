import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Minus,
  RefreshCw,
  Trophy,
} from "lucide-react";

import { ApiError } from "../api/client";
import {
  allPlayQueryOptions,
  currentLeagueQueryOptions,
} from "../api/league";
import { useAuth } from "../auth/AuthProvider";
import "./AllPlayPage.css";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unable to load all-play standings.";
}

function ordinal(rank: number): string {
  const remainder100 = rank % 100;

  if (
    remainder100 >= 11 &&
    remainder100 <= 13
  ) {
    return `${rank}th`;
  }

  switch (rank % 10) {
    case 1:
      return `${rank}st`;
    case 2:
      return `${rank}nd`;
    case 3:
      return `${rank}rd`;
    default:
      return `${rank}th`;
  }
}

export function AllPlayPage() {
  const auth = useAuth();

  const leagueQuery = useQuery(
    currentLeagueQueryOptions(),
  );

  const allPlayQuery = useQuery(
    allPlayQueryOptions(),
  );

  const actualRankByTeam = useMemo(() => {
    const ordered = [
      ...(leagueQuery.data?.teams ?? []),
    ].sort(
      (first, second) =>
        second.wins - first.wins ||
        first.losses - second.losses ||
        second.points_for - first.points_for ||
        first.name.localeCompare(second.name),
    );

    return new Map(
      ordered.map((team, index) => [
        team.team_id,
        index + 1,
      ]),
    );
  }, [leagueQuery.data?.teams]);

  if (
    leagueQuery.isPending ||
    allPlayQuery.isPending
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
        <h1>Calculating all-play standings</h1>
        <p>
          Comparing every team against every other
          team for each completed week.
        </p>
      </section>
    );
  }

  if (
    leagueQuery.isError ||
    allPlayQuery.isError
  ) {
    const currentError =
      leagueQuery.error ??
      allPlayQuery.error;

    return (
      <section
        className="app-state"
        aria-live="assertive"
      >
        <h1>Unable to load all-play standings</h1>
        <p>{errorMessage(currentError)}</p>

        <button
          type="button"
          onClick={() => {
            void Promise.all([
              leagueQuery.refetch(),
              allPlayQuery.refetch(),
            ]);
          }}
        >
          Try again
        </button>
      </section>
    );
  }

  const league = leagueQuery.data;
  const standings = allPlayQuery.data;
  const currentTeamId = auth.user?.team_id;

  const currentTeamStanding =
    currentTeamId === null ||
    currentTeamId === undefined
      ? null
      : standings.find(
          (standing) =>
            standing.team_id === currentTeamId,
        ) ?? null;

  const leader = standings[0];

  return (
    <div className="all-play-page">
      <header className="all-play-header">
        <p className="page-kicker">
          Schedule-independent results
        </p>

        <h1>All-Play Standings</h1>

        <p>
          All-play shows what each team&apos;s record
          would be if everyone played everyone each
          week. It removes schedule luck and provides
          a clearer measure of weekly team strength.
        </p>
      </header>

      <section
        className="all-play-summary"
        aria-label="All-play summary"
      >
        <article className="all-play-summary-item">
          <span>Current leader</span>
          <strong>
            {leader?.team_name ?? "Unavailable"}
          </strong>
          <small>
            {leader
              ? `${leader.wins}-${leader.losses}-${leader.ties}`
              : "No completed results"}
          </small>
        </article>

        <article className="all-play-summary-item">
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

        <article className="all-play-summary-item">
          <span>Your all-play rank</span>
          <strong>
            {currentTeamStanding
              ? ordinal(currentTeamStanding.rank)
              : "Not assigned"}
          </strong>
          <small>
            {currentTeamStanding
              ? currentTeamStanding.team_name
              : "Administrator overview"}
          </small>
        </article>
      </section>

      <section
        className="all-play-panel"
        aria-labelledby="all-play-table-heading"
      >
        <div className="all-play-panel-heading">
          <div>
            <p className="panel-kicker">
              Performance ranking
            </p>

            <h2 id="all-play-table-heading">
              League comparison
            </h2>
          </div>

          <span>{league.season} season</span>
        </div>

        <div className="all-play-column-headings">
          <span>Rank</span>
          <span>Team</span>
          <span>All-play record</span>
          <span>Win rate</span>
          <span>Actual rank</span>
          <span>Difference</span>
        </div>

        <div className="all-play-standings">
          {standings.map((standing) => {
            const actualRank =
              actualRankByTeam.get(
                standing.team_id,
              ) ?? standing.rank;

            const difference =
              actualRank - standing.rank;

            const isCurrentTeam =
              standing.team_id === currentTeamId;

            return (
              <article
                key={standing.team_id}
                className={
                  isCurrentTeam
                    ? "all-play-row all-play-row-current"
                    : "all-play-row"
                }
              >
                <div className="all-play-rank">
                  {standing.rank === 1 ? (
                    <Trophy aria-hidden="true" />
                  ) : null}

                  <strong>{standing.rank}</strong>
                </div>

                <div className="all-play-team">
                  <strong>
                    {standing.team_name}
                  </strong>

                  <small>
                    {isCurrentTeam
                      ? "Your team"
                      : `${standing.points_for.toFixed(
                          1,
                        )} points`}
                  </small>
                </div>

                <div className="all-play-record">
                  <span className="mobile-field-label">
                    Record
                  </span>

                  <strong>
                    {standing.wins}-
                    {standing.losses}-
                    {standing.ties}
                  </strong>
                </div>

                <div className="all-play-percentage">
                  <span className="mobile-field-label">
                    Win rate
                  </span>

                  <strong>
                    {(
                      standing.win_percentage * 100
                    ).toFixed(1)}
                    %
                  </strong>
                </div>

                <div className="actual-rank">
                  <span className="mobile-field-label">
                    Actual
                  </span>

                  <strong>#{actualRank}</strong>
                </div>

                <div
                  className={
                    difference > 0
                      ? "rank-difference rank-difference-positive"
                      : difference < 0
                        ? "rank-difference rank-difference-negative"
                        : "rank-difference"
                  }
                >
                  {difference > 0 ? (
                    <ArrowUp aria-hidden="true" />
                  ) : difference < 0 ? (
                    <ArrowDown aria-hidden="true" />
                  ) : (
                    <Minus aria-hidden="true" />
                  )}

                  <span>
                    {difference > 0
                      ? `+${difference}`
                      : difference}
                  </span>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      <section className="all-play-explanation">
        <h2>How to read the difference</h2>

        <p>
          A positive difference means the team ranks
          higher in all-play than in the actual
          standings, which may indicate difficult
          scheduled opponents. A negative difference
          means the actual record is stronger than
          the weekly scoring comparison.
        </p>
      </section>
    </div>
  );
}
