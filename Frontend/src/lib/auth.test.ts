import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("./mode", () => ({ MOCK: false }));

const ORIGIN = "http://localhost:3000";
const store = new Map<string, string>();
const assign = vi.fn<(url: string) => void>();

beforeAll(() => {
  // A stale teammate .env pointing at the old port must not matter.
  vi.stubEnv("VITE_COGNITO_REDIRECT_URI", "http://localhost:5173/");
  vi.stubEnv("VITE_COGNITO_DOMAIN", "https://mandate.auth.us-east-1.amazoncognito.com");
  vi.stubEnv("VITE_COGNITO_CLIENT_ID", "staff-web-client");
  vi.stubGlobal("window", { location: { origin: ORIGIN, assign } });
  vi.stubGlobal("sessionStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
});

afterAll(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("Cognito redirect URI", () => {
  it("is the page's own origin, ignoring VITE_COGNITO_REDIRECT_URI", async () => {
    const { cognito } = await import("./auth");
    expect(cognito.redirectUri).toBe(`${ORIGIN}/`);
  });

  it("sends the same origin to /oauth2/authorize and /oauth2/token", async () => {
    const { beginLogin, completeLogin } = await import("./auth");

    await beginLogin();
    const authorize = new URL(assign.mock.calls[0][0]);
    expect(authorize.pathname).toBe("/oauth2/authorize");
    expect(authorize.searchParams.get("redirect_uri")).toBe(`${ORIGIN}/`);

    const fetch = vi.fn(async (_url: string, _init: RequestInit) =>
      new Response(JSON.stringify({ access_token: "a.e30.s", expires_in: 3600 }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetch);
    const state = authorize.searchParams.get("state") ?? "";
    await completeLogin(`?code=one-time&state=${state}`);

    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("https://mandate.auth.us-east-1.amazoncognito.com/oauth2/token");
    const body = new URLSearchParams(String(init.body));
    expect(body.get("redirect_uri")).toBe(authorize.searchParams.get("redirect_uri"));
  });
});
