import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LEAGUE_ACCESS_VALIDATED_MESSAGE } from "../../auth/accountMessages";
import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  SPECTATOR_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  type ApiMock,
} from "../../test/accountHarness";
import type { OnboardingStage } from "../../types/auth";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

vi.mock("../../components/layout/AppShell", async () => {
  const { Outlet } = await import("react-router-dom");

  return {
    AppShell: () => (
      <div data-testid="app-shell">
        <Outlet />
      </div>
    ),
  };
});

const VALIDATE = "POST /api/onboarding/provider/validate";
const SWID = "{SYNTHETIC-SWID-0000-TEST}";
const ESPN_S2 = "synthetic-espn-s2-not-a-real-cookie";
const SESSION_CSRF = "synthetic-session-csrf";
const ALLOWED_PATHS = new Set([
  "/api/auth/me",
  "/api/onboarding/provider/validate",
  // Safe read of the validated target on the claim step; never a claim.
  "/api/onboarding/provider/target",
]);

const VALIDATED_PUBLIC = {
  status: "validated",
  code: "provider_validated",
  provider: "espn",
  access_mode: "public",
  onboarding_stage: "league_claim_pending",
};

const CREDENTIALS_REQUIRED = {
  status: "credentials_required",
  code: "credentials_required",
  provider: "espn",
  access_mode: null,
  onboarding_stage: "provider_setup_pending",
};

let consoleSpies: ReturnType<typeof vi.spyOn>[] = [];

beforeEach(() => {
  document.cookie = `fantasy_session_csrf=${SESSION_CSRF}; path=/`;
  consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((method) =>
    vi.spyOn(console, method),
  );
});

afterEach(() => {
  document.cookie = "fantasy_session_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  consoleSpies.forEach((spy) => spy.mockRestore());
});

function provisional(api: ApiMock, stage: OnboardingStage = "provider_setup_pending") {
  signInAs(api, ADMIN_USER, "provisional", { onboardingStage: stage });
  api.reply("GET /api/onboarding/provider/target", 200, {
    provider: "espn",
    league_id: "424242",
    season_year: 2024,
    access_mode: "public",
  });
}

async function openOnboarding(stage: OnboardingStage = "provider_setup_pending") {
  const api = createApiMock();
  provisional(api, stage);
  const view = renderWithProviders(<AppRouter />, "/onboarding");

  await screen.findByRole("heading", { name: "Your workspace is reserved" });

  return { api, ...view };
}

async function chooseEspn(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("radio", { name: /ESPN/ }));
}

async function enterTarget(
  user: ReturnType<typeof userEvent.setup>,
  leagueId = "424242",
  season = "2024",
) {
  await user.type(screen.getByLabelText("ESPN League ID"), leagueId);
  await user.type(screen.getByLabelText("Season year"), season);
}

/** user-event treats "{" and "[" as key descriptors; double them to type literally. */
function literal(value: string): string {
  return value.replace(/[{[]/g, "$&$&");
}

function submitButton() {
  return screen.getByRole("button", { name: /Check league access|Checking league access/ });
}

function expectNoForeignRequests(api: ApiMock) {
  for (const request of api.requests) {
    expect(ALLOWED_PATHS.has(request.path)).toBe(true);
  }
}

function expectNoSecretsLeaked(queryClient: { getQueryCache: () => { getAll: () => { queryKey: unknown }[] }; getMutationCache: () => { getAll: () => unknown[] } }) {
  expect(document.body.textContent).not.toContain(SWID);
  expect(document.body.textContent).not.toContain(ESPN_S2);
  expect(screen.getByTestId("location").textContent).not.toMatch(/SYNTHETIC|espn-s2/);
  expect(window.location.href).not.toMatch(/SYNTHETIC|espn-s2/);
  expect(window.localStorage.length).toBe(0);
  expect(window.sessionStorage.length).toBe(0);
  expect(JSON.stringify(queryClient.getQueryCache().getAll().map((query) => query.queryKey))).not.toMatch(
    /SYNTHETIC|espn-s2|424242/,
  );
  expect(queryClient.getMutationCache().getAll()).toHaveLength(0);

  for (const spy of consoleSpies) {
    expect(JSON.stringify(spy.mock.calls)).not.toMatch(/SYNTHETIC|espn-s2/);
  }
}

describe("provider selection", () => {
  it("offers ESPN and shows planned providers as disabled", async () => {
    await openOnboarding();

    expect(screen.getByRole("heading", { name: "Connect your fantasy league" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /ESPN/ })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /Sleeper\s*Coming later/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /Yahoo\s*Coming later/ })).toBeDisabled();
    // No form for any provider until ESPN is chosen.
    expect(screen.queryByLabelText("ESPN League ID")).not.toBeInTheDocument();
  });

  it("asks only for League ID and season year at first", async () => {
    const user = userEvent.setup();
    await openOnboarding();

    await chooseEspn(user);

    expect(screen.getByLabelText("ESPN League ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Season year")).toBeInTheDocument();
    expect(screen.queryByLabelText("SWID")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("espn_s2")).not.toBeInTheDocument();
    expect(screen.getByText(/does not claim or import the league yet/)).toBeInTheDocument();
  });
});

describe("public league", () => {
  it("validates with a string League ID, refreshes /me once, and shows the next step", async () => {
    const user = userEvent.setup();
    const { api, queryClient } = await openOnboarding();
    api.on(VALIDATE, () => {
      provisional(api, "league_claim_pending");

      return jsonResponse(200, VALIDATED_PUBLIC);
    });
    const meBefore = api.calls("GET /api/auth/me").length;

    await chooseEspn(user);
    await enterTarget(user, "0424242", "2024");
    await user.click(submitButton());

    expect(await screen.findByText(LEAGUE_ACCESS_VALIDATED_MESSAGE)).toBeInTheDocument();

    const [request] = api.calls(VALIDATE);
    expect(request.body).toEqual({ provider: "espn", league_id: "0424242", season_year: 2024 });
    expect(typeof (request.body as { league_id: unknown }).league_id).toBe("string");
    expect(request.headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);
    expect(api.calls("GET /api/auth/me").length - meBefore).toBe(1);

    // Placeholder only: no provider form, no credential fields, no claim.
    expect(screen.queryByRole("radio", { name: /ESPN/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("SWID")).not.toBeInTheDocument();
    expectNoForeignRequests(api);
    expectNoSecretsLeaked(queryClient);
  });
});

describe("private league", () => {
  it("reveals masked credentials, keeps the target, and submits all four values", async () => {
    const user = userEvent.setup();
    const { api, queryClient } = await openOnboarding();
    let attempt = 0;
    api.on(VALIDATE, () => {
      attempt += 1;

      if (attempt === 1) {
        return jsonResponse(200, CREDENTIALS_REQUIRED);
      }

      // /me stays at the provider step so the cleared form stays visible.
      return jsonResponse(200, { ...VALIDATED_PUBLIC, access_mode: "private" });
    });

    await chooseEspn(user);
    await enterTarget(user);
    await user.click(submitButton());

    const swid = await screen.findByLabelText("SWID");
    const espnS2 = screen.getByLabelText("espn_s2");
    expect(screen.getByText(/required only for private ESPN leagues/)).toBeInTheDocument();
    expect(swid).toHaveAttribute("type", "password");
    expect(espnS2).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("ESPN League ID")).toHaveValue("424242");
    expect(screen.getByLabelText("Season year")).toHaveValue("2024");

    await user.type(swid, literal(SWID));
    await user.type(espnS2, ESPN_S2);

    await user.click(screen.getByRole("button", { name: "Show swid" }));
    expect(swid).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide swid" }));
    expect(swid).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Show espn_s2" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    await user.click(submitButton());

    await waitFor(() => {
      expect(api.calls(VALIDATE)).toHaveLength(2);
    });
    expect(api.calls(VALIDATE)[1].body).toEqual({
      provider: "espn",
      league_id: "424242",
      season_year: 2024,
      credentials: { swid: SWID, espn_s2: ESPN_S2 },
    });

    // Success clears every field and hides the credential section.
    await waitFor(() => {
      expect(screen.getByLabelText("ESPN League ID")).toHaveValue("");
    });
    expect(screen.getByLabelText("Season year")).toHaveValue("");
    expect(screen.queryByLabelText("SWID")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(SWID);
    expect(document.body.innerHTML).not.toContain(ESPN_S2);
    expectNoForeignRequests(api);
    expectNoSecretsLeaked(queryClient);
  });

  it("rejects partial credentials locally and supports clearing them", async () => {
    const user = userEvent.setup();
    const { api } = await openOnboarding();
    api.reply(VALIDATE, 200, CREDENTIALS_REQUIRED);

    await chooseEspn(user);
    await enterTarget(user);
    await user.click(submitButton());
    await user.type(await screen.findByLabelText("SWID"), literal(SWID));
    await user.click(submitButton());

    expect(await screen.findByText(/Enter espn_s2\. Both credentials are required/)).toBeInTheDocument();
    expect(api.calls(VALIDATE)).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Clear SWID and espn_s2" }));
    expect(screen.getByLabelText("SWID")).toHaveValue("");
    expect(screen.getByLabelText("espn_s2")).toHaveValue("");
  });

  it("shows the safe authentication failure and preserves values for retry", async () => {
    const user = userEvent.setup();
    const { api, queryClient } = await openOnboarding();
    let attempt = 0;
    api.on(VALIDATE, () => {
      attempt += 1;

      return attempt === 1
        ? jsonResponse(200, CREDENTIALS_REQUIRED)
        : jsonResponse(422, {
            detail:
              "ESPN did not accept these private league credentials. Check SWID and espn_s2 and try again.",
            code: "provider_authentication_failed",
            retryable: false,
          });
    });

    await chooseEspn(user);
    await enterTarget(user);
    await user.click(submitButton());
    await user.type(await screen.findByLabelText("SWID"), literal(SWID));
    await user.type(screen.getByLabelText("espn_s2"), ESPN_S2);
    await user.click(submitButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "ESPN did not accept these private league credentials.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(SWID);
    expect(screen.getByLabelText("SWID")).toHaveValue(SWID);
    expect(screen.getByLabelText("espn_s2")).toHaveValue(ESPN_S2);
    expect(submitButton()).toBeEnabled();
    expectNoSecretsLeaked(queryClient);
  });
});

describe("failures", () => {
  it("distinguishes temporary ESPN unavailability", async () => {
    const user = userEvent.setup();
    const { api } = await openOnboarding();
    api.reply(VALIDATE, 503, {
      detail: "ESPN is not responding right now. Try again in a few minutes.",
      code: "provider_unavailable",
      retryable: true,
    });

    await chooseEspn(user);
    await enterTarget(user);
    await user.click(submitButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("ESPN is not responding right now.");
    expect(screen.getByLabelText("ESPN League ID")).toHaveValue("424242");
  });

  it("shows Retry-After for throttling", async () => {
    const user = userEvent.setup();
    const { api } = await openOnboarding();
    api.reply(
      VALIDATE,
      429,
      {
        detail: "Too many league access checks. Try again later.",
        code: "provider_validation_throttled",
        retryable: true,
      },
      { "Retry-After": "120" },
    );

    await chooseEspn(user);
    await enterTarget(user);
    await user.click(submitButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many league access attempts. Try again in 2 minutes.",
    );
  });

  it("validates the League ID and season locally without a request", async () => {
    const user = userEvent.setup();
    const { api } = await openOnboarding();

    await chooseEspn(user);
    await enterTarget(user, "12ab", "1999");
    await user.click(submitButton());

    expect(await screen.findByText(/Enter the numeric League ID/)).toBeInTheDocument();
    expect(screen.getByText(/Enter a season year from 2000/)).toBeInTheDocument();
    expect(screen.getByLabelText("ESPN League ID")).toHaveAttribute("aria-invalid", "true");
    expect(api.calls(VALIDATE)).toHaveLength(0);
  });

  it("prevents duplicate submission while a check is pending", async () => {
    const user = userEvent.setup();
    const { api } = await openOnboarding();
    let release: (response: Response) => void = () => undefined;
    api.on(
      VALIDATE,
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );

    await chooseEspn(user);
    await enterTarget(user);
    await user.click(submitButton());
    await user.click(submitButton());

    expect(submitButton()).toBeDisabled();
    expect(submitButton()).toHaveAttribute("aria-busy", "true");
    expect(screen.getByLabelText("ESPN League ID")).toHaveValue("424242");

    release(jsonResponse(200, CREDENTIALS_REQUIRED));
    await screen.findByLabelText("SWID");
    expect(api.calls(VALIDATE)).toHaveLength(1);
  });
});

describe("resume", () => {
  it("provider_validation_failed shows a safe retry flow with empty fields", async () => {
    const user = userEvent.setup();
    await openOnboarding("provider_validation_failed");

    expect(screen.getByText(/last league access check did not succeed/)).toBeInTheDocument();
    await chooseEspn(user);
    expect(screen.getByLabelText("ESPN League ID")).toHaveValue("");
    expect(screen.queryByLabelText("SWID")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("provider_validation_failed");
  });

  it("league_claim_pending shows the claim step, never the provider form", async () => {
    const { api } = await openOnboarding("league_claim_pending");

    expect(screen.getByText(LEAGUE_ACCESS_VALIDATED_MESSAGE)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Confirm your league" })).toBeInTheDocument();
    expect(api.calls("POST /api/onboarding/provider/claim")).toHaveLength(0);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Check league access/ })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("league_claim_pending");
    expectNoForeignRequests(api);
  });

  it("provider_setup_pending shows provider selection without raw stage names", async () => {
    await openOnboarding("provider_setup_pending");

    expect(screen.getByRole("radio", { name: /ESPN/ })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("provider_setup_pending");
  });
});

describe("access control", () => {
  it.each([
    ["spectator", SPECTATOR_USER],
    ["active administrator", ADMIN_USER],
  ])("a %s never sees the provider setup UI", async (_label, account) => {
    const api = createApiMock();
    signInAs(api, account);

    renderWithProviders(<AppRouter />, "/onboarding");

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connect your fantasy league" })).not.toBeInTheDocument();
    expect(api.calls(VALIDATE)).toHaveLength(0);
  });
});
