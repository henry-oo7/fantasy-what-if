import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  apiRequest,
  getJson,
  postJson,
} from "./client";

describe("API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("includes browser credentials on GET requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "ok",
        }),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    const result = await getJson<{
      status: string;
    }>("/test");

    expect(result).toEqual({
      status: "ok",
    });

    expect(fetchMock).toHaveBeenCalledOnce();

    const [, options] = fetchMock.mock.calls[0];

    expect(options).toMatchObject({
      method: "GET",
      credentials: "include",
    });
  });

  it("adds JSON and CSRF headers to POST requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          saved: true,
        }),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json",
          },
        },
      ),
    );

    vi.stubGlobal("fetch", fetchMock);

    await postJson(
      "/test",
      {
        value: 123,
      },
      {
        csrfToken: "csrf-token",
      },
    );

    const [, options] = fetchMock.mock.calls[0];
    const headers = new Headers(options.headers);

    expect(options).toMatchObject({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({
        value: 123,
      }),
    });

    expect(
      headers.get("Content-Type"),
    ).toBe("application/json");

    expect(
      headers.get("X-CSRF-Token"),
    ).toBe("csrf-token");
  });

  it("surfaces backend error details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail:
              "Invalid username or password.",
          }),
          {
            status: 401,
            headers: {
              "Content-Type":
                "application/json",
            },
          },
        ),
      ),
    );

    await expect(
      apiRequest("/auth/login"),
    ).rejects.toEqual(
      expect.objectContaining({
        name: "ApiError",
        message:
          "Invalid username or password.",
        status: 401,
        detail:
          "Invalid username or password.",
      }),
    );
  });

  it("uses a fallback for non-JSON errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("Bad gateway", {
          status: 502,
        }),
      ),
    );

    await expect(
      getJson("/test"),
    ).rejects.toEqual(
      expect.objectContaining({
        status: 502,
        detail:
          "Request failed with status 502.",
      }),
    );
  });
});
