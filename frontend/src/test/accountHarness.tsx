import type { ReactNode } from "react";
import { render } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  MemoryRouter,
  type InitialEntry,
} from "react-router-dom";
import { vi } from "vitest";

import { AuthProvider } from "../auth/AuthProvider";
import type {
  AuthenticatedUser,
  OnboardingStage,
  ResolvedWorkspaceAccess,
  WorkspaceLifecycle,
} from "../types/auth";
import type { SetupStatus } from "../types/setup";
import { LocationDisplay } from "./LocationDisplay";

export interface RecordedRequest {
  method: string;
  path: string;
  search: string;
  body: unknown;
  headers: Headers;
}

type Handler = (request: RecordedRequest) => Response | Promise<Response>;

export function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...headers,
    },
  });
}

export const LEGACY_ACTIVE_SETUP: SetupStatus = {
  status: "active",
  installation_exists: true,
  bootstrap_available: false,
  administrator_configured: true,
  provider_configured: true,
  spectator_configured: true,
  active_league_configured: true,
};

/** A SaaS deployment with no legacy installation. */
export const SAAS_ONLY_SETUP: SetupStatus = {
  status: "setup_required",
  installation_exists: false,
  bootstrap_available: false,
  administrator_configured: true,
  provider_configured: false,
  spectator_configured: false,
  active_league_configured: false,
};

export const ACTIVE_WORKSPACE: WorkspaceLifecycle = {
  workspace_id: 7,
  workspace_slug: "alpha-league",
  installation_status: "active",
  registration_status: "active",
  onboarding_stage: "complete",
  workspace_access: "active",
};

/** The GET /api/auth/me workspace object for a given access state. */
export function workspaceFor(
  access: ResolvedWorkspaceAccess,
  {
    onboardingStage = "provider_setup_pending",
    workspaceId = ACTIVE_WORKSPACE.workspace_id,
    workspaceSlug = ACTIVE_WORKSPACE.workspace_slug,
  }: {
    onboardingStage?: OnboardingStage;
    workspaceId?: number;
    workspaceSlug?: string | null;
  } = {},
): WorkspaceLifecycle {
  const identity = { workspace_id: workspaceId, workspace_slug: workspaceSlug };

  switch (access) {
    case "active":
      return { ...ACTIVE_WORKSPACE, ...identity };
    case "provisional":
      return {
        ...identity,
        installation_status: "setup_in_progress",
        registration_status: "provisional",
        onboarding_stage: onboardingStage,
        workspace_access: "provisional",
      };
    case "suspended":
      return {
        ...identity,
        installation_status: "active",
        registration_status: "suspended",
        onboarding_stage: "complete",
        workspace_access: "suspended",
      };
    case "inactive":
      return {
        ...identity,
        installation_status: null,
        registration_status: null,
        onboarding_stage: null,
        workspace_access: "inactive",
      };
  }
}

export const ADMIN_USER: AuthenticatedUser = {
  id: 11,
  display_name: "Example Administrator",
  email: "administrator@example.test",
  role: "admin",
  is_active: true,
  workspace: ACTIVE_WORKSPACE,
};

export const SPECTATOR_USER: AuthenticatedUser = {
  id: 12,
  display_name: "League Spectator",
  email: null,
  role: "spectator",
  is_active: true,
  workspace: { ...ACTIVE_WORKSPACE, onboarding_stage: null },
};

export const GENERIC_UNAUTHENTICATED = {
  detail: "Authentication is required.",
};

export const SESSION_REPLACED_BODY = {
  detail:
    "This administrator account was signed in from another device. Sign in again to continue.",
  code: "session_replaced",
  retryable: false,
};

/**
 * Route-table fetch mock keyed by "METHOD /api/path".
 *
 * Unhandled requests return 404 so unexpected calls are visible.
 */
export function createApiMock() {
  const handlers = new Map<string, Handler>();
  const requests: RecordedRequest[] = [];

  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), "http://localhost");
      const method = (init.method ?? "GET").toUpperCase();
      const recorded: RecordedRequest = {
        method,
        path: url.pathname,
        search: url.search,
        body:
          typeof init.body === "string" ? JSON.parse(init.body) : undefined,
        headers: new Headers(init.headers),
      };

      requests.push(recorded);

      const handler = handlers.get(`${method} ${url.pathname}`);

      if (handler === undefined) {
        return jsonResponse(404, { detail: "Not found." });
      }

      return handler(recorded);
    },
  );

  vi.stubGlobal("fetch", fetchMock);

  const api = {
    requests,
    on(route: string, handler: Handler | Response | (() => Response)) {
      handlers.set(
        route,
        typeof handler === "function"
          ? (handler as Handler)
          : () => (handler as Response).clone(),
      );

      return api;
    },
    reply(route: string, status: number, body: unknown, headers?: Record<string, string>) {
      handlers.set(route, () => jsonResponse(status, body, headers));

      return api;
    },
    calls(route: string): RecordedRequest[] {
      const [method, path] = route.split(" ");

      return requests.filter(
        (request) => request.method === method && request.path === path,
      );
    },
  };

  // Defaults: legacy-active installation, anonymous visitor, CSRF issued.
  api.reply("GET /api/setup/status", 200, LEGACY_ACTIVE_SETUP);
  api.reply("GET /api/auth/me", 401, GENERIC_UNAUTHENTICATED);
  api.reply("GET /api/auth/csrf", 200, { csrf_token: "pre-auth-csrf" });

  return api;
}

export type ApiMock = ReturnType<typeof createApiMock>;

/**
 * Make GET /api/auth/me reflect a session, including its authoritative
 * workspace lifecycle. No other endpoint decides lifecycle state.
 */
export function signInAs(
  api: ApiMock,
  user: AuthenticatedUser,
  access?: ResolvedWorkspaceAccess,
  options?: { onboardingStage?: OnboardingStage },
) {
  const body: AuthenticatedUser =
    access === undefined
      ? user
      : {
          ...user,
          workspace: workspaceFor(access, {
            ...options,
            workspaceId: user.workspace.workspace_id,
            workspaceSlug: user.workspace.workspace_slug,
          }),
        };

  api.reply("GET /api/auth/me", 200, body);

  return body;
}

export function renderWithProviders(
  ui: ReactNode,
  initialPath: InitialEntry = "/",
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AuthProvider>
          {ui}
          <LocationDisplay />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  return { ...view, queryClient };
}
