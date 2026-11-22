import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppRouter } from "../../router";
import {
  createApiMock,
  renderWithProviders,
} from "../../test/accountHarness";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

const RESET_TOKEN = "example-reset-token-value";

const REQUEST_ACCEPTED = {
  detail:
    "If an eligible administrator account exists for that email address, password reset instructions have been sent.",
  code: "password_reset_request_accepted",
};

describe("password reset request", () => {
  it.each([
    "known-administrator@example.test",
    "unknown-person@example.test",
  ])("always shows the backend generic accepted wording (%s)", async (email) => {
    const api = createApiMock();
    api.reply("POST /api/auth/password-reset/request", 202, REQUEST_ACCEPTED);
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/forgot-password");

    await user.type(await screen.findByLabelText("Administrator email"), email);
    await user.click(screen.getByRole("button", { name: "Send reset instructions" }));

    expect(await screen.findByRole("status")).toHaveTextContent(REQUEST_ACCEPTED.detail);
    expect(api.calls("POST /api/auth/password-reset/request")[0].body).toEqual({ email });
    expect(api.calls("POST /api/auth/password-reset/request")[0].headers.get("X-CSRF-Token")).toBe(
      "pre-auth-csrf",
    );
  });

  it("handles throttling with Retry-After", async () => {
    const api = createApiMock();
    api.reply(
      "POST /api/auth/password-reset/request",
      429,
      { detail: "Password reset is temporarily unavailable.", code: "password_reset_throttled", retryable: true },
      { "Retry-After": "60" },
    );
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/forgot-password");

    await user.type(await screen.findByLabelText("Administrator email"), "someone@example.test");
    await user.click(screen.getByRole("button", { name: "Send reset instructions" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many password reset attempts. Try again in 1 minute.",
    );
  });
});

describe("password reset completion", () => {
  it("removes the token from the visible URL immediately", async () => {
    createApiMock();

    renderWithProviders(<AppRouter />, `/reset-password?token=${RESET_TOKEN}`);

    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/reset-password");
    });
    expect(screen.getByTestId("location")).not.toHaveTextContent("token");
    expect(document.body.innerHTML).not.toContain(RESET_TOKEN);
  });

  it("catches password mismatch client-side", async () => {
    const api = createApiMock();
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, `/reset-password?token=${RESET_TOKEN}`);

    await user.type(await screen.findByLabelText("New password"), "a completely new passphrase");
    await user.type(screen.getByLabelText("Confirm new password"), "a different new passphrase");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(screen.getByLabelText("Confirm new password")).toHaveAccessibleDescription(
      "Passwords do not match.",
    );
    expect(api.calls("POST /api/auth/password-reset/complete")).toHaveLength(0);
  });

  it("submits the captured token and routes to administrator login without a session", async () => {
    const api = createApiMock();
    api.reply("POST /api/auth/password-reset/complete", 200, {
      detail: "Your password has been reset. Sign in again with your administrator email.",
      code: "password_reset_completed",
      revoked_sessions: 2,
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, `/reset-password?token=${RESET_TOKEN}`);

    await user.type(await screen.findByLabelText("New password"), "a completely new passphrase");
    await user.type(screen.getByLabelText("Confirm new password"), "a completely new passphrase");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Your password has been reset. Sign in with your administrator email.",
    );
    expect(screen.getByRole("radio", { name: "Administrator" })).toBeChecked();

    const [request] = api.calls("POST /api/auth/password-reset/complete");
    expect(request.body).toEqual({
      token: RESET_TOKEN,
      new_password: "a completely new passphrase",
    });

    // No authenticated state was assumed: /me is only the initial probe.
    expect(api.calls("GET /api/auth/me")).toHaveLength(1);
    expect(document.body.innerHTML).not.toContain("a completely new passphrase");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("offers a new reset link when the token is invalid or used", async () => {
    const api = createApiMock();
    api.reply("POST /api/auth/password-reset/complete", 400, {
      detail: "The password reset link is invalid or expired.",
      code: "invalid_password_reset",
      retryable: false,
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, `/reset-password?token=${RESET_TOKEN}`);

    await user.type(await screen.findByLabelText("New password"), "a completely new passphrase");
    await user.type(screen.getByLabelText("Confirm new password"), "a completely new passphrase");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This password reset link is invalid, expired, or already used.",
    );
    expect(screen.getByRole("link", { name: "Request a new reset link" })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("explains a missing token without a form", async () => {
    createApiMock();

    renderWithProviders(<AppRouter />, "/reset-password");

    expect(
      await screen.findByText(/needs the password reset link from your email/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("New password")).not.toBeInTheDocument();
  });
});
