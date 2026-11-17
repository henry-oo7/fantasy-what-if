import {
  describe,
  expect,
  it,
} from "vitest";

import {
  dashboardGreeting,
} from "./dashboardGreeting";


describe("dashboard greeting", () => {
  it("personalizes an administrator greeting", () => {
    expect(
      dashboardGreeting({
        role: "admin",
        display_name: "Henry Huynh",
      }),
    ).toBe("Welcome back, Henry");
  });

  it("uses a role-aware spectator greeting", () => {
    expect(
      dashboardGreeting({
        role: "spectator",
        display_name: "League Spectator",
      }),
    ).toBe("Welcome back, Spectator");
  });

  it("ignores a customized spectator display name", () => {
    expect(
      dashboardGreeting({
        role: "spectator",
        display_name: "Jimmy League Viewer",
      }),
    ).toBe("Welcome back, Spectator");
  });

  it("handles a missing administrator display name", () => {
    expect(
      dashboardGreeting({
        role: "admin",
        display_name: "   ",
      }),
    ).toBe("Welcome back");
  });

  it("handles a missing authenticated user", () => {
    expect(
      dashboardGreeting(undefined),
    ).toBe("Welcome back");
  });
});
