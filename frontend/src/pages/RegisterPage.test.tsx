import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
} from "react-router-dom";
import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import { AuthProvider } from "../auth/AuthProvider";
import { RegisterPage } from "./RegisterPage";

function jsonResponse(
  body: unknown,
  status = 200,
): Response {
  return new Response(
    JSON.stringify(body),
    {
      status,
      headers: {
        "Content-Type": "application/json",
      },
    },
  );
}

function renderRegistration(
  path = "/register?invite=test-invitation-token-value",
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <Routes>
            <Route
              path="/register"
              element={<RegisterPage />}
            />
            <Route
              path="/"
              element={<p>Dashboard</p>}
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("registration page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the team assigned by the invitation", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL) => {
        const url = String(input);

        if (url.includes("/auth/me")) {
          return jsonResponse(
            {
              detail:
                "Authentication is required.",
            },
            401,
          );
        }

        if (url.includes("/auth/invitations/")) {
          return jsonResponse({
            valid: true,
            team_id: 2,
            team_name:
              "STAIRWAY TO EVANS",
            season: 2026,
            intended_display_name:
              "Registration Browser Test",
            expires_at:
              "2026-11-13T17:00:00+00:00",
          });
        }

        throw new Error(
          `Unexpected request: ${url}`,
        );
      },
    );

    vi.stubGlobal("fetch", fetchMock);

    renderRegistration();

    expect(
      await screen.findByText(
        "STAIRWAY TO EVANS",
      ),
    ).toBeInTheDocument();

    expect(
      screen.getByText("2026 season"),
    ).toBeInTheDocument();

    const displayName = screen.getByLabelText(
      "Display name",
    );

    expect(displayName).toHaveValue(
      "Registration Browser Test",
    );
    expect(displayName).toHaveAttribute(
      "readonly",
    );
  });

  it("shows an error when the link has no token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            detail:
              "Authentication is required.",
          },
          401,
        ),
      ),
    );

    renderRegistration("/register");

    expect(
      await screen.findByText(
        "This registration link cannot be used",
      ),
    ).toBeInTheDocument();

    expect(
      screen.getByText(
        "This registration link does not contain an invitation token.",
      ),
    ).toBeInTheDocument();
  });

  it("shows an unavailable invitation response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (
          input: RequestInfo | URL,
        ) => {
          const url = String(input);

          if (url.includes("/auth/me")) {
            return jsonResponse(
              {
                detail:
                  "Authentication is required.",
              },
              401,
            );
          }

          return jsonResponse(
            {
              detail:
                "Registration invitation has expired.",
            },
            404,
          );
        },
      ),
    );

    renderRegistration();

    await waitFor(() => {
      expect(
        screen.getByText(
          "Registration invitation has expired.",
        ),
      ).toBeInTheDocument();
    });
  });
});
