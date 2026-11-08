import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, Draft, SessionCard } from "../lib/api";
import type { ChatReview } from "../components/ChatPanel";
import type { Chat } from "../lib/useChat";

// Huy's bug: after a session's hour is up, the next message starts a new session and the earlier prompts vanished.
// The real Console with its data hooks stubbed and ChatPanel captured.
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
});

const msg = (id: string, ts: string, role: ChatMessage["role"], text: string): ChatMessage => ({ id, ts, role, text });
const bySid: Record<string, ChatMessage[]> = {
  "sid-old": [msg("u1", "2026-11-08T03:00:00Z", "user", "Check my email"), msg("a1", "2026-11-08T03:00:09Z", "assistant", "3 replies wait for you.")],
  "sid-new": [msg("u2", "2026-11-08T04:20:00Z", "user", "Create a post on the latest pandemic")],
};
const draft = (id: string, sid: string, createdAt: string): Draft => ({
  id, sid, status: "pending", tool: "email_send", category: "case_status", categoryLabel: "Case status updates",
  args: { to: "marcus@example.com", subject: "Re", body: "B" }, createdAt, urgency: "low", score: 30, reason: "r",
});
const drafts = [draft("d-old", "sid-old", "2026-11-08T03:00:05Z")];
let panel: { chat: Chat; review?: ChatReview; renewedAfter?: string | null };

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/AuthProvider", () => ({ useSession: () => ({ username: "maya", groups: ["staffer"] }) }));
vi.mock("../lib/useSessionRevoked", () => ({ useSessionRevoked: () => false }));
vi.mock("../lib/useEventFeed", () => ({ useEventFeed: () => ({ events: [], error: null, lastPollAt: 1 }) }));
vi.mock("../lib/useReview", () => ({ useReview: () => ({ drafts, categories: [], error: null, reload: async () => {} }) }));
vi.mock("../lib/useSessions", () => ({ useSessions: () => ({ sessions: [], error: null, reload: async () => {} }) }));
vi.mock("../lib/useChat", () => ({
  useChat: (sid: string | null) => ({ messages: bySid[sid ?? ""] ?? [], error: null, send: async () => {} }),
}));
vi.mock("../components/Sidebar", () => ({ default: () => null }));
vi.mock("../components/ChatPanel", () => ({
  default: (props: typeof panel) => {
    panel = props;
    return null;
  },
}));
const newCard = (sid: string, expiresAt: string): SessionCard => ({
  sid, agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [], expiresAt,
});
vi.mock("../lib/api", () => ({
  api: {
    startSession: async () => newCard("sid-new", new Date(Date.now() + 3_600_000).toISOString()),
    sendMessage: async (_sid: string, text: string) => msg("u2", "2026-11-08T04:20:00Z", "user", text),
  },
}));

const { default: Console } = await import("./Console");

beforeEach(() => {
  storage.clear();
  storage.set("mandate.card", JSON.stringify(newCard("sid-old", "2026-11-08T04:00:00Z"))); // its hour is up
});

describe("Console: a new prompt never hides the earlier ones", () => {
  it("after the session renews, the earlier messages stay above the new ones, with a divider after them", async () => {
    renderToStaticMarkup(<Console />);
    await panel.chat.send("Create a post on the latest pandemic");
    expect(JSON.parse(storage.get("mandate.card")!).sid).toBe("sid-new");

    renderToStaticMarkup(<Console />); // what the tab shows next
    expect(panel.chat.messages.map((m) => m.id)).toEqual(["u1", "a1", "u2"]);
    expect(panel.renewedAfter).toBe("a1");
  });

  it("the earlier session's drafts stay with the reply that made them", async () => {
    renderToStaticMarkup(<Console />);
    await panel.chat.send("Create a post on the latest pandemic");
    renderToStaticMarkup(<Console />);
    expect(panel.review!.drafts.map((d) => d.id)).toEqual(["d-old"]);
  });

  it("a reload keeps the earlier messages (this tab's history)", async () => {
    renderToStaticMarkup(<Console />);
    await panel.chat.send("Create a post on the latest pandemic");
    vi.resetModules(); // a fresh page, same sessionStorage
    renderToStaticMarkup(<Console />);
    expect(panel.chat.messages.map((m) => m.id)).toEqual(["u1", "a1", "u2"]);
  });
});
