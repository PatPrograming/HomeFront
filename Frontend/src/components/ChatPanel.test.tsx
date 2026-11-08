import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Chat } from "../lib/useChat";
import ChatPanel, { isStalled, STALL_MS } from "./ChatPanel";

vi.mock("../lib/api", () => ({ api: {} })); // draft cards call it; these tests never do

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

describe("ChatPanel: a renewed session keeps the conversation", () => {
  it("draws a divider right after the last message from before the renewal", () => {
    const chat = {
      messages: [
        { id: "a1", ts: "2026-11-08T03:00:09Z", role: "assistant", text: "Earlier reply" },
        { id: "u2", ts: "2026-11-08T04:20:00Z", role: "user", text: "New prompt" },
      ],
      error: null,
      send: async () => {},
    } as unknown as Chat;
    const html = renderToStaticMarkup(<ChatPanel chat={chat} sid="sid-new" disabled={false} renewedAfter="a1" />);
    const divider = html.indexOf('role="separator"');
    expect(divider).toBeGreaterThan(html.indexOf("Earlier reply"));
    expect(divider).toBeLessThan(html.indexOf("New prompt"));
    expect(renderToStaticMarkup(<ChatPanel chat={chat} sid="sid-new" disabled={false} />)).not.toContain('role="separator"');
  });
});
