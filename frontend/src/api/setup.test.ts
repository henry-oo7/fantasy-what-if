import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  bootstrapAdministrator,
  createProviderConnection,
  setupQueryKeys,
  setupStatusQueryOptions,
} from "./setup";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
    },
  });
}

describe("setup API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a stable setup status query key", () => {
    expect(setupQueryKeys.status()).toEqual(["setup", "status"]);
  });

  it("requests the public installation status", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        status: "setup_required",
        installation_exists: false,
        bootstrap_available: true,
        administrator_configured: false,
        provider_configured: false,
        spectator_configured: false,
        active_league_configured: false,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient();

    const result = await queryClient.fetchQuery(setupStatusQueryOptions());

    expect(result).toEqual({
      status: "setup_required",
      installation_exists: false,
      bootstrap_available: true,
      administrator_configured: false,
      provider_configured: false,
      spectator_configured: false,
      active_league_configured: false,
    });

    const [requestedUrl, options] = fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain("/api/setup/status");

    expect(options).toMatchObject({
      method: "GET",
      credentials: "include",
    });
  });

  it("submits administrator bootstrap with CSRF", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        installation_status: "setup_in_progress",
        administrator_id: 1,
        username: "setup-admin",
        display_name: "Setup Administrator",
        email: "admin@example.com",
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    await bootstrapAdministrator(
      {
        username: "setup-admin",
        display_name: "Setup Administrator",
        email: "admin@example.com",
        password: "correct horse battery staple",
      },
      "pre-auth-csrf",
    );

    const [requestedUrl, options] = fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain("/api/setup/bootstrap-admin");

    expect(options).toMatchObject({
      method: "POST",
      credentials: "include",
    });

    expect(new Headers(options.headers).get("X-CSRF-Token")).toBe(
      "pre-auth-csrf",
    );

    expect(JSON.parse(String(options.body))).toEqual({
      username: "setup-admin",
      display_name: "Setup Administrator",
      email: "admin@example.com",
      password: "correct horse battery staple",
    });
  });

  it("submits provider credentials without a manual league name", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        connection_id: 1,
        provider: "espn",
        display_name: "ESPN Fantasy Football",
        credential_version: 1,
        is_active: true,
        validated: true,
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    await createProviderConnection(
      {
        provider: "espn",
        league_id: "529250970",
        season: "2026",
        espn_s2: "private-cookie",
        swid: "{private-swid}",
      },
      "session-csrf",
    );

    const [requestedUrl, options] = fetchMock.mock.calls[0];

    expect(String(requestedUrl)).toContain("/api/setup/provider-connection");

    expect(JSON.parse(String(options.body))).toEqual({
      provider: "espn",
      league_id: "529250970",
      season: "2026",
      espn_s2: "private-cookie",
      swid: "{private-swid}",
    });

    expect(JSON.parse(String(options.body))).not.toHaveProperty("display_name");
  });
});
