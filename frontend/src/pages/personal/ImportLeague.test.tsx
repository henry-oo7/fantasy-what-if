import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { AppRouter } from "../../router";
import {
  PERSONAL_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
} from "../../test/accountHarness";
import { seasonDetail, snapshot, twoLeagues } from "../../test/personalFixtures";

const SWID = "SYNTHETIC-SWID-0001";
const ESPN_S2 = "synthetic-espn-s2-cookie-value";

const IMPORT_RESPONSE = {
  provider: "espn",
  provider_league_id: "1001",
  league_name: "Alpha League",
  season_id: 11,
  season_year: 2025,
  access_status: "active",
  sync_state: "queued",
  data_available: false,
  sync_queued: true,
};

function setup(importReply?: () => Response | Promise<Response>) {
  const api = createApiMock();
  signInAs(api, { ...PERSONAL_USER, navigation_state: "import_required" });
  api.reply("GET /api/leagues", 200, { default_season_id: null, leagues: [] });
  api.on("POST /api/league-imports", importReply ?? (() => jsonResponse(200, IMPORT_RESPONSE)));
  document.cookie = "fantasy_session_csrf=synthetic-session-csrf; path=/";

  return api;
}

afterEach(() => {
  document.cookie = "fantasy_session_csrf=; path=/; max-age=0";
});

async function fillTarget(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("ESPN League ID"), "1001");
  await user.type(screen.getByLabelText("Season year"), "2025");
}

async function fillCredentials(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("checkbox", { name: "This is a private League" }));
  await user.type(screen.getByLabelText("SWID"), SWID);
  await user.type(screen.getByLabelText("espn_s2"), ESPN_S2);
}

describe("League import", () => {
  it("imports a public League, refreshes the League list and opens the imported Season", async () => {
    const api = setup();
    const leagues = twoLeagues();
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/import");
    await fillTarget(user);
    // After the import, the account is past import_required.
    api.on("POST /api/league-imports", () => {
      signInAs(api, PERSONAL_USER);
      api.reply("GET /api/leagues", 200, leagues);
      api.reply("GET /api/seasons/11", 200, {
        ...seasonDetail(leagues, 11),
        sync_state: "queued",
        data_available: false,
      });

      return jsonResponse(200, IMPORT_RESPONSE);
    });
    await user.click(screen.getByRole("button", { name: "Import League" }));

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/11");
    });
    expect(api.calls("POST /api/league-imports")[0].body).toEqual({
      provider: "espn",
      provider_league_id: "1001",
      season_year: 2025,
    });
    expect(api.calls("POST /api/league-imports")[0].headers.get("X-CSRF-Token")).toBe(
      "synthetic-session-csrf",
    );
    // Queued, no data yet: honest importing state, not empty analytics.
    expect(await screen.findByRole("heading", { name: "Importing your League" })).toBeInTheDocument();
    expect(api.calls("GET /api/seasons/11/snapshot")).toHaveLength(0);
  });

  it("sends private credentials only in the request body and never persists them", async () => {
    const api = setup();
    const leagues = twoLeagues();
    api.on("POST /api/league-imports", () => {
      signInAs(api, PERSONAL_USER);
      api.reply("GET /api/leagues", 200, leagues);
      api.reply("GET /api/seasons/11", 200, seasonDetail(leagues, 11));
      api.reply("GET /api/seasons/11/snapshot", 200, snapshot("Alpha League", 2025));

      return jsonResponse(200, { ...IMPORT_RESPONSE, sync_state: "fresh", data_available: true });
    });
    const user = userEvent.setup();

    const { queryClient } = renderWithProviders(<AppRouter />, "/account/import");
    await fillTarget(user);
    await fillCredentials(user);
    await user.click(screen.getByRole("button", { name: "Import League" }));

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/11");
    });
    expect(api.calls("POST /api/league-imports")[0].body).toEqual({
      provider: "espn",
      provider_league_id: "1001",
      season_year: 2025,
      credentials: { swid: SWID, espn_s2: ESPN_S2 },
    });

    const everything = JSON.stringify([
      screen.getByTestId("location").textContent,
      window.location.href,
      { ...window.localStorage },
      { ...window.sessionStorage },
      window.history.state,
      document.cookie,
      queryClient.getMutationCache().getAll().map((mutation) => mutation.state.variables),
      queryClient.getQueryCache().getAll().map((query) => [query.queryKey, query.state.data]),
      api.requests.filter((request) => request.path !== "/api/league-imports").map((request) => [request.search, request.body]),
    ]);
    expect(everything).not.toContain("SYNTHETIC-SWID");
    expect(everything).not.toContain(ESPN_S2);
    expect(document.body.innerHTML).not.toContain(ESPN_S2);
  });

  it("clears credentials after a failed proof and shows only the safe message", async () => {
    setup(() =>
      jsonResponse(422, {
        detail: "ESPN did not accept these private league credentials. Check SWID and espn_s2 and try again.",
        code: "provider_authentication_failed",
        retryable: false,
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/import");
    await fillTarget(user);
    await fillCredentials(user);
    await user.click(screen.getByRole("button", { name: "Import League" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/did not accept these private league credentials/);
    expect(screen.getByLabelText("SWID")).toHaveValue("");
    expect(screen.getByLabelText("espn_s2")).toHaveValue("");
    // The target stays so the user can retry.
    expect(screen.getByLabelText("ESPN League ID")).toHaveValue("1001");
    expect(screen.getByTestId("location")).toHaveTextContent("/account/import");
  });

  it("never shows raw provider text for an uncoded or unexpected error", async () => {
    setup(() => jsonResponse(500, { detail: "ESPN stack trace: boom swid=abc" }));
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/import");
    await fillTarget(user);
    await user.click(screen.getByRole("button", { name: "Import League" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The League could not be imported. Try again.");
    expect(alert).not.toHaveTextContent(/stack trace|swid/i);
  });

  it("reveals the private-League fields when the server asks for credentials", async () => {
    setup(() =>
      jsonResponse(422, {
        detail: "ESPN requires private league credentials. Add SWID and espn_s2 and try again.",
        code: "provider_credentials_required",
        retryable: false,
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/import");
    await fillTarget(user);
    await user.click(screen.getByRole("button", { name: "Import League" }));

    expect(await screen.findByLabelText("SWID")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "This is a private League" })).toBeChecked();
  });

  it("does not submit a second import while one is pending", async () => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const api = setup(async () => {
      await gate;

      return jsonResponse(503, { detail: "The league could not be imported right now. Try again in a moment.", code: "league_import_unavailable", retryable: true });
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/import");
    await fillTarget(user);
    const button = screen.getByRole("button", { name: "Import League" });
    await user.click(button);
    await user.click(screen.getByRole("button", { name: /Checking and importing/ }));
    await user.keyboard("{Enter}");

    expect(api.calls("POST /api/league-imports")).toHaveLength(1);
    release?.();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // After a failure the retry path is open again.
    expect(screen.getByRole("button", { name: "Import League" })).toBeEnabled();
  });

  it("clears both credential fields together and when the private toggle is turned off", async () => {
    setup();
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/import");
    await fillTarget(user);
    await fillCredentials(user);
    await user.click(screen.getByRole("button", { name: "Clear SWID and espn_s2" }));
    expect(screen.getByLabelText("SWID")).toHaveValue("");
    expect(screen.getByLabelText("espn_s2")).toHaveValue("");

    await user.type(screen.getByLabelText("SWID"), SWID);
    await user.click(screen.getByRole("checkbox", { name: "This is a private League" }));
    await user.click(screen.getByRole("checkbox", { name: "This is a private League" }));
    expect(screen.getByLabelText("SWID")).toHaveValue("");
  });

  it("requires both credentials together and associates errors with their fields", async () => {
    const api = setup();
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/account/import");
    await fillTarget(user);
    await user.click(screen.getByRole("checkbox", { name: "This is a private League" }));
    await user.type(screen.getByLabelText("SWID"), SWID);
    await user.click(screen.getByRole("button", { name: "Import League" }));

    const missing = screen.getByLabelText("espn_s2");
    expect(missing).toHaveAttribute("aria-invalid", "true");
    expect(missing.getAttribute("aria-describedby")).toContain("import-espn-s2-error");
    expect(api.calls("POST /api/league-imports")).toHaveLength(0);
  });

  it("offers only ESPN", async () => {
    setup();

    renderWithProviders(<AppRouter />, "/account/import");
    await screen.findByLabelText("ESPN League ID");

    expect(screen.queryByText(/Sleeper|Yahoo/)).not.toBeInTheDocument();
  });
});
