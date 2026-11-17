import type {
  PlayoffPredictorMatchup,
  PlayoffPredictorSnapshot,
  PlayoffPredictorTeam,
} from "../types/league";

export type PredictedResult = number | "tie";

export type PredictionSelections = Readonly<Record<number, PredictedResult>>;

export interface ProjectedStanding {
  seed: number;
  team_id: number;
  name: string;
  wins: number;
  losses: number;
  ties: number;
  win_percentage: number;
  head_to_head_percentage: number | null;
  points_for: number;
  current_rank: number;
  tiebreaker: "record" | "head_to_head" | "points_for" | "current_standings";
}

export interface ProjectedBracketEntry {
  seed: number;
  team_id: number;
  team_name: string;
  points_for: number;
}

export interface ProjectedBracketMatchup {
  position: number;
  first: ProjectedBracketEntry;
  second: ProjectedBracketEntry | null;
  is_bye: boolean;
}

interface MutableRecord {
  team: PlayoffPredictorTeam;
  wins: number;
  losses: number;
  ties: number;
  currentRank: number;
}

interface HeadToHeadRecord {
  wins: number;
  losses: number;
  ties: number;
}

function equivalentWins(wins: number, ties: number): number {
  return wins + ties * 0.5;
}

function winPercentage(wins: number, losses: number, ties: number): number {
  const decisions = wins + losses + ties;

  return decisions === 0 ? 0 : equivalentWins(wins, ties) / decisions;
}

function compareCurrentTeams(
  first: PlayoffPredictorTeam,
  second: PlayoffPredictorTeam,
): number {
  const firstPercentage = winPercentage(first.wins, first.losses, first.ties);
  const secondPercentage = winPercentage(
    second.wins,
    second.losses,
    second.ties,
  );

  return (
    secondPercentage - firstPercentage ||
    second.points_for - first.points_for ||
    first.team_id - second.team_id
  );
}

function currentRanks(
  teams: readonly PlayoffPredictorTeam[],
): Map<number, number> {
  return new Map(
    [...teams]
      .sort(compareCurrentTeams)
      .map((team, index) => [team.team_id, index + 1]),
  );
}

function applyPredictions(
  snapshot: PlayoffPredictorSnapshot,
  predictions: PredictionSelections,
): Map<number, MutableRecord> {
  const ranks = currentRanks(snapshot.teams);

  const records = new Map(
    snapshot.teams.map((team) => [
      team.team_id,
      {
        team,
        wins: team.wins,
        losses: team.losses,
        ties: team.ties,
        currentRank: ranks.get(team.team_id) ?? Number.MAX_SAFE_INTEGER,
      },
    ]),
  );

  for (const matchup of snapshot.remaining_matchups) {
    const result = predictions[matchup.matchup_id];

    if (result === undefined) {
      continue;
    }

    const home = records.get(matchup.home_team_id);
    const away = records.get(matchup.away_team_id);

    if (home === undefined || away === undefined) {
      continue;
    }

    if (result === "tie") {
      home.ties += 1;
      away.ties += 1;
      continue;
    }

    if (result === home.team.team_id) {
      home.wins += 1;
      away.losses += 1;
      continue;
    }

    if (result === away.team.team_id) {
      away.wins += 1;
      home.losses += 1;
    }
  }

  return records;
}

function matchupResult(
  matchup: PlayoffPredictorMatchup,
  predictions: PredictionSelections,
): PredictedResult | undefined {
  if (!matchup.is_complete) {
    return predictions[matchup.matchup_id];
  }

  if (matchup.home_score === null || matchup.away_score === null) {
    return undefined;
  }

  if (matchup.home_score === matchup.away_score) {
    return "tie";
  }

  return matchup.home_score > matchup.away_score
    ? matchup.home_team_id
    : matchup.away_team_id;
}

function tiedGroupHeadToHead(
  group: readonly MutableRecord[],
  matchups: readonly PlayoffPredictorMatchup[],
  predictions: PredictionSelections,
): Map<number, number | null> {
  const groupIds = new Set(group.map((record) => record.team.team_id));

  const records = new Map<number, HeadToHeadRecord>(
    group.map((record) => [
      record.team.team_id,
      {
        wins: 0,
        losses: 0,
        ties: 0,
      },
    ]),
  );

  for (const matchup of matchups) {
    if (
      !groupIds.has(matchup.home_team_id) ||
      !groupIds.has(matchup.away_team_id)
    ) {
      continue;
    }

    const result = matchupResult(matchup, predictions);

    if (result === undefined) {
      continue;
    }

    const home = records.get(matchup.home_team_id);
    const away = records.get(matchup.away_team_id);

    if (home === undefined || away === undefined) {
      continue;
    }

    if (result === "tie") {
      home.ties += 1;
      away.ties += 1;
    } else if (result === matchup.home_team_id) {
      home.wins += 1;
      away.losses += 1;
    } else if (result === matchup.away_team_id) {
      away.wins += 1;
      home.losses += 1;
    }
  }

  return new Map(
    [...records.entries()].map(([teamId, record]) => {
      const decisions = record.wins + record.losses + record.ties;

      return [
        teamId,
        decisions === 0
          ? null
          : equivalentWins(record.wins, record.ties) / decisions,
      ];
    }),
  );
}

function sameProjectedRecord(
  first: MutableRecord,
  second: MutableRecord,
): boolean {
  return (
    first.wins === second.wins &&
    first.losses === second.losses &&
    first.ties === second.ties
  );
}

export function calculateProjectedStandings(
  snapshot: PlayoffPredictorSnapshot,
  predictions: PredictionSelections,
): ProjectedStanding[] {
  const records = [...applyPredictions(snapshot, predictions).values()].sort(
    (first, second) =>
      winPercentage(second.wins, second.losses, second.ties) -
        winPercentage(first.wins, first.losses, first.ties) ||
      first.currentRank - second.currentRank,
  );

  const allMatchups = [
    ...snapshot.completed_matchups,
    ...snapshot.remaining_matchups,
  ];

  const standings: ProjectedStanding[] = [];

  for (let groupStart = 0; groupStart < records.length;) {
    let groupEnd = groupStart + 1;

    while (
      groupEnd < records.length &&
      sameProjectedRecord(records[groupStart], records[groupEnd])
    ) {
      groupEnd += 1;
    }

    const group = records.slice(groupStart, groupEnd);

    const usesHeadToHead =
      snapshot.playoff_seed_tie_rule === "H2H_RECORD";
    const headToHead = usesHeadToHead
      ? tiedGroupHeadToHead(group, allMatchups, predictions)
      : new Map<number, number | null>();

    group.sort((first, second) => {
      if (usesHeadToHead) {
        const firstHeadToHead = headToHead.get(first.team.team_id);
        const secondHeadToHead = headToHead.get(second.team.team_id);

        if (
          firstHeadToHead !== null &&
          firstHeadToHead !== undefined &&
          secondHeadToHead !== null &&
          secondHeadToHead !== undefined &&
          firstHeadToHead !== secondHeadToHead
        ) {
          return secondHeadToHead - firstHeadToHead;
        }
      }

      return (
        second.team.points_for - first.team.points_for ||
        first.currentRank - second.currentRank
      );
    });

    for (const record of group) {
      const percentage = headToHead.get(record.team.team_id);

      const hasHeadToHeadDifference =
        usesHeadToHead &&
        group.length > 1 &&
        percentage !== null &&
        percentage !== undefined &&
        group.some((other) => {
          const otherPercentage = headToHead.get(other.team.team_id);

          return (
            otherPercentage !== null &&
            otherPercentage !== undefined &&
            otherPercentage !== percentage
          );
        });

      standings.push({
        seed: standings.length + 1,
        team_id: record.team.team_id,
        name: record.team.name,
        wins: record.wins,
        losses: record.losses,
        ties: record.ties,
        win_percentage: winPercentage(record.wins, record.losses, record.ties),
        head_to_head_percentage: percentage ?? null,
        points_for: record.team.points_for,
        current_rank: record.currentRank,
        tiebreaker:
          group.length === 1
            ? "record"
            : hasHeadToHeadDifference
              ? "head_to_head"
              : group.some(
                    (other) => other.team.points_for !== record.team.points_for,
                  )
                ? "points_for"
                : "current_standings",
      });
    }

    groupStart = groupEnd;
  }

  return standings;
}

function nextPowerOfTwo(value: number): number {
  let result = 1;

  while (result < value) {
    result *= 2;
  }

  return result;
}

function bracketEntry(standing: ProjectedStanding): ProjectedBracketEntry {
  return {
    seed: standing.seed,
    team_id: standing.team_id,
    team_name: standing.name,
    points_for: standing.points_for,
  };
}

export function buildProjectedBracket(
  standings: readonly ProjectedStanding[],
  playoffTeamCount: number,
): ProjectedBracketMatchup[] {
  const playoffTeams = standings.slice(0, playoffTeamCount);

  if (playoffTeams.length < 2) {
    return [];
  }

  const bracketSize = nextPowerOfTwo(playoffTeams.length);

  const matchups: ProjectedBracketMatchup[] = [];

  for (let firstSeed = 1; firstSeed <= bracketSize / 2; firstSeed += 1) {
    const secondSeed = bracketSize + 1 - firstSeed;

    const first = playoffTeams[firstSeed - 1];
    const second = playoffTeams[secondSeed - 1];

    if (first === undefined) {
      continue;
    }

    matchups.push({
      position: firstSeed,
      first: bracketEntry(first),
      second: second === undefined ? null : bracketEntry(second),
      is_bye: second === undefined,
    });
  }

  return matchups;
}

export function predictionsForWeek(
  matchups: readonly PlayoffPredictorMatchup[],
  predictions: PredictionSelections,
  week: number,
): PredictionSelections {
  const matchupIds = new Set(
    matchups
      .filter((matchup) => matchup.week === week)
      .map((matchup) => matchup.matchup_id),
  );

  return Object.fromEntries(
    Object.entries(predictions).filter(
      ([matchupId]) => !matchupIds.has(Number(matchupId)),
    ),
  );
}

export function remainingWeeks(snapshot: PlayoffPredictorSnapshot): number[] {
  return [
    ...new Set(snapshot.remaining_matchups.map((matchup) => matchup.week)),
  ].sort((first, second) => first - second);
}
