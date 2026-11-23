import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  createApiMock,
  isRetiredApiPath,
  renderWithProviders,
  signInAs,
} from "../../test/accountHarness";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

const TOKEN = "example-verification-token-value";

const VERIFIED = {
  workspace_slug: "example-league",
  display_name: "Example Administrator",
  email: "administrator@example.test",
  role: "admin",
  registration_status: "provisional",
  onboarding_stage: "provider_setup_pending",
  csrf_token: "session-csrf",
};

describe("email verification", () => {
  it("submits the token once, removes it from the URL, and opens onboarding", async () => {
    const api = createApiMock();
    api.on("POST /api/registration/verify-email", () => {
      signInAs(api, ADMIN_USER, "provisional");

      return new Response(JSON.stringify(VERIFIED), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    renderWithProviders(
      <StrictMode>
        <AppRouter />
      </StrictMode>,
      `/verify-email?token=${TOKEN}`,
    );

    expect(
      await screen.findByRole("heading", { name: "Your workspace is reserved" }),
    ).toBeInTheDocument();

    const submissions = api.calls("POST /api/registration/verify-email");
    expect(submissions).toHaveLength(1);
    expect(submissions[0].body).toEqual({ token: TOKEN });
    expect(submissions[0].headers.get("X-CSRF-Token")).toBe("pre-auth-csrf");

    expect(screen.getByTestId("location")).toHaveTextContent("/onboarding");
    expect(screen.getByTestId("location")).not.toHaveTextContent(TOKEN);
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);

    // Onboarding never touches retired installation setup endpoints.
    expect(api.requests.some((request) => isRetiredApiPath(request.path))).toBe(false);
  });

  it("removes the token from the visible URL even when verification fails", async () => {
    const api = createApiMock();
    api.reply("POST /api/registration/verify-email", 400, {
      detail: "The email verification link is invalid or expired.",
      code: "invalid_email_verification",
      retryable: false,
    });

    renderWithProviders(<AppRouter />, `/verify-email?token=${TOKEN}`);

    expect(
      await screen.findByText(/invalid, expired, or already used/),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/verify-email");
    });
    expect(screen.getByTestId("location")).not.toHaveTextContent("token");
    expect(document.body.innerHTML).not.toContain(TOKEN);
  });

  it("offers a generic resend path after an invalid link", async () => {
    const api = createApiMock();
    api.reply("POST /api/registration/verify-email", 400, {
      detail: "The email verification link is invalid or expired.",
      code: "invalid_email_verification",
      retryable: false,
    });
    api.reply("POST /api/registration/resend-verification", 202, {
      detail:
        "If an eligible administrator account exists for that email address, verification instructions have been sent.",
      code: "verification_resend_accepted",
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, `/verify-email?token=${TOKEN}`);

    await user.type(await screen.findByLabelText("Registration email"), "administrator@example.test");
    await user.click(screen.getByRole("button", { name: "Resend verification email" }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("If an eligible administrator account exists");
    expect(status.textContent).not.toMatch(/we sent|has been sent to your account/i);
  });

  it("handles Retry-After on resend without exposing throttle details", async () => {
    const api = createApiMock();
    api.reply(
      "POST /api/registration/resend-verification",
      429,
      {
        detail: "Verification resend is temporarily unavailable. Try again later.",
        code: "verification_resend_throttled",
        retryable: true,
      },
      { "Retry-After": "900" },
    );
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/verify-email");

    await user.type(await screen.findByLabelText("Registration email"), "administrator@example.test");
    await user.click(screen.getByRole("button", { name: "Resend verification email" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Too many verification email attempts. Try again in 15 minutes.",
    );
    expect(alert.textContent).not.toMatch(/hash|bucket|identity/i);
  });

  it("explains a missing link without submitting anything", async () => {
    const api = createApiMock();

    renderWithProviders(<AppRouter />, "/verify-email");

    expect(
      await screen.findByText(/needs the verification link from your email/),
    ).toBeInTheDocument();
    expect(api.calls("POST /api/registration/verify-email")).toHaveLength(0);
  });

  it("shows a safe state when the workspace can no longer be verified", async () => {
    const api = createApiMock();
    api.reply("POST /api/registration/verify-email", 409, {
      detail: "Email verification is unavailable.",
      code: "email_verification_unavailable",
      retryable: false,
    });

    renderWithProviders(<AppRouter />, `/verify-email?token=${TOKEN}`);

    expect(
      await screen.findByRole("heading", { name: "Verification is unavailable" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });
});
