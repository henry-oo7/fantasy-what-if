import { describe, expect, it } from "vitest";

import { season, twoLeagues } from "../test/personalFixtures";
import type {
  PersonalLeagues,
  SeasonAccessStatus,
  SeasonSyncState,
} from "../types/personal";
import { describeSeason, landingSeasonId } from "./seasonState";

const SYNC_STATES: (SeasonSyncState | null)[] = [
  "never_synced",
  "queued",
  "syncing",
  "fresh",
  "stale",
  "failed",
  null,
];
const ACCESS: SeasonAccessStatus[] = ["active", "reverification_required"];

function view(
  access: SeasonAccessStatus,
  sync: SeasonSyncState | null,
  hasData: boolean,
) {
  return describeSeason(
    season(1, 2025, { access_status: access, sync_state: sync, data_available: hasData }),
  );
}

describe("describeSeason without data", () => {
  it.each(SYNC_STATES)("reconnect wins over sync state %s", (sync) => {
    const result = view("reverification_required", sync, false);

    expect(result.chip).toBe("Reconnect needed");
    expect(result.notice).toBeNull();
    expect(result.unavailable?.kind).toBe("reconnect");
    expect(result.unavailable?.title).toBe("Reconnect this League");
  });

  it("reports a failed import", () => {
    const result = view("active", "failed", false);

    expect(result.chip).toBe("Import failed");
    expect(result.notice).toBeNull();
    expect(result.unavailable?.kind).toBe("failed");
  });

  it.each(["queued", "syncing"] as const)("reports %s as importing", (sync) => {
    const result = view("active", sync, false);

    expect(result.chip).toBe("Importing");
    expect(result.notice).toBeNull();
    expect(result.unavailable?.kind).toBe("importing");
    expect(result.unavailable?.title).toBe("Importing your League");
  });

  it.each(["never_synced", null] as const)("reports %s as preparing", (sync) => {
    const result = view("active", sync, false);

    expect(result.chip).toBe("Importing");
    expect(result.notice).toBeNull();
    expect(result.unavailable?.kind).toBe("importing");
    expect(result.unavailable?.title).toBe("Preparing your League data");
  });
});

describe("describeSeason with data", () => {
  it("shows nothing for fresh data", () => {
    expect(view("active", "fresh", true)).toEqual({ chip: null, notice: null, unavailable: null });
  });

  it("shows nothing when the sync state is unknown", () => {
    expect(view("active", null, true)).toEqual({ chip: null, notice: null, unavailable: null });
  });

  it("warns about stale data", () => {
    const result = view("active", "stale", true);

    expect(result.chip).toBe("Out of date");
    expect(result.notice).toEqual({ tone: "warning", text: "This data may be out of date." });
    expect(result.unavailable).toBeNull();
  });

  it.each(["queued", "syncing"] as const)("shows an info refresh notice for %s", (sync) => {
    const result = view("active", sync, true);

    expect(result.chip).toBe("Refreshing");
    expect(result.notice?.tone).toBe("info");
    expect(result.notice?.text).toContain("Refreshing League data");
    expect(result.notice?.text).toContain("last saved data");
    expect(result.unavailable).toBeNull();
  });

  it("warns when the latest refresh failed", () => {
    const result = view("active", "failed", true);

    expect(result.chip).toBe("Refresh failed");
    expect(result.notice?.tone).toBe("warning");
    expect(result.notice?.text).toContain("did not finish");
    expect(result.notice?.text).toContain("last saved data");
    expect(result.unavailable).toBeNull();
  });

  it.each(["fresh", "stale", "queued", "syncing", "failed", "never_synced", null] as const)(
    "keeps saved data and asks to reconnect when reverification is required (sync %s)",
    (sync) => {
      const result = view("reverification_required", sync, true);

      expect(result.chip).toBe("Reconnect needed");
      expect(result.unavailable).toBeNull();
      expect(result.notice?.tone).toBe("warning");
      expect(result.notice?.text).toContain("Import this League and Season again to reconnect");
      expect(result.notice?.text).toContain("Your saved data is still shown");
    },
  );

  it("appends the refresh text after the reconnect text", () => {
    const text = view("reverification_required", "failed", true).notice?.text ?? "";

    expect(text).toContain("did not finish");
    expect(text.indexOf("Your saved data is still shown")).toBeLessThan(text.indexOf("did not finish"));
  });
});

describe("describeSeason copy safety", () => {
  const combinations = ACCESS.flatMap((access) =>
    SYNC_STATES.flatMap((sync) =>
      [true, false].map((hasData) => [access, sync, hasData] as const),
    ),
  );

  it.each(combinations)("never leaks internal terms (%s, %s, data %s)", (access, sync, hasData) => {
    const result = view(access, sync, hasData);
    const strings = [
      result.chip,
      result.notice?.text,
      result.unavailable?.title,
      result.unavailable?.text,
    ].filter((value): value is string => typeof value === "string");

    for (const text of strings) {
      expect(text).not.toMatch(/connection|credential|owner|error code/i);
      expect(text).not.toMatch(
        /never_synced|reverification_required|data_available|queued|syncing/,
      );
    }
  });
});

describe("landingSeasonId", () => {
  it("returns the default Season when it is readable", () => {
    expect(landingSeasonId(twoLeagues({ default_season_id: 10 }))).toBe(10);
    expect(landingSeasonId(twoLeagues({ default_season_id: 21 }))).toBe(21);
  });

  it("ignores a stale default and picks the newest year, first on a tie", () => {
    expect(landingSeasonId(twoLeagues({ default_season_id: 999 }))).toBe(11);
    expect(landingSeasonId(twoLeagues({ default_season_id: null }))).toBe(11);
  });

  it("picks a strictly newer year over an earlier League", () => {
    const leagues: PersonalLeagues = twoLeagues({ default_season_id: null });
    leagues.leagues[1].seasons = [season(21, 2026)];

    expect(landingSeasonId(leagues)).toBe(21);
  });

  it("returns null when there are no Seasons", () => {
    expect(landingSeasonId({ default_season_id: null, leagues: [] })).toBeNull();
    expect(landingSeasonId({ default_season_id: 5, leagues: [] })).toBeNull();
  });

  it("only returns ids present in the input", () => {
    for (const defaultId of [null, 11, 10, 21, 404]) {
      const leagues = twoLeagues({ default_season_id: defaultId });
      const ids = leagues.leagues.flatMap((league) => league.seasons.map((item) => item.season_id));

      expect(ids).toContain(landingSeasonId(leagues));
    }
  });
});
