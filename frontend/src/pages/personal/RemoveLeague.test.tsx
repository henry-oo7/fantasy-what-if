import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { personalQueryKeys } from "../../api/personalLeagues";
import { CONNECTION_ERROR_MESSAGE } from "../../auth/accountMessages";
import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  PERSONAL_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  type ApiMock,
} from "../../test/accountHarness";
import { mockPersonalApi } from "../../test/personalApi";
import { seasonDetail, twoLeagues } from "../../test/personalFixtures";
import type { PersonalLeagues } from "../../types/personal";

const ROUTE_ALPHA = "POST /api/leagues/espn/1001/removal";
const SESSION_CSRF = "synthetic-session-csrf";
const REMOVED_BODY = { detail: "League access removed.", code: "league_access_removed" };
const DATA = /Alpha League 2025 Rockets/;
const GENERIC_FAILURE = "The League could not be removed. Try again.";

beforeEach(() => {
  document.cookie = `fantasy_session_csrf=${SESSION_CSRF}; path=/`;
});

afterEach(() => {
  document.cookie =
    "fantasy_session_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  vi.restoreAllMocks();
});

function location() {
  return screen.getByTestId("location").textContent;
}

function BackButton() {
  const navigate = useNavigate();

  return (
    <button type="button" onClick={() => void navigate(-1)}>
      history back
    </button>
  );
}

function renderSettings(api: ApiMock = mockPersonalApi(), path = "/account/settings") {
  const view = renderWithProviders(
    <>
      <AppRouter />
      <BackButton />
    </>,
    path,
  );

  return { api, ...view };
}

function alphaOnly(): PersonalLeagues {
  const both = twoLeagues();

  return { ...both, leagues: [both.leagues[0]] };
}

function betaOnly(): PersonalLeagues {
  const both = twoLeagues();

  return { default_season_id: 21, leagues: [both.leagues[1]] };
}

const NO_LEAGUES: PersonalLeagues = { default_season_id: null, leagues: [] };

/** Make the removal succeed and flip the refetched account state. */
function removalSucceeds(api: ApiMock, remaining: PersonalLeagues, importRequired = false) {
  api.on(ROUTE_ALPHA, () => {
    api.reply("GET /api/leagues", 200, remaining);

    if (importRequired) {
      api.reply("GET /api/auth/me", 200, {
        ...PERSONAL_USER,
        navigation_state: "import_required",
      });
    }

    return jsonResponse(200, REMOVED_BODY);
  });
}

async function openAlphaForm() {
  const user = userEvent.setup();

  await user.click(await screen.findByRole("button", { name: "Remove Alpha League" }));

  const form = screen.getByRole("form", { name: "Remove Alpha League" });
  const input = within(form).getByLabelText("Type Alpha League to confirm");
  const submit = within(form).getByRole("button", { name: "Remove League" });

  return { user, form, input, submit };
}

async function removeAlpha(typed = "Alpha League") {
  const opened = await openAlphaForm();

  await opened.user.type(opened.input, typed);
  await opened.user.click(opened.submit);

  return opened;
}

describe("remove League section", () => {
  it("lists every League with an accessible Remove button", async () => {
    renderSettings();

    expect(
      await screen.findByRole("heading", { level: 2, name: "Remove a League from your account" }),
    ).toBeInTheDocument();

    for (const name of ["Alpha League", "Beta League"]) {
      const button = await screen.findByRole("button", { name: `Remove ${name}` });

      expect(button.tagName).toBe("BUTTON");
      expect(button).toHaveAttribute("type", "button");
      expect(button).toHaveAttribute("aria-label", `Remove ${name}`);
    }
  });

  it("describes the removal truthfully", async () => {
    renderSettings();
    const section = (
      await screen.findByRole("heading", { name: "Remove a League from your account" })
    ).closest("section") as HTMLElement;
    const text = section.textContent ?? "";

    expect(text).toMatch(/affects only this account/);
    expect(text).toMatch(/your access to it/);
    expect(text).toMatch(/ESPN\s+credentials you saved/);
    expect(text).toMatch(/deleted/);
    expect(text).toMatch(/other accounts use is not\s+affected/);
    expect(text).toMatch(/import the League again later/);

    expect(text).not.toMatch(/League data (is|will be|are) (deleted|destroyed)/i);
    expect(text).not.toMatch(/destroy/i);
    expect(text).not.toMatch(/workspace|slug|recover|restore/i);
    expect(text).not.toMatch(
      /other (accounts?|users?) (exist|still|have)|no other account|another account|last (account|user)|only (account|user)/i,
    );
  });

  it("shows the empty text when the account has no Leagues", async () => {
    renderSettings(mockPersonalApi(NO_LEAGUES));

    expect(await screen.findByText("You have no Leagues on this account.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
  });
});

describe("confirmation form", () => {
  it("opens a labelled form and enables submit only on a normalized match", async () => {
    renderSettings();
    const { user, form, input, submit } = await openAlphaForm();

    expect(form).toBeInTheDocument();
    expect(input.tagName).toBe("INPUT");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(input).not.toHaveAttribute("placeholder");
    expect(submit.tagName).toBe("BUTTON");
    expect(submit).toHaveAttribute("type", "submit");
    expect(submit).toBeDisabled();

    await user.type(input, "Alpha Leagu");
    expect(submit).toBeDisabled();

    await user.clear(input);
    await user.type(input, "  aLPHA    league ");
    expect(submit).toBeEnabled();

    await user.clear(input);
    await user.type(input, "Beta League");
    expect(submit).toBeDisabled();
  });

  it("does not submit with Enter while the name does not match", async () => {
    const api = mockPersonalApi();
    api.reply(ROUTE_ALPHA, 200, REMOVED_BODY);
    renderSettings(api);
    const { user, input } = await openAlphaForm();

    await user.type(input, "Alpha{Enter}");

    expect(api.calls(ROUTE_ALPHA)).toHaveLength(0);
  });

  it("closes without any request when the user keeps the League", async () => {
    const api = mockPersonalApi();
    renderSettings(api);
    const { user, input } = await openAlphaForm();

    await user.type(input, "Alpha League");
    await user.click(screen.getByRole("button", { name: "Keep this League" }));

    expect(screen.queryByRole("form", { name: "Remove Alpha League" })).toBeNull();
    expect(screen.queryByLabelText("Type Alpha League to confirm")).toBeNull();
    expect(screen.getByRole("button", { name: "Remove Alpha League" })).toBeInTheDocument();
    expect(api.requests.filter((request) => request.method === "POST")).toHaveLength(0);
  });
});

describe("successful removal", () => {
  it("posts the exact body and CSRF header, refetches, and prunes only that League", async () => {
    const api = mockPersonalApi();
    removalSucceeds(api, betaOnly());
    const { queryClient } = renderSettings(api);
    await screen.findByRole("button", { name: "Remove Alpha League" });

    queryClient.setQueryData(personalQueryKeys.seasonStatus(11), { season_id: 11 });
    queryClient.setQueryData([...personalQueryKeys.season(10), "snapshot"], { x: 10 });
    queryClient.setQueryData(personalQueryKeys.seasonStatus(21), { season_id: 21 });

    const leaguesBefore = api.calls("GET /api/leagues").length;
    const meBefore = api.calls("GET /api/auth/me").length;
    const typed = "  alpha   league ";

    await removeAlpha(typed);

    const notice = await screen.findByText("Alpha League was removed from your account.");
    expect(notice).toBeInTheDocument();

    const [request] = api.calls(ROUTE_ALPHA);
    expect(api.calls(ROUTE_ALPHA)).toHaveLength(1);
    expect(request.body).toEqual({ confirmation: typed });
    expect(request.headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);

    expect(api.calls("GET /api/leagues").length).toBeGreaterThan(leaguesBefore);
    expect(api.calls("GET /api/auth/me").length).toBeGreaterThan(meBefore);

    expect(queryClient.getQueryData(personalQueryKeys.seasonStatus(11))).toBeUndefined();
    expect(
      queryClient.getQueryData([...personalQueryKeys.season(10), "snapshot"]),
    ).toBeUndefined();
    expect(queryClient.getQueryData(personalQueryKeys.seasonStatus(21))).toEqual({
      season_id: 21,
    });

    expect(screen.queryByRole("form", { name: "Remove Alpha League" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove Alpha League" })).toBeNull();
    expect(screen.getByRole("button", { name: "Remove Beta League" })).toBeInTheDocument();
  });

  it("stays on settings listing the remaining League", async () => {
    const api = mockPersonalApi();
    removalSucceeds(api, betaOnly());
    renderSettings(api);

    await removeAlpha();
    await screen.findByText("Alpha League was removed from your account.");

    expect(location()).toBe("/account/settings");
    expect(screen.getByRole("heading", { level: 1, name: "Account settings" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Beta League" })).toBeInTheDocument();
    expect(screen.queryByText("You have no Leagues on this account.")).toBeNull();
  });

  it("goes to import when the last League is removed", async () => {
    const api = mockPersonalApi(alphaOnly());
    removalSucceeds(api, NO_LEAGUES, true);
    renderSettings(api);

    await removeAlpha();

    await waitFor(() => {
      expect(location()).toBe("/account/import");
    });
    expect(screen.queryByRole("button", { name: "Remove Alpha League" })).toBeNull();
  });

  it("does not render cached removed data when going back to its Season (last League)", async () => {
    const leagues = alphaOnly();
    const api = mockPersonalApi(leagues);
    removalSucceeds(api, NO_LEAGUES, true);
    const user = userEvent.setup();
    renderSettings(api, "/account/seasons/11");

    await screen.findAllByText(DATA);
    await user.click(screen.getByRole("link", { name: "Account settings" }));
    await screen.findByRole("heading", { level: 1, name: "Account settings" });
    await user.click(screen.getByRole("button", { name: "Remove Alpha League" }));
    await user.type(screen.getByLabelText("Type Alpha League to confirm"), "Alpha League");
    await user.click(screen.getByRole("button", { name: "Remove League" }));
    await waitFor(() => {
      expect(location()).toBe("/account/import");
    });

    await user.click(screen.getByRole("button", { name: "history back" }));

    await waitFor(() => {
      expect(screen.queryAllByText(DATA)).toHaveLength(0);
    });
    expect(screen.queryByText(DATA)).toBeNull();
    expect(location()).not.toBe("/account/seasons/11");
  });

  it("refetches the removed Season and shows the bounded unavailable state when going back", async () => {
    const both = twoLeagues();
    const api = mockPersonalApi(both);
    removalSucceeds(api, betaOnly());
    const user = userEvent.setup();
    renderSettings(api, "/account/seasons/11");

    await screen.findAllByText(DATA);
    const statusBefore = api.calls("GET /api/seasons/11").length;
    expect(seasonDetail(both, 11).league_name).toBe("Alpha League");

    await user.click(screen.getByRole("link", { name: "Account settings" }));
    await screen.findByRole("heading", { level: 1, name: "Account settings" });
    await user.click(screen.getByRole("button", { name: "Remove Alpha League" }));
    await user.type(screen.getByLabelText("Type Alpha League to confirm"), "Alpha League");
    api.on("POST /api/leagues/espn/1001/removal", () => {
      api.reply("GET /api/leagues", 200, betaOnly());
      api.reply("GET /api/seasons/11", 404, { detail: "League season not found." });

      return jsonResponse(200, REMOVED_BODY);
    });
    await user.click(screen.getByRole("button", { name: "Remove League" }));
    await screen.findByText("Alpha League was removed from your account.");

    await user.click(screen.getByRole("button", { name: "history back" }));

    expect(
      await screen.findByRole("heading", { name: "Season not available" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/account/seasons/11");
    expect(api.calls("GET /api/seasons/11").length).toBeGreaterThan(statusBefore);
    expect(screen.queryByText(DATA)).toBeNull();
  });
});

describe("failed removal", () => {
  async function expectStaysOpen(api: ApiMock, message: string | RegExp) {
    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent(message);
    expect(location()).toBe("/account/settings");
    expect(api.calls(ROUTE_ALPHA)).toHaveLength(1);
    expect(screen.getByRole("form", { name: "Remove Alpha League" })).toBeInTheDocument();
    expect(screen.getByLabelText("Type Alpha League to confirm")).toHaveValue("Alpha League");
    expect(screen.queryByText(/was removed from your account/)).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Account settings" })).toBeInTheDocument();
  }

  it.each([
    [
      "confirmation mismatch",
      400,
      { detail: "x", code: "league_removal_confirmation_mismatch" },
      undefined,
      "Type the League name exactly as shown.",
    ],
    [
      "no longer available",
      404,
      { detail: "x", code: "league_removal_unavailable" },
      undefined,
      "This League is no longer on your account.",
    ],
    [
      "throttled",
      429,
      { detail: "x", code: "league_removal_throttled" },
      { "Retry-After": "120" },
      /League removal/,
    ],
  ])("shows a safe message for %s", async (_name, status, body, headers, message) => {
    const api = mockPersonalApi();
    api.reply(ROUTE_ALPHA, status, body, headers);
    renderSettings(api);

    const { submit } = await removeAlpha();

    await expectStaysOpen(api, message);
    expect(submit).toBeEnabled();
    expect(submit).toHaveAttribute("aria-busy", "false");
  });

  it("never renders a raw server detail", async () => {
    const api = mockPersonalApi();
    api.reply(ROUTE_ALPHA, 500, { detail: "RAW-SERVER-DETAIL", code: "raw_internal_code" });
    renderSettings(api);

    await removeAlpha();

    await expectStaysOpen(api, GENERIC_FAILURE);
    expect(document.body.textContent).not.toContain("RAW-SERVER-DETAIL");
    expect(document.body.textContent).not.toContain("raw_internal_code");
  });

  it("shows the connection message on a network failure", async () => {
    const api = mockPersonalApi();
    api.on(ROUTE_ALPHA, () => {
      throw new TypeError("Failed to fetch");
    });
    renderSettings(api);

    await removeAlpha();

    await expectStaysOpen(api, CONNECTION_ERROR_MESSAGE);
  });

  it("ends the session and redirects to sign in on 401", async () => {
    const api = mockPersonalApi();
    api.on(ROUTE_ALPHA, () => {
      api.reply("GET /api/auth/me", 401, { detail: "Authentication is required." });

      return jsonResponse(401, { detail: "Authentication is required." });
    });
    const { queryClient } = renderSettings(api);

    await removeAlpha();

    await waitFor(() => {
      expect(location()).toBe("/login");
    });
    expect(screen.queryByRole("button", { name: "Remove Alpha League" })).toBeNull();
    expect(screen.queryByRole("form", { name: "Remove Alpha League" })).toBeNull();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
  });
});

describe("duplicate submission", () => {
  it("sends exactly one request while pending and exposes busy state", async () => {
    const api = mockPersonalApi();
    let release: () => void = () => undefined;
    api.on(ROUTE_ALPHA, async () => {
      await new Promise<void>((resolve) => {
        release = resolve;
      });

      return jsonResponse(404, { detail: "x", code: "league_removal_unavailable" });
    });
    renderSettings(api);
    const { user, input } = await removeAlpha();

    const busy = await screen.findByRole("button", { name: "Removing League…" });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");

    await user.click(busy);
    await user.click(busy);
    await user.type(input, "{Enter}");

    expect(api.calls(ROUTE_ALPHA)).toHaveLength(1);

    release();

    await screen.findByRole("alert");
    expect(api.calls(ROUTE_ALPHA)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Remove League" })).toHaveAttribute(
      "aria-busy",
      "false",
    );
  });
});

describe("account route guard", () => {
  it("lets an account without Leagues open settings but not other personal pages", async () => {
    const api = mockPersonalApi(NO_LEAGUES);
    api.reply("GET /api/auth/me", 200, {
      ...PERSONAL_USER,
      navigation_state: "import_required",
    });
    const view = renderSettings(api);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Account settings" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/account/settings");
    expect(screen.getByRole("form", { name: "Delete your account" })).toBeInTheDocument();
    expect(await screen.findByText("You have no Leagues on this account.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();

    view.unmount();

    renderWithProviders(<AppRouter />, "/account/seasons/1");

    await waitFor(() => {
      expect(location()).toBe("/account/import");
    });
  });

  it("sends a legacy workspace administrator away from settings", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER, "active");
    renderWithProviders(<AppRouter />, "/account/settings");

    await waitFor(() => {
      expect(location()).toBe("/");
    });
    expect(screen.queryByRole("heading", { name: "Remove a League from your account" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
    expect(api.requests.filter((request) => request.method === "POST")).toHaveLength(0);
  });
});

describe("accessibility", () => {
  it("names the form and input, and exposes errors as alerts", async () => {
    const api = mockPersonalApi();
    api.reply(ROUTE_ALPHA, 400, {
      detail: "x",
      code: "league_removal_confirmation_mismatch",
    });
    renderSettings(api);
    const { form, input } = await removeAlpha();

    expect(form).toHaveAccessibleName("Remove Alpha League");
    expect(input).toHaveAccessibleName("Type Alpha League to confirm");
    expect(document.querySelector("[title]")).toBeNull();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Type the League name exactly as shown.");

    for (const button of within(form).getAllByRole("button")) {
      expect(button.tagName).toBe("BUTTON");
    }
    expect(screen.getByRole("button", { name: "Keep this League" })).toHaveAttribute(
      "type",
      "button",
    );
  });
});
