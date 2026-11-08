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
  analyticsQueryKeys,
  scheduleSwapQueryOptions,
} from "./analytics";


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

describe("analytics API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a stable schedule-swap query key", () => {
    expect(
      analyticsQueryKeys.scheduleSwap(1, 2),
    ).toEqual([
      "analytics",
      "schedule-swap",
      1,
      2,
    ]);
  });

  it("disables a swap between the same team", () => {
    const options =
      scheduleSwapQueryOptions(1, 1);

    expect(options.enabled).toBe(false);
  });

  it("requests both selected team IDs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        first_team: {},
        second_team: {},
        completed_weeks: [
          1,
          2,
        ],
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient();

    await queryClient.fetchQuery(
      scheduleSwapQueryOptions(1, 2),
    );

    expect(fetchMock).toHaveBeenCalledOnce();

    const [requestedUrl, options] =
      fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain(
      "/analytics/schedule-swap",
    );
    expect(String(requestedUrl)).toContain(
      "first_team_id=1",
    );
    expect(String(requestedUrl)).toContain(
      "second_team_id=2",
    );
    expect(options).toMatchObject({
      method: "GET",
      credentials: "include",
    });
  });
});


describe("league average API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a stable league-average query key", async () => {
    const { analyticsQueryKeys } =
      await import("./analytics");

    expect(
      analyticsQueryKeys.leagueAverage(),
    ).toEqual([
      "analytics",
      "league-average",
    ]);
  });

  it("requests league-average standings", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse([]),
    );

    vi.stubGlobal("fetch", fetchMock);

    const {
      leagueAverageQueryOptions,
    } = await import("./analytics");

    const queryClient = new QueryClient();

    await queryClient.fetchQuery(
      leagueAverageQueryOptions(),
    );

    expect(fetchMock).toHaveBeenCalledOnce();

    const [requestedUrl, options] =
      fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain(
      "/analytics/league-average",
    );

    expect(options).toMatchObject({
      method: "GET",
      credentials: "include",
    });
  });
});


describe("optimal-lineup API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates stable optimal-lineup query keys", async () => {
    const { analyticsQueryKeys } =
      await import("./analytics");

    expect(
      analyticsQueryKeys.optimalLineups(),
    ).toEqual([
      "analytics",
      "optimal-lineups",
    ]);

    expect(
      analyticsQueryKeys.optimalLineupTeam(4),
    ).toEqual([
      "analytics",
      "optimal-lineups",
      4,
    ]);
  });

  it("requests the manager efficiency ranking", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse([]),
    );

    vi.stubGlobal("fetch", fetchMock);

    const {
      optimalLineupsQueryOptions,
    } = await import("./analytics");

    const queryClient = new QueryClient();

    await queryClient.fetchQuery(
      optimalLineupsQueryOptions(),
    );

    const [requestedUrl, options] =
      fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain(
      "/analytics/optimal-lineups",
    );

    expect(options).toMatchObject({
      method: "GET",
      credentials: "include",
    });
  });

  it("requests one team by ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        team_id: 4,
        team_name: "Kamara Sutra",
        weekly_results: [],
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const {
      teamOptimalLineupQueryOptions,
    } = await import("./analytics");

    const queryClient = new QueryClient();

    await queryClient.fetchQuery(
      teamOptimalLineupQueryOptions(4),
    );

    const [requestedUrl] =
      fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain(
      "/analytics/optimal-lineups/4",
    );
  });
});
