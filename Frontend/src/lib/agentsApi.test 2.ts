import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("./mode", () => ({ MOCK: false }));
vi.mock("./auth", () => ({ getAccessToken: () => "chief-access-token" }));

const { agentsApi } = await import("./agentsApi");

type FetchMock = Mock<(url: string, init: RequestInit & { headers: Record<string, string>; body: string }) => Promise<Response>>;

function respond(status: number, body: unknown): FetchMock {
  const fetch: FetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  globalThis.fetch = fetch as unknown as typeof globalThis.fetch;
  return fetch;
}

describe("agentsApi (teammates' tabs)", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("sends our Cognito token on every agent call, reads included", async () => {
    const fetch = respond(200, []);
    await agentsApi.agents();
    await agentsApi.approvals("pending");
    await agentsApi.audit(10, { decision: "deny" });
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/agents",
      "/api/v1/approvals?status=pending",
      "/api/v1/audit?limit=10&decision=deny",
    ]);
    for (const [, init] of fetch.mock.calls) expect(init.headers.Authorization).toBe("Bearer chief-access-token");
  });

  it("calls a tool with the login in Authorization and the workload token in X-Agent-Token", async () => {
    const fetch = respond(200, { decision: "allow", tool: "web.scrape", result: {} });
    await agentsApi.callTool("agent-workload-token", "web.scrape", { url: "https://example.gov" });
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/v1/agents/tool-call");
    expect(init.headers.Authorization).toBe("Bearer chief-access-token");
    expect(init.headers["X-Agent-Token"]).toBe("agent-workload-token");
    expect(JSON.parse(init.body)).toEqual({ tool: "web.scrape", arguments: { url: "https://example.gov" } });
  });

  it("reads the old tool catalog from /agent-tools, not the mandate /tools", async () => {
    const fetch = respond(200, []);
    await agentsApi.tools();
    expect(fetch.mock.calls[0][0]).toBe("/api/v1/agent-tools");
  });
});
