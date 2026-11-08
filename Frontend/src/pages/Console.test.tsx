import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionCard } from "../lib/api";
import type { Chat } from "../lib/useChat";

// Render the real Console with its data hooks stubbed (no DOM in these tests): what it shows, and what its
// chat box does with the first message.
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
});

const state = { revoked: false };
const chatSent: string[] = [];
let panelChat: Chat | null = null;
let panelProps: { retryAgent?: () => Promise<unknown>; onNewConversation?: () => void } = {};
const retryAgent = vi.fn();
const startSession = vi.fn();
const sendMessage = vi.fn();

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/AuthProvider", () => ({ useSession: () => ({ username: "maya" }) }));
vi.mock("../lib/useSessionRevoked", () => ({ useSessionRevoked: () => state.revoked }));
vi.mock("../lib/useEventFeed", () => ({ useEventFeed: () => ({ events: [], error: null, lastPollAt: null }) }));
vi.mock("../lib/useReview", () => ({ useReview: () => ({ drafts: [], categories: [], reload: async () => {} }) }));
vi.mock("../lib/useChat", () => ({
  useChat: () => ({ messages: [], error: null, send: async (t: string) => void chatSent.push(t) }),
}));
vi.mock("../components/ChatPanel", () => ({
  default: (props: { chat: Chat; disabled: boolean; retryAgent?: () => Promise<unknown>; onNewConversation?: () => void }) => {
    const { chat, disabled } = props;
    panelChat = chat;
    panelProps = props;
    return <div data-chat-disabled={String(disabled)}>chat</div>;
  },
}));
vi.mock("../lib/api", () => ({
  api: {
    startSession: () => startSession(),
    sendMessage: (s: string, t: string) => sendMessage(s, t),
    retryAgent: (sid: string) => retryAgent(sid),
  },
}));

const { default: Console } = await import("./Console");

const card = (sid: string): SessionCard => ({
  sid, agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [],
  expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
});

describe("Console: the chat replaces 'Start a session'", () => {
  beforeEach(() => {
    storage.clear();
    state.revoked = false;
    chatSent.length = 0;
    panelChat = null;
    startSession.mockReset().mockResolvedValue(card("sid-new"));
    sendMessage.mockReset().mockResolvedValue({ id: "m1", ts: "t", role: "user", text: "x" });
  });

  it("has no Start button and no identity card before the first message", () => {
    const html = renderToStaticMarkup(<Console />);
    expect(html).not.toMatch(/Start (a )?(new )?(session|assistant)/i);
    expect(html).not.toContain("agent-1");
    expect(html).toContain('data-chat-disabled="false"');
  });

  it("the first message starts the session through the broker and is sent in it", async () => {
    renderToStaticMarkup(<Console />);
    await panelChat!.send("Check my email");
    expect(startSession).toHaveBeenCalledTimes(1);
    expect(sendMessage).toHaveBeenCalledWith("sid-new", "Check my email");
    expect(JSON.parse(storage.get("mandate.card")!).sid).toBe("sid-new"); // the card the page shows next
  });

  it("with a live session it shows who acts and until when, and sends without starting another", async () => {
    storage.set("mandate.card", JSON.stringify(card("sid-live")));
    const html = renderToStaticMarkup(<Console />);
    expect(html).toContain("agent-1");
    expect(html).toContain("rep-x");
    expect(html).toContain("Access ends at"); // staff get one line; the countdown card is admin-only (ConsoleRoles.test.tsx)
    await panelChat!.send("Any new mail?");
    expect(startSession).not.toHaveBeenCalled();
    expect(chatSent).toEqual(["Any new mail?"]);
  });

  it("a revoked session keeps the card, shows Revoked and closes the chat", () => {
    storage.set("mandate.card", JSON.stringify(card("sid-live")));
    state.revoked = true;
    const html = renderToStaticMarkup(<Console />);
    expect(html).toContain("Revoked");
    expect(html).toContain('data-chat-disabled="true"');
    expect(html).toContain("Start a new conversation"); // the way out of a revoked session
  });

  it("a live session offers no 'new conversation' button", () => {
    storage.set("mandate.card", JSON.stringify(card("sid-live")));
    expect(renderToStaticMarkup(<Console />)).not.toContain("Start a new conversation");
  });

  it("hands the chat its ways back: Try again for this session, and a new conversation", async () => {
    storage.set("mandate.card", JSON.stringify(card("sid-live")));
    retryAgent.mockReset().mockResolvedValue({ sid: "sid-live", agent: "started", retriesLeft: 2 });
    renderToStaticMarkup(<Console />);
    expect(typeof panelProps.onNewConversation).toBe("function");
    await panelProps.retryAgent!();
    expect(retryAgent).toHaveBeenCalledWith("sid-live");
  });
});
