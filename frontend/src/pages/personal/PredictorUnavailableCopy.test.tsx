import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  createApiMock,
  renderWithProviders,
  signInAs,
} from "../../test/accountHarness";
import { mockPersonalApi } from "../../test/personalApi";
import { snapshot } from "../../test/personalFixtures";

const RAW = "playoff_team_count missing: provider_error=ESPN-503 job_id=77";
const WORKSPACE_ERA = /administrator|workspace owner|shared (account|password)|workspace/i;

describe("Playoff Predictor 409 copy", () => {
  it("shows bounded personal copy and no workspace-era or raw text on a personal Season", async () => {
    const api = mockPersonalApi();
    api.reply("GET /api/seasons/11/playoff-predictor", 409, { detail: RAW });

    renderWithProviders(<AppRouter />, "/account/seasons/11/playoff-predictor");

    expect(await screen.findByRole("heading", { name: "Playoff data is not available yet" })).toBeInTheDocument();
    expect(
      screen.getByText("League data is not available yet. Check again after synchronization finishes."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();

    const page = document.body.textContent ?? "";
    expect(page).not.toMatch(WORKSPACE_ERA);
    expect(page).not.toContain("ESPN-503");
    expect(page).not.toContain("job_id");
    expect(page).not.toContain("playoff_team_count");
  });

  it("keeps the administrator guidance on the legacy workspace route", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);
    api.reply("GET /api/leagues/current", 200, snapshot("Legacy League", 2025));
    api.reply("GET /api/leagues/current/playoff-predictor", 409, {
      detail: "Playoff settings have not been imported yet.",
    });

    renderWithProviders(<AppRouter />, "/playoff-predictor");

    expect(await screen.findByRole("heading", { name: "Playoff setup needs a refresh" })).toBeInTheDocument();
    expect(screen.getByText("Playoff settings have not been imported yet.")).toBeInTheDocument();
    expect(screen.getByText(/An administrator can run a normal synchronization/)).toBeInTheDocument();
  });
});
