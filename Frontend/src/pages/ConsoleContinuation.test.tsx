import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, Draft, SessionCard, SessionSummary } from "../lib/api";
import type { ChatReview } from "../components/ChatPanel";
import type { Chat } from "../lib/useChat";

// Lane E's continuation: the expired session is continued (POST /sessions {continues}), and the listing's
// `earlier` brings the older sessions' messages back from the server, in any tab.
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
});

const msg = (id: string, ts: string, role: ChatMessage["role"], text: string): ChatMessage => ({ id, ts, role, text });
const old = [msg("u1", "2026-11-08T03:00:00Z", "user", "Check my email"), msg("a1", "2026-11-08T03:00:09Z", "assistant", "3 replies wait.")];
const current = [msg("u2", "2026-11-08T04:20:00Z", "user", "Post about the pandemic")];
const card = (sid: string, expiresAt: string): SessionCard => ({ sid, agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [], expiresAt });
const row: SessionSummary = {
  ...card("sid-new", new Date(Date.now() + 3_600_000).toISOString()), title: "Check my email", status: "active", waiting: 1,
  createdAt: "2026-11-08T03:00:00Z", pinned: false, group: null, archived: false, earlier: ["sid-old"],
};
const drafts: Draft[] = [{
  id: "d-old", sid: "sid-old", status: "pending", tool: "email_send", category: "case_status", categoryLabel: "Case status updates",
  args: { to: "marcus@example.com", subject: "Re", body: "B" }, createdAt: "2026-11-08T03:00:05Z", urgency: "low", score: 30, reason: "r",
}];
const startSession = vi.fn(async (_continues?: string) => ({ ...card("sid-new", new Date(Date.now() + 3_600_000).toISOString()), continuesFrom: "sid-old", grantsReset: true }));
const earlierAsked: string[][] = [];
let panel: { chat: Chat; review?: ChatReview; renewedAfter?: string | null };

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/AuthProvider", () => ({ useSession: () => ({ username: "maya", groups: ["staffer"] }) }));
vi.mock("../lib/useSessionRevoked", () => ({ useSessionRevoked: () => false }));
vi.mock("../lib/useEventFeed", () => ({ useEventFeed: () => ({ events: [], error: null, lastPollAt: 1 }) }));
vi.mock("../lib/useReview", () => ({ useReview: () => ({ drafts, categories: [], error: null, reload: async () => {} }) }));
vi.mock("../lib/useSessions", () => ({ useSessions: () => ({ sessions: [row], error: null, reload: async () => {} }) }));
vi.mock("../lib/useEarlier", () => ({
  useEarlier: (sids: string[]) => {
    earlierAsked.push(sids);
    return sids.join() === "sid-old" ? old : [];
  },
}));
vi.mock("../lib/useChat", () => ({ useChat: (sid: string | null) => ({ messages: sid === "sid-new" ? current : [], error: null, send: async () => {} }) }));
vi.mock("../components/Sidebar", () => ({ default: () => null }));
vi.mock("../components/ChatPanel", () => ({
  default: (props: typeof panel) => {
    panel = props;
    return null;
  },
}));
vi.mock("../lib/api", () => ({
  api: { startSession, sendMessage: async (_s: string, text: string) => msg("u2", "2026-11-08T04:20:00Z", "user", text), listSessions: async () => ({ sessions: [row] }) },
}));

const { default: Console } = await import("./Console");

beforeEach(() => {
  storage.clear();
  earlierAsked.length = 0;
  startSession.mockClear();
});

describe("Console: a conversation continues across sessions (Lane E)", () => {
  it("shows the earlier sessions' messages from the server, in any tab, with their drafts", () => {
    storage.set("mandate.card", JSON.stringify(row)); // a fresh tab: no local history at all
    renderToStaticMarkup(<Console />);
    expect(earlierAsked).toContainEqual(["sid-old"]);
    expect(panel.chat.messages.map((m) => m.id)).toEqual(["u1", "a1", "u2"]);
    expect(panel.renewedAfter).toBe("a1");
    expect(panel.review!.drafts.map((d) => d.id)).toEqual(["d-old"]);
  });

  it("an expired session is continued, and a reset of one-time permissions is said quietly", async () => {
    storage.set("mandate.card", JSON.stringify(card("sid-old", "2026-11-08T04:00:00Z")));
    renderToStaticMarkup(<Console />);
    await panel.chat.send("Post about the pandemic");
    expect(startSession).toHaveBeenCalledWith("sid-old");
    expect(renderToStaticMarkup(<Console />)).toContain("New session: one-time permissions were reset");
  });
});
