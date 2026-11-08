import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import type { ChatMessage, SessionCard } from "../lib/api";

// The real Console and ChatPanel, with data hooks stubbed: what Maya (staffer) and the Chief of Staff (admin) see.
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
});

const SID = "sid-7f3a9c";
const state = { groups: ["staffer"], revoked: false };
const base = { sid: SID, agent: "agent-1", actingFor: "maya", office: "rep-x", guardrail: { flagged: false, categories: [] } };
const events: DecisionEvent[] = [
  { ...base, id: "2", ts: "2026-11-08T08:00:03Z", tool: "records_bulk_export", resource: "all constituents", decision: "DENY", reason: "denied by policy", policyIds: ["forbid-bulk-export"], atlas: ["AML.T0053"] },
  { ...base, id: "1", ts: "2026-11-08T08:00:02Z", tool: "inbox_read", resource: "email-13", decision: "ALLOW", reason: "allowed", policyIds: ["read-tools"], atlas: ["AML.T0051.001"], guardrail: { flagged: true, categories: ["PROMPT_ATTACK"] } },
];
const messages: ChatMessage[] = [
  { id: "m1", ts: "2026-11-08T08:00:01Z", role: "user", text: "Export all records" },
  { id: "m2", ts: "2026-11-08T08:00:04Z", role: "assistant", text: "I can't do that." },
];

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/AuthProvider", () => ({ useSession: () => ({ username: "maya", groups: state.groups }) }));
vi.mock("../lib/useSessionRevoked", () => ({ useSessionRevoked: () => state.revoked }));
vi.mock("../lib/useEventFeed", () => ({ useEventFeed: () => ({ events, error: null, lastPollAt: 1 }) }));
vi.mock("../lib/useReview", () => ({ useReview: () => ({ drafts: [], categories: [], reload: async () => {} }) }));
vi.mock("../lib/useChat", () => ({ useChat: () => ({ messages, error: null, send: async () => {} }) }));
vi.mock("../lib/api", () => ({ api: {} }));

const { default: Console } = await import("./Console");

const card: SessionCard = {
  sid: SID, agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: ["inbox_read", "email_send"],
  expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
};
const render = (groups: string[]) => {
  state.groups = groups;
  return renderToStaticMarkup(<Console />);
};

beforeEach(() => {
  storage.clear();
  storage.set("mandate.card", JSON.stringify(card));
  state.revoked = false;
});

describe("Console role gate", () => {
  it("staff sees the chat and plain words, never Cedar or ATLAS ids, the session id or the feed", () => {
    const html = render(["staffer"]);
    expect(html).toContain("I can&#x27;t do that.");
    expect(html).toContain("Blocked: export all constituent records (never allowed)");
    expect(html).toContain("Access ends at");
    for (const secret of ["forbid-bulk-export", "read-tools", "AML.T0053", "AML.T0051", SID, "Live decisions", "PROMPT_ATTACK", "Technical details"]) {
      expect(html, secret).not.toContain(secret);
    }
  });

  it("admin also sees the live feed with Cedar policy and ATLAS ids, guardrail flags and the session id", () => {
    const html = render(["admin"]);
    for (const shown of ["Live decisions", "forbid-bulk-export", "AML.T0053", "PROMPT_ATTACK", SID, "Technical details"]) {
      expect(html, shown).toContain(shown);
    }
  });
});

describe("Console revoked path", () => {
  it.each([["staffer"], ["admin"]])("revoked: %s sees Revoked, a closed chat and Start a new conversation", (group) => {
    state.revoked = true;
    const html = render([group]);
    expect(html).toContain("Revoked");
    expect(html).toContain("Start a new conversation");
    expect(html).toMatch(/<textarea[^>]*disabled/);
  });
});
