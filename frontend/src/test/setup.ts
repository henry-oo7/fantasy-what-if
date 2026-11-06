import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";


afterEach(() => {
  cleanup();

  document.cookie =
    "fantasy_session=; Max-Age=0; Path=/";
  document.cookie =
    "fantasy_session_csrf=; Max-Age=0; Path=/";
  document.cookie =
    "fantasy_preauth_csrf=; Max-Age=0; Path=/";
});
