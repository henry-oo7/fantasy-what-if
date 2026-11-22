import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  ADMIN_USER,
  SPECTATOR_USER,
  createApiMock,
  renderWithProviders,
  signInAs,
} from "../../test/accountHarness";
import { AppShell } from "./AppShell";

function renderShell() {
  renderWithProviders(
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<h1>League home</h1>} />
      </Route>
    </Routes>,
    "/",
  );
}

describe("AppShell role-based controls", () => {
  it("does not render administrator controls for a spectator", async () => {
    const api = createApiMock();
    signInAs(api, SPECTATOR_USER);

    renderShell();

    expect(await screen.findByRole("heading", { name: "League home" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open administration" })).not.toBeInTheDocument();
    expect(screen.queryByText("Administrator")).not.toBeInTheDocument();
  });

  it("renders administrator controls for an administrator", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER);

    renderShell();

    expect(
      await screen.findByRole("link", { name: "Open administration" }),
    ).toBeInTheDocument();
  });
});
