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
  getBackgroundJob,
  pollBackgroundJob,
  resetSpectatorPassword,
  replaceProviderCredentials,
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

  it("accepts a full synchronization job", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          job_id: 84,
          status: "pending",
          existing: false,
        }),
        {
          status: 202,
          headers: {
            "Content-Type": "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result = await runSynchronization(
      true,
      "session-csrf",
    );

    expect(result).toEqual({
      job_id: 84,
      status: "pending",
      existing: false,
    });

    const [requestedUrl, options] =
      fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain(
      "/api/admin/sync",
    );

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
      full_sync: true,
    });
  });

  it("reads a workspace-scoped background job", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        job_id: 84,
        job_type: "full_season_sync",
        status: "running",
        attempt_count: 1,
        maximum_attempts: 5,
        progress: {
          stage: "fetching_matchups",
        },
        available_at: "2026-11-19T18:00:00Z",
        started_at: "2026-11-19T18:00:02Z",
        completed_at: null,
        failed_at: null,
        canceled_at: null,
        safe_error_code: null,
        safe_error_message: null,
        retryable: null,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const job = await getBackgroundJob(84);

    expect(job.status).toBe("running");
    expect(job.progress?.stage).toBe(
      "fetching_matchups",
    );

    const [requestedUrl, options] =
      fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain(
      "/api/admin/jobs/84",
    );
    expect(options).toMatchObject({
      method: "GET",
      credentials: "include",
    });
  });

  it("polls until the persisted job succeeds", async () => {
    vi.useFakeTimers();

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          job_id: 84,
          job_type: "full_season_sync",
          status: "running",
          attempt_count: 1,
          maximum_attempts: 5,
          progress: null,
          available_at: "2026-11-19T18:00:00Z",
          started_at: "2026-11-19T18:00:02Z",
          completed_at: null,
          failed_at: null,
          canceled_at: null,
          safe_error_code: null,
          safe_error_message: null,
          retryable: null,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          job_id: 84,
          job_type: "full_season_sync",
          status: "succeeded",
          attempt_count: 1,
          maximum_attempts: 5,
          progress: null,
          available_at: "2026-11-19T18:00:00Z",
          started_at: "2026-11-19T18:00:02Z",
          completed_at: "2026-11-19T18:01:00Z",
          failed_at: null,
          canceled_at: null,
          safe_error_code: null,
          safe_error_message: null,
          retryable: null,
        }),
      );

    vi.stubGlobal("fetch", fetchMock);

    const resultPromise = pollBackgroundJob(
      84,
      {
        intervalMs: 10,
        timeoutMs: 100,
      },
    );

    await vi.advanceTimersByTimeAsync(10);

    await expect(resultPromise).resolves.toEqual(
      expect.objectContaining({
        job_id: 84,
        status: "succeeded",
      }),
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);

    vi.useRealTimers();
  });

  it("tolerates a temporary polling failure", async () => {
    vi.useFakeTimers();

    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(
        new TypeError("Temporary network failure"),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          job_id: 42,
          job_type: "incremental_season_sync",
          status: "succeeded",
          attempt_count: 1,
          maximum_attempts: 5,
          progress: null,
          available_at: "2026-11-19T18:00:00Z",
          started_at: "2026-11-19T18:00:02Z",
          completed_at: "2026-11-19T18:00:30Z",
          failed_at: null,
          canceled_at: null,
          safe_error_code: null,
          safe_error_message: null,
          retryable: null,
        }),
      );

    vi.stubGlobal("fetch", fetchMock);

    const resultPromise = pollBackgroundJob(
      42,
      {
        intervalMs: 10,
        timeoutMs: 100,
      },
    );

    await vi.advanceTimersByTimeAsync(10);

    await expect(resultPromise).resolves.toEqual(
      expect.objectContaining({
        status: "succeeded",
      }),
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);

    vi.useRealTimers();
  });


  it("replaces provider credentials through the protected admin route", async () => {
    const response = {
      workspace_id: 1,
      connection_id: 30,
      provider: "espn",
      provider_league_id: "529250970",
      provider_season_id: "2026",
      credential_version: 1,
      credential_schema_version: 1,
      validated_at: "2026-11-20T15:00:00Z",
    };

    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(response),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result = await replaceProviderCredentials(
      {
        espn_s2: "replacement-cookie",
        swid: "{replacement-swid}",
      },
      "csrf-token",
    );

    expect(fetchMock).toHaveBeenCalledOnce();

    const [url, options] = fetchMock.mock.calls[0];
    const headers = new Headers(options.headers);

    expect(url).toBe("/api/admin/provider-credentials");
    expect(options).toMatchObject({
      method: "PATCH",
      credentials: "include",
      body: JSON.stringify({
        espn_s2: "replacement-cookie",
        swid: "{replacement-swid}",
      }),
    });
    expect(headers.get("Content-Type")).toBe(
      "application/json",
    );
    expect(headers.get("X-CSRF-Token")).toBe(
      "csrf-token",
    );

    expect(result).toEqual(response);
    expect(result).not.toHaveProperty("espn_s2");
    expect(result).not.toHaveProperty("swid");
    expect(result).not.toHaveProperty("encrypted_credentials");
  });

});
