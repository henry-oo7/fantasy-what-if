import {
  afterEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import {
  ApiError,
  apiRequest,
  getJson,
  parseRetryAfter,
  postJson,
  subscribeToApiErrors,
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

  it("exposes code, retryable, and Retry-After from coded errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: "Registration is temporarily unavailable. Try again later.",
            code: "registration_throttled",
            retryable: true,
          }),
          {
            status: 429,
            headers: {
              "Content-Type": "application/json",
              "Retry-After": "300",
            },
          },
        ),
      ),
    );

    const error = await postJson("/registration", {}).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 429,
      code: "registration_throttled",
      retryable: true,
      retryAfterSeconds: 300,
    });
  });

  it("never surfaces non-string validation details as the message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [{ loc: ["body", "email"], msg: "value is not a valid email" }],
          }),
          {
            status: 422,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    await expect(postJson("/test", {})).rejects.toMatchObject({
      status: 422,
      detail: "Request failed with status 422.",
      code: null,
      retryable: null,
      retryAfterSeconds: null,
    });
  });

  it.each([
    ["120", 120],
    [" 5 ", 5],
    [null, null],
    ["", null],
    ["-1", null],
    ["Wed, 21 Oct 2015 07:28:00 GMT", null],
    ["999999999", 86400],
  ])("parses Retry-After %j as %j", (value, expected) => {
    expect(parseRetryAfter(value)).toBe(expected);
  });

  it("notifies error listeners unless the request opts out", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        async () =>
          new Response(
            JSON.stringify({
              detail: "Authentication is required.",
            }),
            { status: 401, headers: { "Content-Type": "application/json" } },
          ),
      ),
    );

    const listener = vi.fn();
    const unsubscribe = subscribeToApiErrors(listener);

    await expect(getJson("/auth/me")).rejects.toBeInstanceOf(ApiError);
    await expect(
      postJson("/auth/admin/login", {}, { reportErrors: false }),
    ).rejects.toBeInstanceOf(ApiError);

    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener.mock.calls[0][0]).toMatchObject({ status: 401 });

    unsubscribe();
    await expect(getJson("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
