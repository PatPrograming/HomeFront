import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import type { AutonomyCategory, SessionCard } from "../lib/api";
import AgentActivity from "./AgentActivity";

const card: SessionCard = {
  sid: "sid-secret-1", agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official",
  allowedTools: ["inbox_read", "case_read", "email_send"], expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
};
const base = { sid: "sid-secret-1", agent: "agent-1", actingFor: "maya", office: "rep-x", guardrail: { flagged: false, categories: [] } };
const events: DecisionEvent[] = [
  { ...base, id: "1", ts: "2026-11-08T08:00:01Z", tool: "inbox_read", resource: "email-3", decision: "ALLOW", reason: "allowed", policyIds: ["read-tools"], atlas: [] },
  { ...base, id: "2", ts: "2026-11-08T08:00:02Z", tool: "records_bulk_export", resource: "all constituents", decision: "DENY", reason: "denied by policy", policyIds: ["forbid-bulk-export"], atlas: ["AML.T0053"] },
];
const render = (admin: boolean, working = false) =>
  renderToStaticMarkup(<AgentActivity card={card} events={events} categories={[]} working={working} admin={admin} />);

describe("AgentActivity: the inline 'what is it doing' disclosure", () => {
  it("tells a staffer who acts, what it may do and what it did, in plain words", () => {
    const html = render(false);
    expect(html).toContain("<details");
    expect(html).toContain("Acting for you");
    expect(html).toContain("Email agent: Read email email-3"); // each action names the agent kind that did it
    expect(html).toContain("Records agent: Blocked: export all constituent records (never allowed)");
    expect(html).toContain("rep-x");
    expect(html).toContain("Read your inbox");
    expect(html).toContain("Send email replies (waits for your review)");
    expect(html).toContain("Never allowed");
    expect(html).toContain("Read email email-3");
    expect(html).toContain("Blocked: export all constituent records (never allowed)");
  });

  it("never shows a staffer policy ids, ATLAS ids, the session id or the technical link", () => {
    const html = render(false);
    for (const secret of ["forbid-bulk-export", "read-tools", "AML.", "sid-secret-1", "Technical details", "agent-1"]) expect(html).not.toContain(secret);
  });

  it("gives an admin a Technical details link to the audit view", () => {
    const html = render(true);
    expect(html).toContain("Technical details");
    expect(html).toContain('href="/admin"');
  });

  it("reads Working… while the turn runs and Done with a count after", () => {
    expect(render(false, true)).toContain("Working…");
    expect(render(false, false)).toContain("Done · 2 actions");
  });
});

describe("AgentActivity: session grants", () => {
  it("lists a category granted for this session instead of claiming replies wait for review", () => {
    const cats = [{ category: "case_status", label: "Case status updates", level: "review", locked: false }] as AutonomyCategory[];
    const html = renderToStaticMarkup(
      <AgentActivity card={card} events={events} categories={cats} grants={["case_status"]} working={false} admin={false} />,
    );
    expect(html).toContain("Sends case status updates without asking (this session only)");
    expect(html).not.toContain("Send email replies (waits for your review)");
  });
});
