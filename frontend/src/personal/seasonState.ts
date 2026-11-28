import type { PersonalLeagues, PersonalSeason } from "../types/personal";

export type SeasonNoticeTone = "info" | "warning";

/** What the Season layout shows. Text always carries the meaning; tone only styles it. */
export interface SeasonStateView {
  /** Short status chip text; null when nothing needs saying. */
  chip: string | null;
  /** Banner above available data; null when the data is simply current. */
  notice: { tone: SeasonNoticeTone; text: string } | null;
  /** Replaces the analytics when no data exists. */
  unavailable:
    | { kind: "importing" | "failed" | "reconnect"; title: string; text: string }
    | null;
}

const RECONNECT_TEXT =
  "We could not recently confirm your access to this League. Import this League and Season again to reconnect.";

/** Pure mapping from the safe backend fields to presentation. Never shows raw codes. */
export function describeSeason(season: PersonalSeason): SeasonStateView {
  const { access_status: access, sync_state: sync, data_available: hasData } = season;
  const reconnect = access === "reverification_required";

  if (!hasData) {
    if (reconnect) {
      return {
        chip: "Reconnect needed",
        notice: null,
        unavailable: {
          kind: "reconnect",
          title: "Reconnect this League",
          text: RECONNECT_TEXT,
        },
      };
    }

    if (sync === "failed") {
      return {
        chip: "Import failed",
        notice: null,
        unavailable: {
          kind: "failed",
          title: "This import did not finish",
          text: "Nothing was loaded for this Season. Import it again to retry.",
        },
      };
    }

    return {
      chip: "Importing",
      notice: null,
      unavailable: {
        kind: "importing",
        title: sync === "never_synced" || sync === null ? "Preparing your League data" : "Importing your League",
        text: "League data is not available yet. This page updates when the import finishes.",
      },
    };
  }

  const refresh =
    sync === "queued" || sync === "syncing"
      ? "Refreshing League data. Showing the last saved data."
      : sync === "failed"
        ? "The latest refresh did not finish. Showing the last saved data."
        : sync === "stale"
          ? "This data may be out of date."
          : null;
  const chip =
    sync === "queued" || sync === "syncing"
      ? "Refreshing"
      : sync === "failed"
        ? "Refresh failed"
        : sync === "stale"
          ? "Out of date"
          : null;

  if (reconnect) {
    return {
      chip: "Reconnect needed",
      notice: {
        tone: "warning",
        text: `${RECONNECT_TEXT} Your saved data is still shown.${refresh ? ` ${refresh}` : ""}`,
      },
      unavailable: null,
    };
  }

  return {
    chip,
    notice: refresh
      ? { tone: sync === "queued" || sync === "syncing" ? "info" : "warning", text: refresh }
      : null,
    unavailable: null,
  };
}

/**
 * The Season to open when no Season is named: the stored default if still
 * readable, otherwise the newest year (first in backend order on a tie).
 * Never invents access: only Seasons in the response are considered.
 */
export function landingSeasonId(leagues: PersonalLeagues): number | null {
  const all = leagues.leagues.flatMap((league) => league.seasons);

  if (
    leagues.default_season_id !== null &&
    all.some((season) => season.season_id === leagues.default_season_id)
  ) {
    return leagues.default_season_id;
  }

  let best: PersonalSeason | null = null;

  for (const season of all) {
    if (best === null || season.season_year > best.season_year) {
      best = season;
    }
  }

  return best?.season_id ?? null;
}
