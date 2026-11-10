import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  importSeason,
  importedSeasonsQueryOptions,
  leagueQueryKeys,
} from "./league";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

describe("league API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a stable season inventory key", () => {
    expect(leagueQueryKeys.seasons()).toEqual(["league", "seasons"]);
  });

  it("loads only imported seasons", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        seasons: [
          {
            season_id: 1,
            league_id: 1,
            league_name: "Jimmy and Friends",
            year: 2026,
            status: "active",
            is_active: true,
            last_synced_at: "2026-11-09T16:42:46Z",
          },
        ],
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient();

    const result = await queryClient.fetchQuery(importedSeasonsQueryOptions());

    expect(result.seasons).toEqual([
      {
        season_id: 1,
        league_id: 1,
        league_name: "Jimmy and Friends",
        year: 2026,
        status: "active",
        is_active: true,
        last_synced_at: "2026-11-09T16:42:46Z",
      },
    ]);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/leagues/seasons"),
      expect.objectContaining({
        method: "GET",
        credentials: "include",
      }),
    );
  });

  it("accepts multiple historical seasons", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        seasons: [
          {
            season_id: 2,
            league_id: 1,
            league_name: "Jimmy and Friends",
            year: 2026,
            status: "active",
            is_active: true,
            last_synced_at: null,
          },
          {
            season_id: 1,
            league_id: 1,
            league_name: "Jimmy and Friends",
            year: 2025,
            status: "completed",
            is_active: false,
            last_synced_at: null,
          },
        ],
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient();

    const result = await queryClient.fetchQuery(importedSeasonsQueryOptions());

    expect(result.seasons.map((season) => season.year)).toEqual([2026, 2025]);

    expect(result.seasons.filter((season) => season.is_active)).toHaveLength(1);
  });

  it("imports another season with CSRF", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        season_id: 25,
        league_id: 10,
        year: 2025,
        status: "completed",
        synchronization_enabled: false,
        teams_upserted: 12,
        matchups_upserted: 84,
        fantasy_byes_upserted: 0,
        player_week_scores_upserted: 1344,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    await importSeason(
      {
        year: 2025,
      },
      "session-csrf",
    );

    const [requestedUrl, options] = fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain("/api/leagues/seasons/import");

    expect(options).toMatchObject({
      method: "POST",
      credentials: "include",
    });

    expect(new Headers(options.headers).get("X-CSRF-Token")).toBe(
      "session-csrf",
    );

    expect(JSON.parse(String(options.body))).toEqual({
      year: 2025,
    });
  });
});
