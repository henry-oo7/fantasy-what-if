import { describe, expect, it } from "vitest";

import type {
  PlayoffPredictorMatchup,
  PlayoffPredictorSnapshot,
  PlayoffPredictorTeam,
} from "../types/league";
import {
  buildProjectedBracket,
  calculateProjectedStandings,
  predictionsForWeek,
  remainingWeeks,
} from "./playoffPredictor";

function team(
  id: number,
  wins: number,
  losses: number,
  pointsFor = 1000 - id,
): PlayoffPredictorTeam {
  return {
    team_id: id,
    name: `Team ${id}`,
    wins,
    losses,
    ties: 0,
    points_for: pointsFor,
    points_against: 900 + id,
  };
}

function matchup(
  id: number,
  week: number,
  home: number,
  away: number,
  complete = false,
  homeScore: number | null = null,
  awayScore: number | null = null,
): PlayoffPredictorMatchup {
  return {
    matchup_id: id,
    week,
    home_team_id: home,
    away_team_id: away,
    home_score: homeScore,
    away_score: awayScore,
    is_complete: complete,
  };
}

function snapshot(
  teams: PlayoffPredictorTeam[],
  completed: PlayoffPredictorMatchup[] = [],
  remaining: PlayoffPredictorMatchup[] = [],
  playoffTeams = 6,
  playoffSeedTieRule = "H2H_RECORD",
): PlayoffPredictorSnapshot {
  return {
    league_id: 1,
    league_name: "Test League",
    season_id: 1,
    season: 2026,
    current_week: 11,
    regular_season_weeks: 14,
    playoff_team_count: playoffTeams,
    playoff_seed_tie_rule: playoffSeedTieRule,
    playoff_seed_tie_rule_source: "provider",
    allows_ties: true,
    teams,
    completed_matchups: completed,
    remaining_matchups: remaining,
  };
}

describe("playoff predictor standings", () => {
  it("applies selected winners to projected records", () => {
    const data = snapshot(
      [team(1, 6, 4), team(2, 5, 5)],
      [],
      [matchup(101, 11, 1, 2)],
      2,
    );

    const standings = calculateProjectedStandings(data, {
      101: 2,
    });

    expect(
      standings.map((standing) => ({
        team: standing.team_id,
        record: `${standing.wins}-${standing.losses}`,
      })),
    ).toEqual([
      {
        team: 2,
        record: "6-5",
      },
      {
        team: 1,
        record: "6-5",
      },
    ]);
  });

  it("does not double-count when a selection changes", () => {
    const data = snapshot(
      [team(1, 6, 4), team(2, 5, 5)],
      [],
      [matchup(101, 11, 1, 2)],
      2,
    );

    const firstResult = calculateProjectedStandings(data, {
      101: 1,
    });

    const secondResult = calculateProjectedStandings(data, {
      101: 2,
    });

    expect(firstResult[0]).toMatchObject({
      team_id: 1,
      wins: 7,
      losses: 4,
    });

    expect(secondResult).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          team_id: 1,
          wins: 6,
          losses: 5,
        }),
        expect.objectContaining({
          team_id: 2,
          wins: 6,
          losses: 5,
        }),
      ]),
    );
  });

  it("uses completed head-to-head results for two-team ties", () => {
    const data = snapshot(
      [team(1, 6, 5, 900), team(2, 6, 5, 1100)],
      [matchup(1, 4, 1, 2, true, 120, 100)],
      [],
      2,
    );

    const standings = calculateProjectedStandings(data, {});

    expect(standings[0]).toMatchObject({
      team_id: 1,
      tiebreaker: "head_to_head",
      head_to_head_percentage: 1,
    });

    expect(standings[1]).toMatchObject({
      team_id: 2,
      tiebreaker: "head_to_head",
      head_to_head_percentage: 0,
    });
  });

  it("calculates head-to-head percentages across a tied group", () => {
    const data = snapshot(
      [team(1, 6, 5), team(2, 6, 5), team(3, 6, 5)],
      [
        matchup(1, 1, 1, 2, true, 110, 90),
        matchup(2, 2, 1, 3, true, 105, 95),
        matchup(3, 3, 2, 3, true, 100, 90),
      ],
      [],
      3,
    );

    const standings = calculateProjectedStandings(data, {});

    expect(standings.map((standing) => standing.team_id)).toEqual([1, 2, 3]);

    expect(
      standings.map((standing) => standing.head_to_head_percentage),
    ).toEqual([1, 0.5, 0]);
  });

  it("uses points for before head-to-head when the provider requires it", () => {
    const data = snapshot(
      [team(1, 6, 5, 900), team(2, 6, 5, 1100)],
      [matchup(1, 4, 1, 2, true, 120, 100)],
      [],
      2,
      "TOTAL_POINTS_SCORED",
    );

    const standings = calculateProjectedStandings(data, {});

    expect(standings[0]).toMatchObject({
      team_id: 2,
      tiebreaker: "points_for",
      head_to_head_percentage: null,
    });
    expect(standings[1]).toMatchObject({
      team_id: 1,
      tiebreaker: "points_for",
      head_to_head_percentage: null,
    });
  });

  it("uses current points for when head-to-head is equal", () => {
    const data = snapshot(
      [team(1, 6, 5, 1100), team(2, 6, 5, 1000)],
      [matchup(1, 1, 1, 2, true, 110, 90), matchup(2, 2, 2, 1, true, 120, 100)],
      [],
      2,
    );

    const standings = calculateProjectedStandings(data, {});

    expect(standings[0]).toMatchObject({
      team_id: 1,
      tiebreaker: "points_for",
    });

    expect(standings[1]).toMatchObject({
      team_id: 2,
      tiebreaker: "points_for",
    });
  });

  it("supports predicted ties", () => {
    const data = snapshot(
      [team(1, 5, 5), team(2, 5, 5)],
      [],
      [matchup(101, 11, 1, 2)],
      2,
    );

    const standings = calculateProjectedStandings(data, {
      101: "tie",
    });

    expect(standings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          team_id: 1,
          ties: 1,
        }),
        expect.objectContaining({
          team_id: 2,
          ties: 1,
        }),
      ]),
    );
  });
});

describe("playoff predictor bracket", () => {
  function standings(count: number) {
    const data = snapshot(
      Array.from(
        {
          length: count,
        },
        (_, index) => team(index + 1, count - index, index),
      ),
      [],
      [],
      count,
    );

    return calculateProjectedStandings(data, {});
  }

  it("builds a four-team bracket", () => {
    expect(
      buildProjectedBracket(standings(4), 4).map((entry) => [
        entry.first.seed,
        entry.second?.seed,
      ]),
    ).toEqual([
      [1, 4],
      [2, 3],
    ]);
  });

  it("gives the top two seeds byes in a six-team bracket", () => {
    expect(
      buildProjectedBracket(standings(6), 6).map((entry) => ({
        first: entry.first.seed,
        second: entry.second?.seed ?? null,
        bye: entry.is_bye,
      })),
    ).toEqual([
      {
        first: 1,
        second: null,
        bye: true,
      },
      {
        first: 2,
        second: null,
        bye: true,
      },
      {
        first: 3,
        second: 6,
        bye: false,
      },
      {
        first: 4,
        second: 5,
        bye: false,
      },
    ]);
  });

  it("builds an eight-team bracket", () => {
    expect(
      buildProjectedBracket(standings(8), 8).map((entry) => [
        entry.first.seed,
        entry.second?.seed,
      ]),
    ).toEqual([
      [1, 8],
      [2, 7],
      [3, 6],
      [4, 5],
    ]);
  });
});

describe("playoff predictor controls", () => {
  it("removes only predictions from one week", () => {
    const matchups = [
      matchup(101, 11, 1, 2),
      matchup(102, 11, 3, 4),
      matchup(201, 12, 1, 3),
    ];

    expect(
      predictionsForWeek(
        matchups,
        {
          101: 1,
          102: 4,
          201: 3,
        },
        11,
      ),
    ).toEqual({
      201: 3,
    });
  });

  it("returns sorted remaining weeks without duplicates", () => {
    const data = snapshot(
      [team(1, 5, 5), team(2, 5, 5)],
      [],
      [matchup(201, 12, 1, 2), matchup(101, 11, 1, 2), matchup(102, 11, 2, 1)],
      2,
    );

    expect(remainingWeeks(data)).toEqual([11, 12]);
  });
});
