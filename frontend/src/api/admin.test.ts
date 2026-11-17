import {
  QueryClient,
} from "@tanstack/react-query";
import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  resetSpectatorPassword,
  runSynchronization,
  setSpectatorAccess,
  setStandingsTiebreakerFallback,
  spectatorQueryOptions,
  standingsTiebreakerQueryOptions,
  syncStatusQueryOptions,
} from "./admin";


function jsonResponse(
  body: unknown,
): Response {
  return new Response(
    JSON.stringify(body),
    {
      status: 200,
      headers: {
        "Content-Type": "application/json",
      },
    },
  );
}


describe("admin API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads spectator account status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        configured: true,
        id: 2,
        username: "league-viewer",
        display_name: "League Spectator",
        email: null,
        is_active: true,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient();

    const result = await queryClient.fetchQuery(
      spectatorQueryOptions(),
    );

    expect(result.username).toBe(
      "league-viewer",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/admin/spectator",
      ),
      expect.objectContaining({
        method: "GET",
        credentials: "include",
      }),
    );
  });

  it("loads provider synchronization status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        provider: "espn",
        season_id: 1,
        provider_league_id: "529250970",
        provider_season_id: "2026",
        last_synced_at: null,
        latest_run: null,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient();

    const result = await queryClient.fetchQuery(
      syncStatusQueryOptions(),
    );

    expect(result.provider).toBe("espn");
    expect(result.provider_season_id).toBe(
      "2026",
    );
  });

  it("loads standings tiebreaker configuration", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        season_id: 1,
        provider_rule: "TOTAL_POINTS_SCORED",
        provider_rule_supported: true,
        admin_fallback_rule: null,
        effective_rule: "TOTAL_POINTS_SCORED",
        effective_rule_source: "provider",
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient();

    const result = await queryClient.fetchQuery(
      standingsTiebreakerQueryOptions(),
    );

    expect(result.effective_rule).toBe(
      "TOTAL_POINTS_SCORED",
    );
    expect(result.effective_rule_source).toBe(
      "provider",
    );
  });

  it("updates the standings tiebreaker fallback with CSRF", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        season_id: 1,
        provider_rule: "TOTAL_POINTS_SCORED",
        provider_rule_supported: true,
        admin_fallback_rule: "H2H_RECORD",
        effective_rule: "TOTAL_POINTS_SCORED",
        effective_rule_source: "provider",
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    await setStandingsTiebreakerFallback(
      "H2H_RECORD",
      "session-csrf",
    );

    const [, options] =
      fetchMock.mock.calls[0];

    expect(options).toMatchObject({
      method: "PATCH",
      credentials: "include",
    });

    expect(
      new Headers(options.headers).get(
        "X-CSRF-Token",
      ),
    ).toBe("session-csrf");

    expect(
      JSON.parse(String(options.body)),
    ).toEqual({
      fallback_rule: "H2H_RECORD",
    });
  });

  it("resets the spectator password with CSRF", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        configured: true,
        id: 2,
        username: "league-viewer",
        display_name: "League Spectator",
        email: null,
        is_active: true,
        revoked_sessions: 3,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    await resetSpectatorPassword(
      "new shared spectator password",
      "session-csrf",
    );

    const [, options] =
      fetchMock.mock.calls[0];

    expect(options).toMatchObject({
      method: "POST",
      credentials: "include",
    });

    expect(
      new Headers(options.headers).get(
        "X-CSRF-Token",
      ),
    ).toBe("session-csrf");

    expect(
      JSON.parse(String(options.body)),
    ).toEqual({
      password:
        "new shared spectator password",
    });
  });

  it("changes spectator access with PATCH", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        configured: true,
        id: 2,
        username: "league-viewer",
        display_name: "League Spectator",
        email: null,
        is_active: false,
        revoked_sessions: 2,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    await setSpectatorAccess(
      false,
      "session-csrf",
    );

    const [, options] =
      fetchMock.mock.calls[0];

    expect(options).toMatchObject({
      method: "PATCH",
      credentials: "include",
    });

    expect(
      JSON.parse(String(options.body)),
    ).toEqual({
      is_active: false,
    });
  });

  it("runs a full synchronization", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        provider: "espn",
        season_id: 1,
        provider_league_id: "529250970",
        provider_season_id: "2026",
        current_week: 9,
        full_sync: true,
        player_weeks: [
          1,
          2,
          3,
          4,
          5,
          6,
          7,
          8,
        ],
        teams_upserted: 12,
        matchups_upserted: 72,
        fantasy_byes_upserted: 0,
        player_week_scores_upserted: 1579,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result = await runSynchronization(
      true,
      "session-csrf",
    );

    expect(result.full_sync).toBe(true);

    const [, options] =
      fetchMock.mock.calls[0];

    expect(
      JSON.parse(String(options.body)),
    ).toEqual({
      full_sync: true,
    });
  });
});
