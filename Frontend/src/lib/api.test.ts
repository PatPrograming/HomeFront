import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("./mode", () => ({ MOCK: false }));
vi.mock("./auth", () => ({ getAccessToken: () => "maya-access-token" }));

const { api, ApiError } = await import("./api");

type FetchMock = Mock<(url: string, init: RequestInit & { headers: Record<string, string>; body: string }) => Promise<Response>>;

function respond(status: number, body: unknown): FetchMock {
  const fetch: FetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  globalThis.fetch = fetch as unknown as typeof globalThis.fetch;
  return fetch;
}

describe("api", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("starts a session with POST /api/v1/sessions and the Cognito Bearer token", async () => {
    const fetch = respond(200, { sid: "s1" });
    await expect(api.startSession()).resolves.toEqual({ sid: "s1" });
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/v1/sessions");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer maya-access-token");
    expect(JSON.parse(init.body)).toEqual({});
  });

  it("polls GET /api/v1/events with the since cursor", async () => {
    const fetch = respond(200, { events: [], next: null });
    await api.events();
    await api.events("2026-11-07T20:00:00Z#a");
    expect(fetch.mock.calls[0][0]).toBe("/api/v1/events");
    expect(fetch.mock.calls[1][0]).toBe("/api/v1/events?since=2026-11-07T20%3A00%3A00Z%23a");
    expect(fetch.mock.calls[1][1].headers.Authorization).toBe("Bearer maya-access-token");
  });

  it("offboards a staffer with POST /api/v1/staff/{id}/offboard", async () => {
    const fetch = respond(200, { staffer: "maya", active: false, revokedSessions: 1 });
    await api.offboard("maya");
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/v1/staff/maya/offboard");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer maya-access-token");
  });

  it("gives up on a request that never answers, so polling can recover", async () => {
    const clock = new AbortController();
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(clock.signal);
    globalThis.fetch = vi.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => init.signal!.addEventListener("abort", () => reject(init.signal!.reason))),
    ) as unknown as typeof globalThis.fetch;
    const pending = api.events().catch((e) => e);
    clock.abort(new DOMException("timed out", "TimeoutError")); // what the 5 s timer does
    expect(timeout).toHaveBeenCalledWith(5000);
    expect((await pending).name).toBe("TimeoutError");
  });

  it("turns an error response into ApiError with the server's detail", async () => {
    respond(403, { detail: "admin only" });
    const err = await api.offboard("maya").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.message).toBe("admin only");
  });
});
