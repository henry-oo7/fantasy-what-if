import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  PERSONAL_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
} from "../../test/accountHarness";
import { season, twoLeagues } from "../../test/personalFixtures";
import type { PersonalLeagues } from "../../types/personal";
import { LeagueSwitcher } from "./LeagueSwitcher";

function setup(leagues: PersonalLeagues = twoLeagues()) {
  const api = createApiMock();
  signInAs(api, PERSONAL_USER);
  api.reply("GET /api/leagues", 200, leagues);

  return api;
}

function renderAt(path: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/account/seasons/:seasonId/*" element={<LeagueSwitcher />} />
      <Route path="/account/*" element={<LeagueSwitcher />} />
    </Routes>,
    path,
  );
}

async function switcher() {
  return screen.findByRole("navigation", { name: "Leagues and Seasons" });
}

describe("LeagueSwitcher", () => {
  it("renders a named landmark with a region per League and a link per Season", async () => {
    setup();
    renderAt("/account/seasons/11");
    await screen.findByRole("region", { name: /Alpha League/ });

    const alpha = screen.getByRole("region", { name: /Alpha League/ });
    expect(within(alpha).getAllByRole("link").map((link) => link.textContent)).toEqual([
      expect.stringContaining("2025"),
      expect.stringContaining("2024"),
    ]);
    const beta = screen.getByRole("region", { name: /Beta League/ });
    expect(within(beta).getAllByRole("link")).toHaveLength(1);
    expect(within(await switcher()).getAllByRole("region")).toHaveLength(2);
  });

  it("marks only the selected Season with aria-current", async () => {
    setup();
    renderAt("/account/seasons/10");
    await switcher();
    await screen.findByRole("region", { name: /Alpha League/ });

    const alpha = screen.getByRole("region", { name: /Alpha League/ });
    expect(within(alpha).getByRole("link", { name: /2024/ })).toHaveAttribute("aria-current", "true");
    expect(within(alpha).getByRole("link", { name: /2025/ })).not.toHaveAttribute("aria-current");
    expect(
      within(screen.getByRole("region", { name: /Beta League/ })).getByRole("link", { name: /2025/ }),
    ).not.toHaveAttribute("aria-current");
  });

  it("marks no Season selected outside a Season route", async () => {
    setup();
    renderAt("/account");
    await screen.findByRole("region", { name: /Alpha League/ });

    const nav = await switcher();
    for (const link of within(nav).getAllByRole("link")) {
      expect(link).not.toHaveAttribute("aria-current");
    }
  });

  it("shows the Default text on the default Season only", async () => {
    setup();
    renderAt("/account/seasons/11");
    await screen.findByRole("region", { name: /Alpha League/ });

    expect(screen.getAllByText("Default")).toHaveLength(1);
    const alpha = screen.getByRole("region", { name: /Alpha League/ });
    expect(within(within(alpha).getByRole("link", { name: /2025/ })).getByText("Default")).toBeInTheDocument();
  });

  it("states Reconnect needed in text for a Season needing reverification", async () => {
    setup(
      twoLeagues({
        leagues: [
          {
            provider: "espn",
            provider_league_id: "1001",
            league_name: "Alpha League",
            seasons: [season(11, 2025, { access_status: "reverification_required" }), season(10, 2024)],
          },
        ],
      }),
    );
    renderAt("/account/seasons/11");
    await screen.findByRole("region", { name: /Alpha League/ });

    expect(screen.getAllByText("Reconnect needed")).toHaveLength(1);
    expect(screen.getByRole("link", { name: /2025.*Reconnect needed/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /2024/ })).not.toHaveTextContent("Reconnect needed");
  });

  it("announces loading politely", async () => {
    const api = setup();
    api.on("GET /api/leagues", () => new Promise<Response>(() => undefined));
    renderAt("/account/seasons/11");

    expect(await screen.findByRole("status")).toHaveTextContent("Loading your Leagues…");
  });

  it("shows an alert with a working Try again after a server error", async () => {
    const api = setup();
    api.reply("GET /api/leagues", 500, { detail: "boom" });
    const user = userEvent.setup();
    renderAt("/account/seasons/11");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your Leagues could not be loaded.");
    expect(alert).not.toHaveTextContent("boom");
    const before = api.calls("GET /api/leagues").length;

    api.reply("GET /api/leagues", 200, twoLeagues());
    await user.click(within(alert).getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("region", { name: /Alpha League/ })).toBeInTheDocument();
    expect(api.calls("GET /api/leagues").length).toBeGreaterThan(before);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers a first import for an empty account", async () => {
    setup({ default_season_id: null, leagues: [] });
    renderAt("/account");

    expect(await screen.findByText("You have not imported a League yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Import a League" })).toHaveAttribute("href", "/account/import");
    expect(screen.queryByRole("link", { name: "Import another League" })).not.toBeInTheDocument();
  });

  it("offers another import when Leagues exist", async () => {
    setup();
    renderAt("/account");

    expect(await screen.findByRole("link", { name: "Import another League" })).toHaveAttribute(
      "href",
      "/account/import",
    );
    expect(screen.queryByText("You have not imported a League yet.")).not.toBeInTheDocument();
  });

  it.each([
    ["/account/seasons/11/all-play", "/account/seasons/10/all-play"],
    ["/account/seasons/11", "/account/seasons/10"],
    ["/account/seasons/11/optimal-lineups/5", "/account/seasons/10/optimal-lineups"],
  ])("keeps the current section: %s -> %s", async (path, expected) => {
    setup();
    renderAt(path);
    await screen.findByRole("region", { name: /Alpha League/ });

    expect(screen.getByRole("link", { name: /2024/ })).toHaveAttribute("href", expected);
  });

  it("follows the backend order exactly", async () => {
    setup(
      twoLeagues({
        default_season_id: null,
        leagues: [
          {
            provider: "espn",
            provider_league_id: "2",
            league_name: "Zulu League",
            seasons: [season(3, 2023), season(4, 2025), season(5, 2024)],
          },
          {
            provider: "espn",
            provider_league_id: "1",
            league_name: "Able League",
            seasons: [season(6, 2022)],
          },
        ],
      }),
    );
    renderAt("/account/seasons/4");
    await screen.findByRole("region", { name: /Zulu League/ });

    const regions = screen.getAllByRole("region");
    expect(regions[0]).toHaveAccessibleName(/Zulu League/);
    expect(regions[1]).toHaveAccessibleName(/Able League/);
    expect(
      within(regions[0]).getAllByRole("link").map((link) => link.getAttribute("href")),
    ).toEqual(["/account/seasons/3", "/account/seasons/4", "/account/seasons/5"]);
  });

  it("uses no legacy workspace vocabulary and gives every link an accessible name", async () => {
    setup();
    renderAt("/account/seasons/11");
    const nav = await switcher();
    await screen.findByRole("region", { name: /Alpha League/ });

    expect(nav).not.toHaveTextContent(/workspace|claim|owner|connection/i);
    for (const link of within(nav).getAllByRole("link")) {
      expect(link).toHaveAccessibleName(/\S/);
    }
  });

  it("never requests anything but the League list", async () => {
    const api = setup();
    api.on("GET /api/leagues", () => jsonResponse(200, twoLeagues()));
    renderAt("/account/seasons/11");
    await screen.findByRole("region", { name: /Alpha League/ });

    expect(
      api.requests.filter((request) => request.path.startsWith("/api/seasons")),
    ).toEqual([]);
  });
});
