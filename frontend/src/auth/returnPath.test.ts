import { describe, expect, it } from "vitest";

import { safeReturnPath } from "./returnPath";

describe("safeReturnPath", () => {
  it.each([
    "/",
    "/all-play",
    "/optimal-lineups/7?season_id=4",
    "/admin#sync",
  ])("allows in-application path %s", (path) => {
    expect(safeReturnPath(path)).toBe(path);
  });

  it.each([
    "//attacker.example",
    "//attacker.example/path",
    "/\\attacker.example",
    "https://attacker.example",
    "javascript:alert(1)",
    "all-play",
    "",
    "/all-play\nSet-Cookie: x",
    "/login",
    "/login?next=/",
    "/register",
    "/verify-email?token=abc",
    "/reset-password",
    "/forgot-password",
    "/onboarding",
  ])("rejects unsafe or account path %s", (path) => {
    expect(safeReturnPath(path)).toBeNull();
  });

  it.each([null, undefined, 42, { pathname: "/" }])(
    "rejects non-string value %s",
    (value) => {
      expect(safeReturnPath(value)).toBeNull();
    },
  );
});
