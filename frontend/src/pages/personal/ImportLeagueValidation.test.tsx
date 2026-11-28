import { screen } from "@testing-library/react";
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
import { twoLeagues } from "../../test/personalFixtures";

const ID_ERROR = "Enter the numeric League ID from your ESPN league address.";
const YEAR_ERROR = `Enter a season year from 2000 to ${new Date().getFullYear()}.`;

function setup(importReply?: () => Response | Promise<Response>) {
  const api = createApiMock();
  signInAs(api, { ...PERSONAL_USER, navigation_state: "import_required" });
  api.reply("GET /api/leagues", 200, { default_season_id: null, leagues: [] });

  if (importReply) {
    api.on("POST /api/league-imports", importReply);
  }

  document.cookie = "fantasy_session_csrf=synthetic-session-csrf; path=/";

  return api;
}

afterEach(() => {
  document.cookie = "fantasy_session_csrf=; path=/; max-age=0";
});

type User = ReturnType<typeof userEvent.setup>;

async function submit(user: User, leagueId: string, year: string) {
  const idInput = await screen.findByLabelText("ESPN League ID");
  const yearInput = screen.getByLabelText("Season year");

  await user.clear(idInput);
  await user.clear(yearInput);

  if (leagueId) {
    await user.type(idInput, leagueId);
  }

  if (year) {
    await user.type(yearInput, year);
  }

  await user.click(screen.getByRole("button", { name: "Import League" }));
}

describe("import field validation", () => {
  it("rejects an empty League ID with an associated error", async () => {
    const api = setup();
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "", "2025");

    const input = screen.getByLabelText("ESPN League ID");
    const error = screen.getByText(ID_ERROR);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
    expect(screen.getByLabelText("Season year")).not.toHaveAttribute("aria-invalid");
    expect(api.calls("POST /api/league-imports")).toHaveLength(0);
  });

  it.each(["abc", "000", "12a4"])("rejects League ID %s", async (value) => {
    const api = setup();
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, value, "2025");

    expect(screen.getByText(ID_ERROR)).toBeInTheDocument();
    expect(api.calls("POST /api/league-imports")).toHaveLength(0);
  });

  it.each([
    ["1999", "1999"],
    ["next year", String(new Date().getFullYear() + 1)],
    ["non-numeric", "20x5"],
    ["empty", ""],
  ])("rejects season year (%s)", async (_label, value) => {
    const api = setup();
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "1001", value);

    const input = screen.getByLabelText("Season year");
    const error = screen.getByText(YEAR_ERROR);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
    expect(api.calls("POST /api/league-imports")).toHaveLength(0);
  });

  it("reports both field errors at once", async () => {
    const api = setup();
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "abc", "1999");

    expect(screen.getByText(ID_ERROR)).toBeInTheDocument();
    expect(screen.getByText(YEAR_ERROR)).toBeInTheDocument();
    expect(api.calls("POST /api/league-imports")).toHaveLength(0);
  });

  it("accepts the boundary years", async () => {
    const api = setup(() => jsonResponse(503, { detail: "x", code: "league_import_unavailable" }));
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "1001", "2000");
    await screen.findByRole("alert");
    await submit(user, "1001", String(new Date().getFullYear()));
    await screen.findByRole("alert");

    expect(screen.queryByText(YEAR_ERROR)).not.toBeInTheDocument();
    expect(api.calls("POST /api/league-imports")).toHaveLength(2);
  });
});

describe("import failure messages", () => {
  it.each([
    ["provider_validation_failed", 422, "That ESPN League could not be found for this season."],
    ["provider_unavailable", 503, "ESPN is not responding right now. Try again later."],
    ["unsupported_provider", 422, "That provider is not supported."],
    ["league_import_forbidden", 403, "You cannot import this League."],
    ["league_import_unavailable", 503, "The league could not be imported right now. Try again in a moment."],
  ])("shows the server detail for %s", async (code, status, detail) => {
    setup(() => jsonResponse(status, { detail, code, retryable: false }));
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "1001", "2025");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(detail);
    expect(alert).toHaveAttribute("id", "import-alert");
  });

  it("describes the throttle from Retry-After", async () => {
    setup(() =>
      jsonResponse(
        429,
        { detail: "Slow down.", code: "league_import_throttled", retryable: true },
        { "Retry-After": "120" },
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "1001", "2025");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many League import attempts. Try again in 2 minutes.",
    );
  });

  it("hides the detail of an unknown code", async () => {
    setup(() => jsonResponse(500, { detail: "internal secret detail", code: "something_else", retryable: false }));
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "1001", "2025");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The League could not be imported. Try again.");
    expect(alert).not.toHaveTextContent("internal secret detail");
  });

  it("explains a network failure", async () => {
    setup(() => {
      throw new TypeError("Failed to fetch");
    });
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "1001", "2025");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Unable to reach Fantasy What-If. Check your connection and try again.",
    );
  });

  it("points the form at the alert", async () => {
    setup(() => jsonResponse(503, { detail: "Try later.", code: "league_import_unavailable" }));
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    await submit(user, "1001", "2025");
    const alert = await screen.findByRole("alert");

    const form = alert.closest("form") as HTMLFormElement;
    expect(form).toHaveAttribute("aria-describedby", "import-alert");
    expect(alert).toHaveAttribute("id", "import-alert");
  });
});

describe("import page structure", () => {
  it("labels the first import for an import_required account", async () => {
    setup();
    renderWithProviders(<AppRouter />, "/account/import");

    expect(await screen.findByRole("heading", { name: "Import your first League" })).toBeInTheDocument();
  });

  it("labels another import for a personal account with Leagues", async () => {
    const api = createApiMock();
    signInAs(api, PERSONAL_USER);
    api.reply("GET /api/leagues", 200, twoLeagues());
    renderWithProviders(<AppRouter />, "/account/import");

    expect(await screen.findByRole("heading", { name: "Import another League" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Import your first League" })).not.toBeInTheDocument();
  });

  it("has visible labelled fields and no alert before submission", async () => {
    setup();
    renderWithProviders(<AppRouter />, "/account/import");

    expect(await screen.findByLabelText("ESPN League ID")).toBeVisible();
    expect(screen.getByLabelText("Season year")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("ESPN League ID").closest("form")).not.toHaveAttribute("aria-describedby");
  });

  it("keeps credentials as password inputs with autocomplete off", async () => {
    setup();
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account/import");

    expect(screen.queryByLabelText("SWID")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("checkbox", { name: "This is a private League" }));

    for (const label of ["SWID", "espn_s2"]) {
      const input = screen.getByLabelText(label);
      expect(input).toHaveAttribute("type", "password");
      expect(input).toHaveAttribute("autocomplete", "off");
    }
  });

  it("offers neither Sleeper nor Yahoo", async () => {
    setup();
    renderWithProviders(<AppRouter />, "/account/import");
    await screen.findByLabelText("ESPN League ID");

    expect(document.body).not.toHaveTextContent(/Sleeper|Yahoo/);
  });
});
