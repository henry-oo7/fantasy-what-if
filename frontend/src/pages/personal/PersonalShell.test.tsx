import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AppRouter } from "../../router";
import {
  PERSONAL_USER,
  createApiMock,
  renderWithProviders,
  signInAs,
} from "../../test/accountHarness";
import { seasonDetail, snapshot, twoLeagues } from "../../test/personalFixtures";

function setup() {
  const leagues = twoLeagues();
  const api = createApiMock();
  signInAs(api, PERSONAL_USER);
  api.reply("GET /api/leagues", 200, leagues);

  for (const league of leagues.leagues) {
    for (const item of league.seasons) {
      api.reply(`GET /api/seasons/${item.season_id}`, 200, seasonDetail(leagues, item.season_id));
      api.reply(`GET /api/seasons/${item.season_id}/snapshot`, 200, snapshot(league.league_name, item.season_year));
    }
  }

  return api;
}

async function renderShell(path = "/account/seasons/11") {
  const view = renderWithProviders(<AppRouter />, path);
  await screen.findByRole("navigation", { name: "Leagues and Seasons" });

  return view;
}

describe("PersonalShell", () => {
  it("places the League switcher inside the application sidebar", async () => {
    setup();
    await renderShell();

    const sidebar = document.getElementById("application-sidebar") as HTMLElement;
    expect(sidebar).not.toBeNull();
    expect(
      within(sidebar).getByRole("navigation", { name: "Leagues and Seasons" }),
    ).toBeInTheDocument();
  });

  it("toggles the mobile drawer and closes it with Escape", async () => {
    setup();
    const user = userEvent.setup();
    await renderShell();

    const menu = screen.getByRole("button", { name: "Open navigation" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(menu).toHaveAttribute("aria-controls", "application-sidebar");

    await user.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");

    await user.keyboard("{Escape}");
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });

  it("makes the scrim focusable only while the drawer is open", async () => {
    setup();
    const user = userEvent.setup();
    await renderShell();

    const scrim = () =>
      screen
        .getAllByRole("button", { name: "Close navigation" })
        .find((button) => button.classList.contains("navigation-scrim")) as HTMLElement;

    expect(scrim()).toHaveAttribute("tabindex", "-1");
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(scrim()).toHaveAttribute("tabindex", "0");

    await user.click(scrim());
    expect(screen.getByRole("button", { name: "Open navigation" })).toHaveAttribute("aria-expanded", "false");
    expect(scrim()).toHaveAttribute("tabindex", "-1");
  });

  it("lists the Season sections with the current one marked", async () => {
    setup();
    await renderShell("/account/seasons/11/all-play");

    const nav = await screen.findByRole("navigation", { name: "Season navigation" });
    const links = within(nav).getAllByRole("link");

    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Season overview", "/account/seasons/11"],
      ["All-Play Standings", "/account/seasons/11/all-play"],
      ["League Average", "/account/seasons/11/league-average"],
      ["Schedule Swap", "/account/seasons/11/schedule-swap"],
      ["Playoff Predictor", "/account/seasons/11/playoff-predictor"],
      ["Optimal Lineups", "/account/seasons/11/optimal-lineups"],
    ]);
    expect(within(nav).getByRole("link", { name: "All-Play Standings" })).toHaveAttribute("aria-current", "page");
    expect(links.filter((link) => link.getAttribute("aria-current") === "page")).toHaveLength(1);
  });

  it("marks only Season overview current on the Season root", async () => {
    setup();
    await renderShell("/account/seasons/11");

    const nav = await screen.findByRole("navigation", { name: "Season navigation" });
    expect(within(nav).getByRole("link", { name: "Season overview" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "All-Play Standings" })).not.toHaveAttribute("aria-current");
  });

  it("shows no administrator, spectator or workspace vocabulary", async () => {
    setup();
    await renderShell();
    await screen.findAllByText(/Alpha League 2025 Rockets/);

    expect(document.body).not.toHaveTextContent(/Administration|Administrator|Spectator|workspace/i);
  });

  it("shows the account chip", async () => {
    setup();
    await renderShell();

    const sidebar = document.getElementById("application-sidebar") as HTMLElement;
    expect(within(sidebar).getByText("Example Person")).toBeInTheDocument();
    expect(within(sidebar).getByText("Personal account")).toBeInTheDocument();
  });

  it("has no Season navigation on the import page", async () => {
    setup();
    await renderShell("/account/import");

    expect(screen.queryByRole("navigation", { name: "Season navigation" })).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Leagues and Seasons" })).toBeInTheDocument();
  });

  it("closes the drawer when a switcher Season link is chosen", async () => {
    setup();
    const user = userEvent.setup();
    await renderShell();

    const menu = screen.getByRole("button", { name: "Open navigation" });
    await user.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");

    await user.click(
      within(screen.getByRole("navigation", { name: "Leagues and Seasons" })).getByRole("link", { name: /2024/ }),
    );

    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("location")).toHaveTextContent("/account/seasons/10");
  });
});
