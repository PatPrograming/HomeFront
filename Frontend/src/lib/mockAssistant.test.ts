import { describe, expect, it } from "vitest";
import { rankDraft } from "./mockAssistant";

// email-9 in agent/data/emails.json is ethan.brown's "sensitive" email that mentions "medical".
const reply = (to: string) => ({
  tool: "email_send" as const, category: "sensitive", createdAt: new Date().toISOString(),
  args: { to, subject: "Re", body: "URGENT eviction today", in_reply_to: "email-9" },
});

describe("mock rankDraft (copy of the server scorer)", () => {
  it("counts the original email's urgent words only when replying to its sender, never the draft text", () => {
    expect(rankDraft(reply("ethan.brown@example.net"), Date.now()).reason).toContain("'medical'");
    const borrowed = rankDraft(reply("someone.else@example.com"), Date.now());
    expect(borrowed.reason).not.toMatch(/medical|eviction|urgent/);
  });

  it("knows the Vietnamese urgent word in email-11", () => {
    const vi = { tool: "email_send" as const, category: "case_status", createdAt: new Date().toISOString(), args: { to: "minh.nguyen@example.net", in_reply_to: "email-11" } };
    expect(rankDraft(vi, Date.now()).reason).toContain("'gấp'");
  });
});
