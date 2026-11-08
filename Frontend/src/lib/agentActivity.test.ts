import { describe, expect, it } from "vitest";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import { agentKind, describeEvent, permissionLines, toolLabel, turnEvents } from "./agentActivity";
import type { AutonomyCategory, SessionCard } from "./api";

const ev = (over: Partial<DecisionEvent>): DecisionEvent => ({
  id: "e", ts: "2026-11-08T08:00:01Z", sid: "sid-1", agent: "agent-1", actingFor: "maya", office: "rep-x",
  tool: "inbox_read", resource: "email-3", decision: "ALLOW", reason: "allowed", policyIds: ["read-tools"], atlas: [],
  guardrail: { flagged: false, categories: [] }, ...over,
});

describe("agentActivity: plain words for staff", () => {
  it("labels every tool in plain words", () => {
    expect(toolLabel("inbox_read")).toBe("Read your inbox");
    expect(toolLabel("records_bulk_export")).toBe("Export all constituent records");
    expect(toolLabel("something_new")).toBe("something new"); // an unknown tool still reads as words
  });

  it("describes a denied bulk export without policy or ATLAS ids", () => {
    const line = describeEvent(ev({ tool: "records_bulk_export", resource: "all constituents", decision: "DENY", reason: "denied by policy", policyIds: ["forbid-bulk-export"], atlas: ["AML.T0053"] }));
    expect(line).toEqual({ text: "Blocked: export all constituent records (never allowed)", blocked: true });
  });

  it("names the staffer's office role in plain words when a role policy blocks a call", () => {
    const denied = ev({ tool: "social_post", resource: null, decision: "DENY", reason: "denied by policy", policyIds: ["role-social-post"] });
    expect(describeEvent(denied, "Caseworker")).toEqual({
      text: "Blocked: post on the official account (waits for your review) (not part of the Caseworker role)",
      blocked: true,
    });
    expect(describeEvent(denied, "No role").text).toContain("your office role (none is on file)");
    expect(describeEvent(denied, "Caseworker").text).not.toContain("role-social-post"); // no policy ids for staff
  });

  it("says a held send is waiting for review, and a revoked call was blocked", () => {
    expect(describeEvent(ev({ tool: "email_send", resource: "lee@example.com", reason: "held for staff review" })).text).toBe(
      "Drafted a reply to lee@example.com, waiting for your review",
    );
    expect(describeEvent(ev({ decision: "DENY", reason: "session revoked / delegator inactive" }))).toEqual({
      text: "Blocked: your access was removed",
      blocked: true,
    });
    expect(describeEvent(ev({ resource: "email-13", guardrail: { flagged: true, categories: ["PROMPT_ATTACK"] } })).text).toBe(
      "Read email email-13 (it looked like a trick; its instructions were ignored)",
    );
  });

  it("lists what the agent may do this session, including what it sends alone", () => {
    const card = { allowedTools: ["inbox_read", "email_send"] } as unknown as SessionCard;
    const cats = [
      { category: "case_status", label: "Case status updates", level: "auto", locked: false },
      { category: "newsletter", label: "Newsletters", level: "review", locked: false },
    ] as AutonomyCategory[];
    const lines = permissionLines(card, cats);
    expect(lines.allowed).toEqual([
      "Read your inbox",
      "Send email replies (the kinds listed below go out without asking)",
      "Sends case status updates without asking",
    ]);
    expect(lines.never).toContain("Export all constituent records");
    expect(lines.never).not.toContain("Read your inbox");
  });

  it("counts 'Approve and allow for this session' grants, so it never claims every reply waits for review", () => {
    const card = { allowedTools: ["inbox_read", "email_send"] } as unknown as SessionCard;
    const cats = [{ category: "case_status", label: "Case status updates", level: "review", locked: false }] as AutonomyCategory[];
    const lines = permissionLines(card, cats, ["case_status"]);
    expect(lines.allowed).toContain("Sends case status updates without asking (this session only)");
    expect(lines.allowed).not.toContain("Send email replies (waits for your review)");
    expect(lines.allowed).toContain("Send email replies (the kinds listed below go out without asking)");
  });

  it("names the agent by what it does, never by its id", () => {
    expect(agentKind("inbox_read")).toBe("Email agent");
    expect(agentKind("email_send")).toBe("Email agent");
    expect(agentKind("social_post")).toBe("Social media agent");
    expect(agentKind("datagov_search")).toBe("Research agent");
    expect(agentKind("web_scrape")).toBe("Research agent");
    expect(agentKind("case_read")).toBe("Research agent");
    expect(agentKind("records_bulk_export")).toBe("Records agent");
  });

  it("takes the events of one turn, oldest first", () => {
    const events = [ev({ id: "c", ts: "2026-11-08T08:00:09Z" }), ev({ id: "b", ts: "2026-11-08T08:00:05Z" }), ev({ id: "a", ts: "2026-11-08T07:59:00Z" })];
    expect(turnEvents(events, "2026-11-08T08:00:00Z", "2026-11-08T08:00:06Z").map((e) => e.id)).toEqual(["b"]);
    expect(turnEvents(events, "2026-11-08T08:00:00Z", null).map((e) => e.id)).toEqual(["b", "c"]);
  });
});
