import { screen } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppRouter } from "../../router";
import {
  createApiMock,
  renderWithProviders,
} from "../../test/accountHarness";

vi.mock("../../App", () => ({
  default: () => <h1>Application home</h1>,
}));

const ACCEPTED = {
  verification_required: true,
};

const RESEND_ACCEPTED = {
  detail:
    "If an eligible administrator account exists for that email address, verification instructions will be sent. Check your email for the next step.",
  code: "verification_resend_accepted",
};

async function fillRegistration(
  user: UserEvent,
  overrides: Partial<Record<string, string>> = {},
) {
  const values = {
    "Display name": "Example Person",
    Email: "person@example.test",
    Password: "correct horse battery",
    "Confirm password": "correct horse battery",
    ...overrides,
  };

  for (const [label, value] of Object.entries(values)) {
    const field = screen.getByLabelText(label);

    await user.clear(field);

    if (value) {
      await user.type(field, value);
    }
  }
}

async function openRegistration() {
  renderWithProviders(<AppRouter />, "/register");

  return screen.findByRole("heading", { name: "Create your account" });
}

describe("personal account registration", () => {
  it("requires every field and connects errors to their fields", async () => {
    const api = createApiMock();
    const user = userEvent.setup();

    await openRegistration();
    await user.click(screen.getByRole("button", { name: "Create account" }));

    const displayName = screen.getByLabelText("Display name");
    expect(displayName).toHaveAttribute("aria-invalid", "true");
    expect(displayName).toHaveFocus();
    expect(displayName).toHaveAccessibleDescription(expect.stringMatching(/\S/));
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByLabelText(/Workspace/i)).not.toBeInTheDocument();
    expect(api.calls("POST /api/registration")).toHaveLength(0);
  });

  it("catches password confirmation mismatch before submitting", async () => {
    const api = createApiMock();
    const user = userEvent.setup();

    await openRegistration();
    await fillRegistration(user, { "Confirm password": "different horse battery" });
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(screen.getByLabelText("Confirm password")).toHaveAccessibleDescription(
      "Passwords do not match.",
    );
    expect(api.calls("POST /api/registration")).toHaveLength(0);
  });

  it("submits the backend contract and shows the verification-pending state", async () => {
    const api = createApiMock();
    api.reply("POST /api/registration", 201, ACCEPTED);
    const user = userEvent.setup();

    await openRegistration();
    await fillRegistration(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Check your inbox" })).toBeInTheDocument();

    const pending = document.body.textContent ?? "";
    expect(pending).toContain("will be sent");
    expect(pending).toContain("Check your email for the next step");
    expect(pending).not.toMatch(/arrive shortly|has been sent|delivered/i);

    const [request] = api.calls("POST /api/registration");
    expect(request.body).toEqual({
      display_name: "Example Person",
      email: "person@example.test",
      password: "correct horse battery",
      confirm_password: "correct horse battery",
    });
    expect(request.headers.get("X-CSRF-Token")).toBe("pre-auth-csrf");

    // Passwords are not retained on the pending screen or in the page.
    expect(document.body.innerHTML).not.toContain("correct horse battery");
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(screen.getByTestId("location")).toHaveTextContent("/register");
  });

  it("renders duplicate-email success exactly like an ordinary registration", async () => {
    async function pendingMarkup(email: string) {
      const api = createApiMock();
      api.reply("POST /api/registration", 201, ACCEPTED);
      const user = userEvent.setup();
      const view = renderWithProviders(<AppRouter />, "/register");

      await screen.findByRole("heading", { name: "Create your account" });
      await fillRegistration(user, { Email: email });
      await user.click(screen.getByRole("button", { name: "Create account" }));
      await screen.findByRole("heading", { name: "Check your inbox" });

      const markup = view.container.innerHTML.replaceAll(email, "EMAIL");
      view.unmount();

      return markup;
    }

    const fresh = await pendingMarkup("new-owner@example.test");
    const duplicate = await pendingMarkup("existing-owner@example.test");

    expect(duplicate).toBe(fresh);
    expect(fresh).not.toMatch(/already (registered|exists)/i);
  });

  it("does not treat a 503 email_delivery_unavailable response as pending verification", async () => {
    const api = createApiMock();
    api.reply("POST /api/registration", 503, {
      detail: "The request could not be completed.",
      code: "email_delivery_unavailable",
      retryable: true,
    });
    const user = userEvent.setup();

    await openRegistration();
    await fillRegistration(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(/provider|resend|delivery|queued/i);
    expect(
      screen.queryByRole("heading", { name: "Check your inbox" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveValue("person@example.test");
    expect(document.body.textContent).not.toMatch(/will be sent/i);
    expect(api.calls("POST /api/registration")).toHaveLength(1);
  });

  it("shows closed registration safely", async () => {
    const api = createApiMock();
    api.reply("POST /api/registration", 503, {
      detail: "Registration is unavailable.",
      code: "registration_unavailable",
      retryable: false,
    });
    const user = userEvent.setup();

    await openRegistration();
    await fillRegistration(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Registration is currently closed. Try again later.",
    );
  });

  it("uses Retry-After when registration is throttled", async () => {
    const api = createApiMock();
    api.reply(
      "POST /api/registration",
      429,
      { detail: "Registration is temporarily unavailable.", code: "registration_throttled", retryable: true },
      { "Retry-After": "45" },
    );
    const user = userEvent.setup();

    await openRegistration();
    await fillRegistration(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many registration attempts. Try again in 45 seconds.",
    );
  });

  it("prevents duplicate submission while registering", async () => {
    const api = createApiMock();
    let release: (response: Response) => void = () => undefined;
    api.on(
      "POST /api/registration",
      () => new Promise<Response>((resolve) => {
        release = resolve;
      }),
    );
    const user = userEvent.setup();

    await openRegistration();
    await fillRegistration(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));

    const busy = await screen.findByRole("button", { name: "Creating account…" });
    expect(busy).toBeDisabled();
    await user.click(busy);
    expect(api.calls("POST /api/registration")).toHaveLength(1);

    release(new Response(JSON.stringify(ACCEPTED), { status: 201 }));
    expect(await screen.findByRole("heading", { name: "Check your inbox" })).toBeInTheDocument();
  });

  it("resends verification with generic wording from the pending screen", async () => {
    const api = createApiMock();
    api.reply("POST /api/registration", 201, ACCEPTED);
    api.reply("POST /api/registration/resend-verification", 202, RESEND_ACCEPTED);
    const user = userEvent.setup();

    await openRegistration();
    await fillRegistration(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await user.click(await screen.findByRole("button", { name: "Resend verification email" }));

    expect(await screen.findByRole("status")).toHaveTextContent(RESEND_ACCEPTED.detail);
    expect(api.calls("POST /api/registration/resend-verification")[0].body).toEqual({
      email: "person@example.test",
    });
  });
});
