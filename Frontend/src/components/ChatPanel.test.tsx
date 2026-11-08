import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
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
    expect(fresh).not.toContain("isn&#x27;t connected");

    const stale = chatWith(new Date(Date.now() - STALL_MS - 5000).toISOString());
    const admin = { card: null, events: [], categories: [], admin: true };
    const old = renderToStaticMarkup(<ChatPanel chat={stale} sid="sid-1" disabled={false} activity={admin} />);
    expect(old).not.toContain("Working…");
    expect(old).toContain("connected to this session");
    expect(old).toContain("--sid sid-1 --chat");

    // Staff get the same notice in plain words: no command, no session id.
    const staff = renderToStaticMarkup(<ChatPanel chat={stale} sid="sid-1" disabled={false} />);
    expect(staff).toContain("connected to this session");
    expect(staff).not.toContain("sid-1");
  });

  it("never promises a 15-minute session", () => {
    const empty = { messages: [], error: null, send: async () => {} } as unknown as Chat;
    expect(renderToStaticMarkup(<ChatPanel chat={empty} sid={null} disabled={false} />)).not.toMatch(/15-minute/);
  });
});
