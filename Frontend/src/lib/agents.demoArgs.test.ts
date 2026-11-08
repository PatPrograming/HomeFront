import { describe, expect, it, vi } from "vitest";

vi.mock("./auth", () => ({
  getAccessToken: () => null,
  authConfig: { redirectUri: "http://localhost/" },
}));

const { demoArgsForTool, pickFocusTool } = await import("./agents");
import type { Agent, Tool } from "./agents";

const baseAgent = (grants: string[]): Agent => ({
  agent_id: "agent-test-01",
  kind: "research",
  name: "Tester",
  agency: "Demo",
  instructions: "",
  model: null,
  max_steps: 5,
  owner_id: null,
  tool_grants: grants,
  recipients: [],
  token_version: 1,
  active: true,
});

describe("pickFocusTool", () => {
  it("prefers non-HITL lower-risk grants", () => {
    const catalog: Tool[] = [
      { name: "email.send", description: "", risk_level: "high", requires_hitl: true },
      { name: "web.scrape", description: "", risk_level: "medium", requires_hitl: false },
      { name: "datagov.search", description: "", risk_level: "low", requires_hitl: false },
    ];
    expect(pickFocusTool(baseAgent(["email.send", "web.scrape", "datagov.search"]), catalog)).toBe(
      "datagov.search",
    );
  });

  it("returns null when there are no grants", () => {
    expect(pickFocusTool(baseAgent([]), [])).toBeNull();
  });

  it("is stable for equal-risk grants (sorted input)", () => {
    const catalog: Tool[] = [
      { name: "nasa.search", description: "", risk_level: "medium" },
      { name: "datagov.search", description: "", risk_level: "medium" },
      { name: "web.scrape", description: "", risk_level: "medium" },
    ];
    expect(pickFocusTool(baseAgent(["datagov.search", "nasa.search", "web.scrape"]), catalog)).toBe(
      "datagov.search",
    );
  });
});

describe("demoArgsForTool", () => {
  it("returns scrape and search placeholders", () => {
    expect(demoArgsForTool("web.scrape")).toEqual({ url: "https://www.usa.gov" });
    expect(demoArgsForTool("datagov.search").query).toBe("public health");
    expect(demoArgsForTool("usaspending.search")).toEqual({
      keywords: "broadband",
      fiscal_year: 0,
      awarding_agency: "",
      limit: 5,
    });
  });

  it("uses the agent's first recipient for email.send", () => {
    const agent = baseAgent(["email.send"]);
    agent.recipients = ["maya@example.gov"];
    expect(demoArgsForTool("email.send", agent).to).toBe("maya@example.gov");
  });
});
