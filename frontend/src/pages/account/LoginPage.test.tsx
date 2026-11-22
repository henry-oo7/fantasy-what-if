import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppRouter } from "../../router";
import {
  ADMIN_USER,
  SPECTATOR_USER,
  createApiMock,
  renderWithProviders,
  signInAs,
} from "../../test/accountHarness";

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

async function openLogin(path = "/login") {
  renderWithProviders(<AppRouter />, path);

  return screen.findByRole("heading", { name: "Sign in to continue" });
}

describe("administrator email login", () => {
  it("submits email and password to the administrator route, never username", async () => {
    const api = createApiMock();
    api.on("POST /api/auth/admin/login", () => {
      signInAs(api, ADMIN_USER);

      return new Response(
        JSON.stringify({
          user: { ...ADMIN_USER, workspace_id: 3 },
          csrf_token: "session-csrf",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    const user = userEvent.setup();

    await openLogin();
    await user.type(screen.getByLabelText("Email"), "administrator@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();

    const [request] = api.calls("POST /api/auth/admin/login");
    expect(request.body).toEqual({
      email: "administrator@example.test",
      password: "correct horse battery",
    });
    expect(request.headers.get("X-CSRF-Token")).toBe("pre-auth-csrf");
    expect(api.calls("POST /api/auth/login")).toHaveLength(0);
    expect(JSON.stringify(api.requests.map((entry) => entry.body))).not.toContain("username");
  });

  it("shows the safe credential message on 401 and keeps the entered email", async () => {
    const api = createApiMock();
    api.reply("POST /api/auth/admin/login", 401, { detail: "Invalid email or password." });
    const user = userEvent.setup();

    await openLogin();
    await user.type(screen.getByLabelText("Email"), "someone@example.test");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(screen.getByLabelText("Email")).toHaveValue("someone@example.test");
  });

  it("uses Retry-After for throttled sign-in", async () => {
    const api = createApiMock();
    api.reply(
      "POST /api/auth/admin/login",
      429,
      { detail: "Invalid email or password." },
      { "Retry-After": "120" },
    );
    const user = userEvent.setup();

    await openLogin();
    await user.type(screen.getByLabelText("Email"), "someone@example.test");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many sign-in attempts. Try again in 2 minutes.",
    );
  });

  it("routes a provisional administrator to onboarding, not the application", async () => {
    const api = createApiMock();
    api.on("POST /api/auth/admin/login", () => {
      signInAs(api, ADMIN_USER, "provisional");

      return new Response(
        JSON.stringify({ user: { ...ADMIN_USER, workspace_id: 3 }, csrf_token: "c" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    const user = userEvent.setup();

    await openLogin();
    await user.type(screen.getByLabelText("Email"), "administrator@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByRole("heading", { name: "Your workspace is reserved" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/onboarding");
    expect(api.requests.some((request) => request.path.startsWith("/api/setup/") && request.method === "POST")).toBe(false);
  });

  it("returns an active administrator to a safe in-app destination", async () => {
    const api = createApiMock();
    api.on("POST /api/auth/admin/login", () => {
      signInAs(api, ADMIN_USER);

      return new Response(
        JSON.stringify({ user: { ...ADMIN_USER, workspace_id: 3 }, csrf_token: "c" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    const user = userEvent.setup();

    renderWithProviders(<AppRouter />, "/all-play?season_id=4");

    expect(await screen.findByRole("heading", { name: "Sign in to continue" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "administrator@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("/all-play?season_id=4");
    });
  });
});

describe("spectator workspace login", () => {
  it("submits workspace slug and shared password to the spectator route", async () => {
    const api = createApiMock();
    api.on("POST /api/auth/spectator/login", () => {
      signInAs(api, SPECTATOR_USER);

      return new Response(
        JSON.stringify({
          user: { ...SPECTATOR_USER, workspace_id: 3, workspace_slug: "example-league" },
          csrf_token: "c",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    const user = userEvent.setup();

    await openLogin();
    await user.click(screen.getByRole("radio", { name: "Spectator" }));
    await user.type(screen.getByLabelText("Workspace", { exact: false }), "example-league");
    await user.type(screen.getByLabelText("Spectator password"), "shared passphrase");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Application home" })).toBeInTheDocument();

    const [request] = api.calls("POST /api/auth/spectator/login");
    expect(request.body).toEqual({
      workspace_slug: "example-league",
      password: "shared passphrase",
    });
    expect(api.calls("POST /api/auth/admin/login")).toHaveLength(0);
  });

  it("uses one safe failure message that does not reveal workspace existence", async () => {
    const api = createApiMock();
    api.reply("POST /api/auth/spectator/login", 401, {
      detail: "The workspace or spectator credentials are incorrect.",
    });
    const user = userEvent.setup();

    await openLogin();
    await user.click(screen.getByRole("radio", { name: "Spectator" }));
    await user.type(screen.getByLabelText("Workspace", { exact: false }), "missing-league");
    await user.type(screen.getByLabelText("Spectator password"), "anything");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The workspace or spectator credentials are incorrect.");
    expect(alert.textContent).not.toMatch(/not found|does not exist|unknown workspace/i);
  });

  it("maps schema validation errors to the same safe message", async () => {
    const api = createApiMock();
    api.reply("POST /api/auth/spectator/login", 422, {
      detail: [{ loc: ["body", "workspace_slug"], msg: "too short" }],
    });
    const user = userEvent.setup();

    await openLogin();
    await user.click(screen.getByRole("radio", { name: "Spectator" }));
    await user.type(screen.getByLabelText("Workspace", { exact: false }), "ab");
    await user.type(screen.getByLabelText("Spectator password"), "anything");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The workspace or spectator credentials are incorrect.",
    );
  });
});

describe("login form behavior", () => {
  it("makes authentication mode explicit and keyboard operable", async () => {
    createApiMock();
    const user = userEvent.setup();

    await openLogin();

    const administrator = screen.getByRole("radio", { name: "Administrator" });
    const spectator = screen.getByRole("radio", { name: "Spectator" });

    expect(administrator).toBeChecked();
    administrator.focus();
    await user.keyboard("{ArrowRight}");
    expect(spectator).toBeChecked();
    expect(screen.getByLabelText("Spectator password")).toBeInTheDocument();
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
  });

  it("names the password visibility control accessibly", async () => {
    createApiMock();
    const user = userEvent.setup();

    await openLogin();

    const toggle = screen.getByRole("button", { name: "Show password" });
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");

    await user.click(toggle);

    expect(screen.getByRole("button", { name: "Hide password" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "text");
  });

  it("prevents duplicate submission while signing in", async () => {
    const api = createApiMock();
    let release: (response: Response) => void = () => undefined;
    api.on(
      "POST /api/auth/admin/login",
      () => new Promise<Response>((resolve) => {
        release = resolve;
      }),
    );
    const user = userEvent.setup();

    await openLogin();
    await user.type(screen.getByLabelText("Email"), "administrator@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const busy = await screen.findByRole("button", { name: "Signing in…" });
    expect(busy).toBeDisabled();
    expect(screen.getByLabelText("Email")).toHaveValue("administrator@example.test");

    await user.click(busy);
    expect(api.calls("POST /api/auth/admin/login")).toHaveLength(1);

    release(new Response(JSON.stringify({ detail: "Invalid email or password." }), { status: 401 }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
