import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("../lib/mode", () => ({ MOCK: false }));
vi.mock("../lib/auth", () => ({ getAccessToken: () => "maya-access-token" }));

const { default: PlanCard } = await import("./PlanCard");
const { api } = await import("../lib/api");
import type { ChatPlan } from "../lib/api";

const plan: ChatPlan = {
  id: "p1",
  status: "pending",
  steps: [
    {
      agent: "research",
      label: "Research agent",
      purpose: "find facts",
      tools: [{ name: "datagov_search", label: "search government data (data.gov)", outbound: false }],
      readOnly: true,
      dropped: ["records_bulk_export"],
    },
    {
      agent: "social",
      label: "Social media agent",
      purpose: "write one X post draft",
      tools: [{ name: "social_post", label: "write one social post draft (held for your review)", outbound: true }],
      readOnly: false,
      dropped: [],
    },
  ],
};

describe("PlanCard", () => {
  it("lists each helper agent, what it may do, what the role drops, and the Approve/Cancel buttons", () => {
    const html = renderToStaticMarkup(<PlanCard sid="s1" plan={plan} />);
    expect(html).toContain("Research agent");
    expect(html).toContain("read-only");
    expect(html).toContain("search government data (data.gov)");
    expect(html).toContain("not allowed for your role");
    expect(html).toContain("Social media agent");
    expect(html).toContain("held for your review");
    expect(html).toContain("Approve plan");
    expect(html).toContain("Cancel");
  });

  it("shows the decision instead of buttons once decided", () => {
    const html = renderToStaticMarkup(<PlanCard sid="s1" plan={{ ...plan, status: "cancelled" }} />);
    expect(html).toContain("Cancelled");
    expect(html).not.toContain("Approve plan");
  });

  it("decides through POST /api/v1/sessions/{sid}/plans/{id} with the staffer's token", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ id: "p1", status: "approved" }), { status: 200 }));
    globalThis.fetch = fetch as unknown as typeof globalThis.fetch;
    await expect(api.decidePlan("s1", "p1", "approve")).resolves.toEqual({ id: "p1", status: "approved" });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit & { headers: Record<string, string> }];
    expect(url).toBe("/api/v1/sessions/s1/plans/p1");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer maya-access-token");
    expect(JSON.parse(init.body as string)).toEqual({ decision: "approve" });
  });
});
