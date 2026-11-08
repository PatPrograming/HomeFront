import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("./mode", () => ({ MOCK: false }));
vi.mock("./auth", () => ({ getAccessToken: () => "maya-access-token" }));

const { sendToAgent, buildEmailPayload, NOT_CONNECTED } = await import("./agentPayload");

type FetchMock = Mock<(url: string, init: RequestInit & { headers: Record<string, string>; body: string }) => Promise<Response>>;

function respond(status: number, body: unknown): FetchMock {
  const fetch: FetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  globalThis.fetch = fetch as unknown as typeof globalThis.fetch;
  return fetch;
}

const payload = buildEmailPayload({
  mailboxes: ["inbox"], mailPlatforms: ["Outlook"], mailType: "Questions", perDay: "20",
  role: "Draft", message: " Deadline is Friday ", restrictions: "",
});

describe("sendToAgent", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("POSTs the form JSON to /api/v1/agent-requests with our Cognito bearer", async () => {
    const fetch = respond(200, { ok: true });
    await sendToAgent(payload);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/v1/agent-requests");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer maya-access-token");
    expect(JSON.parse(init.body)).toMatchObject({ type: "email", message: "Deadline is Friday", emails_per_day: 20 });
  });

  it("says 'not connected yet' while the backend route doesn't exist", async () => {
    respond(404, { detail: "Not Found" });
    await expect(sendToAgent(payload)).rejects.toThrow(NOT_CONNECTED);
  });

  it("passes other errors through", async () => {
    respond(500, { detail: "boom" });
    await expect(sendToAgent(payload)).rejects.toThrow("boom");
  });
});
