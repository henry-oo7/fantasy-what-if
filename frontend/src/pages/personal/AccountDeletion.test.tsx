import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { personalQueryKeys } from "../../api/personalLeagues";
import {
  ACCOUNT_DELETED_MESSAGE,
  CONNECTION_ERROR_MESSAGE,
} from "../../auth/accountMessages";
import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  createApiMock,
  jsonResponse,
  renderWithProviders,
  signInAs,
  type ApiMock,
} from "../../test/accountHarness";
import { mockPersonalApi } from "../../test/personalApi";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

const ROUTE = "POST /api/account/deletion";
const SESSION_CSRF = "synthetic-session-csrf";
const PASSWORD = "synthetic-password-value";
const PHRASE = "DELETE MY ACCOUNT";
const GENERIC_FAILURE =
  "Your account could not be deleted. Nothing was deleted. Try again later.";
const DELETED_BODY = { detail: "Your account was deleted.", code: "account_deleted" };

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

function renderSettings(api: ApiMock = mockPersonalApi()) {
  const view = renderWithProviders(
    <>
      <AppRouter />
      <BackButton />
    </>,
    "/account/settings",
  );

  return { api, ...view };
}

async function openForm() {
  const user = userEvent.setup();
  const password = await screen.findByLabelText("Current password");
  const confirmation = screen.getByLabelText(`Type ${PHRASE} to confirm`);
  const submit = screen.getByRole("button", { name: "Delete my account" });

  return { user, password, confirmation, submit };
}

async function fillAndSubmit(typed = PHRASE) {
  const form = await openForm();

  await form.user.type(form.password, PASSWORD);
  await form.user.type(form.confirmation, typed);
  await form.user.click(form.submit);

  return form;
}

describe("account settings page", () => {
  it("renders labelled, native, hover-free controls", async () => {
    renderSettings();
    const { password, confirmation, submit } = await openForm();

    expect(screen.getByRole("heading", { level: 1, name: "Account settings" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Delete your account" })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Delete your account" })).toBeInTheDocument();

    expect(password.tagName).toBe("INPUT");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    expect(password).toHaveAttribute("maxlength", "128");
    expect(password).toBeRequired();

    expect(confirmation.tagName).toBe("INPUT");
    expect(confirmation).toHaveAttribute("autocomplete", "off");
    expect(confirmation).toHaveAttribute("maxlength", "64");
    expect(confirmation).not.toHaveAttribute("placeholder");
    expect(password).not.toHaveAttribute("placeholder");

    expect(submit.tagName).toBe("BUTTON");
    expect(submit).toHaveAttribute("type", "submit");
    expect(submit).toBeDisabled();
    expect(document.querySelector("[title]")).toBeNull();
  });

  it("enables the button only when the password and normalized phrase are present", async () => {
    renderSettings();
    const { user, password, confirmation, submit } = await openForm();

    await user.type(confirmation, PHRASE);
    expect(submit).toBeDisabled();

    await user.type(password, PASSWORD);
    expect(submit).toBeEnabled();

    await user.clear(confirmation);
    await user.type(confirmation, "DELETE MY ACCOUN");
    expect(submit).toBeDisabled();

    await user.clear(confirmation);
    await user.type(confirmation, "  delete   my   account ");
    expect(submit).toBeEnabled();

    await user.clear(password);
    expect(submit).toBeDisabled();
  });

  it("describes the deletion truthfully", async () => {
    renderSettings();
    await openForm();
    const text = document.body.textContent ?? "";

    expect(text).toMatch(/immediate and cannot be undone/);
    expect(text).toMatch(/sign-in/);
    expect(text).toMatch(/email address/);
    expect(text).toMatch(/display name/);
    expect(text).toMatch(/session/);
    expect(text).toMatch(/preferences/);
    expect(text).toMatch(/ESPN credentials/);
    expect(text).toMatch(/access to every League Season/);
    expect(text).toMatch(/other accounts still use stays available to them/);
    expect(text).toMatch(/may be kept for a limited time/);

    expect(text).not.toMatch(/all (of )?(your )?League data (is|will be) (deleted|destroyed)/i);
    expect(text).not.toMatch(/recover|restore|cancel|grace/i);
    expect(text).not.toMatch(/workspace|slug/i);
    expect(text).not.toMatch(/other (accounts?|users?) (exist|use this)|no other account|another account (exists|uses)/i);
  });

  it("links to account settings from the personal shell", async () => {
    mockPersonalApi();
    const user = userEvent.setup();
    renderWithProviders(<AppRouter />, "/account");

    const link = await screen.findByRole("link", { name: "Account settings" });
    await user.click(link);

    await screen.findByRole("heading", { level: 1, name: "Account settings" });
    expect(location()).toBe("/account/settings");
  });
});

describe("successful deletion", () => {
  it("posts the exact body and CSRF header, then signs out to /login with the notice", async () => {
    const api = mockPersonalApi();
    let release: () => void = () => undefined;
    api.on(ROUTE, async () => {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      api.reply("GET /api/auth/me", 401, { detail: "Authentication is required." });

      return jsonResponse(200, DELETED_BODY);
    });
    const { queryClient } = renderSettings(api);
    await screen.findByRole("heading", { name: "Account settings" });
    queryClient.setQueryData(personalQueryKeys.leagues(), { leagues: [] });
    queryClient.setQueryData(personalQueryKeys.season(11), { season_id: 11 });
    expect(queryClient.getQueryCache().getAll().length).toBeGreaterThan(0);

    const typed = "  delete my   account ";
    const { password } = await fillAndSubmit(typed);

    await waitFor(() => {
      expect(api.calls(ROUTE)).toHaveLength(1);
    });
    expect(password).toHaveValue("");
    expect(location()).toBe("/account/settings");

    release();

    expect(await screen.findByText(ACCOUNT_DELETED_MESSAGE)).toBeInTheDocument();
    expect(location()).toBe("/login");
    expect(screen.queryByRole("button", { name: "Delete my account" })).toBeNull();

    const [request] = api.calls(ROUTE);
    expect(request.body).toEqual({ current_password: PASSWORD, confirmation: typed });
    expect(request.headers.get("X-CSRF-Token")).toBe(SESSION_CSRF);
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
    expect(document.body.textContent).not.toContain(PASSWORD);
  });

  it("does not show the deletion form when going back after deletion", async () => {
    const api = mockPersonalApi();
    api.on(ROUTE, () => {
      api.reply("GET /api/auth/me", 401, { detail: "Authentication is required." });

      return jsonResponse(200, DELETED_BODY);
    });
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <AppRouter />
        <BackButton />
      </>,
      "/account",
    );

    await user.click(await screen.findByRole("link", { name: "Account settings" }));
    await screen.findByRole("heading", { level: 1, name: "Account settings" });
    await user.type(screen.getByLabelText("Current password"), PASSWORD);
    await user.type(screen.getByLabelText(`Type ${PHRASE} to confirm`), PHRASE);
    await user.click(screen.getByRole("button", { name: "Delete my account" }));

    await screen.findByText(ACCOUNT_DELETED_MESSAGE);
    await user.click(screen.getByRole("button", { name: "history back" }));

    await waitFor(() => {
      expect(location()).toBe("/login");
    });
    expect(screen.queryByLabelText("Current password")).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete my account" })).toBeNull();
    expect(screen.queryByRole("form", { name: "Delete your account" })).toBeNull();
  });
});

describe("failed deletion", () => {
  async function expectStaysSignedIn(api: ApiMock, message: string | RegExp) {
    const alert = await screen.findByRole("alert");

    expect(alert).toHaveTextContent(message);
    expect(location()).toBe("/account/settings");
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.queryByText(ACCOUNT_DELETED_MESSAGE)).toBeNull();
    expect(api.calls(ROUTE)).toHaveLength(1);
    expect(screen.getByRole("form", { name: "Delete your account" })).toBeInTheDocument();
  }

  it.each([
    [
      "wrong password",
      400,
      { detail: "x", code: "invalid_current_password" },
      undefined,
      "The current password is incorrect.",
    ],
    [
      "confirmation mismatch",
      400,
      { detail: "x", code: "account_deletion_confirmation_mismatch" },
      undefined,
      /DELETE MY ACCOUNT/,
    ],
    [
      "unavailable",
      409,
      { detail: "x", code: "account_deletion_unavailable" },
      undefined,
      "Account deletion is unavailable for this account.",
    ],
    [
      "throttled",
      429,
      { detail: "x", code: "account_deletion_throttled" },
      { "Retry-After": "120" },
      /account deletion/,
    ],
  ])("shows a safe message for %s", async (_name, status, body, headers, message) => {
    const api = mockPersonalApi();
    api.reply(ROUTE, status, body, headers);
    renderSettings(api);

    const { user, password, submit } = await fillAndSubmit();

    await expectStaysSignedIn(api, message);

    // The form is usable again once refilled.
    expect(submit).toBeDisabled();
    await user.type(password, PASSWORD);
    expect(submit).toBeEnabled();
    expect(submit).not.toHaveAttribute("aria-busy", "true");
  });

  it("never renders a raw server detail", async () => {
    const api = mockPersonalApi();
    api.reply(ROUTE, 503, {
      detail: "RAW-SERVER-DETAIL",
      code: "account_deletion_notice_unavailable",
    });
    renderSettings(api);

    await fillAndSubmit();

    await expectStaysSignedIn(api, GENERIC_FAILURE);
    expect(document.body.textContent).not.toContain("RAW-SERVER-DETAIL");
  });

  it("shows the connection message on a network failure", async () => {
    const api = mockPersonalApi();
    api.on(ROUTE, () => {
      throw new TypeError("Failed to fetch");
    });
    renderSettings(api);

    await fillAndSubmit();

    await expectStaysSignedIn(api, CONNECTION_ERROR_MESSAGE);
  });

  it("ends the session and redirects to sign in on 401", async () => {
    const api = mockPersonalApi();
    api.on(ROUTE, () => {
      api.reply("GET /api/auth/me", 401, { detail: "Authentication is required." });

      return jsonResponse(401, { detail: "Authentication is required." });
    });
    const { queryClient } = renderSettings(api);

    await fillAndSubmit();

    await waitFor(() => {
      expect(location()).toBe("/login");
    });
    expect(screen.queryByRole("button", { name: "Delete my account" })).toBeNull();
    expect(screen.queryByText(ACCOUNT_DELETED_MESSAGE)).toBeNull();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
  });
});

describe("duplicate submission", () => {
  it("sends exactly one request while pending and exposes busy state", async () => {
    const api = mockPersonalApi();
    let release: () => void = () => undefined;
    api.on(ROUTE, async () => {
      await new Promise<void>((resolve) => {
        release = resolve;
      });

      return jsonResponse(400, { detail: "x", code: "invalid_current_password" });
    });
    renderSettings(api);
    const { user, confirmation } = await fillAndSubmit();

    const busy = await screen.findByRole("button", { name: "Deleting your account…" });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");

    await user.click(busy);
    await user.click(busy);
    await user.type(confirmation, "{Enter}");

    expect(api.calls(ROUTE)).toHaveLength(1);

    release();

    await screen.findByRole("alert");
    expect(api.calls(ROUTE)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Delete my account" })).toHaveAttribute(
      "aria-busy",
      "false",
    );
  });

  it("does not submit with Enter while the form is incomplete", async () => {
    const api = mockPersonalApi();
    api.reply(ROUTE, 200, DELETED_BODY);
    renderSettings(api);
    const { user, password, confirmation } = await openForm();

    await user.type(password, PASSWORD);
    await user.type(confirmation, "DELETE{Enter}");

    expect(api.calls(ROUTE)).toHaveLength(0);
  });
});

describe("legacy compatibility", () => {
  it("sends a legacy workspace administrator away from account settings", async () => {
    const api = createApiMock();
    signInAs(api, ADMIN_USER, "active");
    renderWithProviders(<AppRouter />, "/account/settings");

    await screen.findByText("Application home");
    expect(location()).toBe("/");
    expect(screen.queryByRole("button", { name: "Delete my account" })).toBeNull();
    expect(screen.queryByRole("form", { name: "Delete your account" })).toBeNull();
    expect(screen.queryByLabelText("Current password")).toBeNull();
    expect(api.calls(ROUTE)).toHaveLength(0);
  });

  it("does not show workspace deletion recovery to a personal account", async () => {
    mockPersonalApi();
    renderWithProviders(<AppRouter />, "/workspace-deletion");

    await waitFor(() => {
      expect(location()).not.toBe("/workspace-deletion");
    });
    expect(
      screen.queryByRole("heading", { name: /scheduled for deletion/i }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel deletion" })).toBeNull();
    expect(screen.queryByRole("form", { name: "Cancel workspace deletion" })).toBeNull();
  });
});
