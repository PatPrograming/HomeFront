import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// ConnectPrompt links to /account; render the link as a plain anchor so no Router is needed.
// The chat only renders the prompt here; the connect flow itself is tested in ConnectPrompt.test.tsx.
vi.mock("../lib/connections", () => ({ beginConnect: vi.fn() }));
vi.mock("react-router-dom", () => ({
  Link: ({ to, children }: { to: string; children: unknown }) => <a href={to}>{children as never}</a>,
}));
import type { Chat } from "../lib/useChat";
import ChatPanel, { isStalled, STALL_MS } from "./ChatPanel";

const chatWith = (ts: string): Chat =>
  ({ messages: [{ id: "m1", ts, role: "user", text: "Check my email" }], error: null, send: async () => {} }) as unknown as Chat;

describe("ChatPanel: no endless 'Working…'", () => {
  it("counts a user message as stalled only after STALL_MS without a reply", () => {
    const sent = "2026-11-08T08:00:00Z";
    const t = new Date(sent).getTime();
    expect(isStalled(sent, t + STALL_MS - 1)).toBe(false);
    expect(isStalled(sent, t + STALL_MS + 1)).toBe(true);
  });

  it("shows Working… for a fresh message and the 'not connected' notice for an old one", () => {
    const fresh = renderToStaticMarkup(<ChatPanel chat={chatWith(new Date().toISOString())} sid="sid-1" disabled={false} />);
    expect(fresh).toContain("Working…");
    expect(fresh).not.toContain("isn&#x27;t answering");

    const stale = chatWith(new Date(Date.now() - STALL_MS - 5000).toISOString());
    const admin = { card: null, events: [], categories: [], admin: true };
    const old = renderToStaticMarkup(<ChatPanel chat={stale} sid="sid-1" disabled={false} activity={admin} />);
    expect(old).not.toContain("Working…");
    expect(old).toContain("isn&#x27;t answering this session");
    expect(old).toContain("Send your message again to restart it");
    expect(old).toContain("--sid sid-1 --chat");

    // Staff get the same notice in plain words: no command, no session id.
    const staff = renderToStaticMarkup(<ChatPanel chat={stale} sid="sid-1" disabled={false} />);
    expect(staff).toContain("isn&#x27;t answering this session");
    expect(staff).toContain("Send your message again to restart it");
    expect(staff).not.toContain("sid-1");
  });

  it("never promises a 15-minute session", () => {
    const empty = { messages: [], error: null, send: async () => {} } as unknown as Chat;
    expect(renderToStaticMarkup(<ChatPanel chat={empty} sid={null} disabled={false} />)).not.toMatch(/15-minute/);
  });
});

describe("ChatPanel: Connect prompt under assistant messages", () => {
  const chatOf = (messages: unknown[]) => ({ messages, error: null, send: async () => {} }) as unknown as Chat;
  const ts = "2026-11-08T08:00:00Z";
  const html = (messages: unknown[]) => renderToStaticMarkup(<ChatPanel chat={chatOf(messages)} sid="sid-1" disabled={false} />);

  it("shows 'Nothing was sent' and a Connect Gmail button under an assistant message with connect gmail", () => {
    const out = html([
      { id: "u1", ts, role: "user", text: "Send the email" },
      { id: "a1", ts, role: "assistant", text: "I could not send it.", connect: "gmail" },
    ]);
    expect(out).toContain("Nothing was sent");
    expect(out).toContain("Connect Gmail");
    expect(out.indexOf("I could not send it.")).toBeLessThan(out.indexOf("Nothing was sent"));
  });

  it("shows Connect X for connect x", () => {
    const out = html([{ id: "a1", ts, role: "assistant", text: "I could not post it.", connect: "x" }]);
    expect(out).toContain("Nothing was sent");
    expect(out).toContain("Connect X");
    expect(out).not.toContain("Connect Gmail");
  });

  it("shows no Connect button when connect is missing or null", () => {
    const out = html([
      { id: "a1", ts, role: "assistant", text: "Done." },
      { id: "a2", ts, role: "assistant", text: "All sent.", connect: null },
    ]);
    expect(out).not.toContain("Nothing was sent");
    expect(out).not.toMatch(/Connect (Gmail|X)/);
  });

  it.each(["__proto__", "toString", "constructor"])("renders the reply and no Connect button for the inherited name %s", (name) => {
    const out = html([{ id: "a1", ts, role: "assistant", text: "Here is your answer.", connect: name as never }]);
    expect(out).toContain("Here is your answer.");
    expect(out).not.toContain("Nothing was sent");
    expect(out).not.toContain("Connect ");
  });

  it("ignores a connect field on a user message (assistant messages only)", () => {
    const out = html([{ id: "u1", ts, role: "user", text: "hello", connect: "gmail" }]);
    expect(out).not.toContain("Nothing was sent");
    expect(out).not.toContain("Connect Gmail");
  });
});
