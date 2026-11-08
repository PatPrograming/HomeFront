import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, Draft, SessionCard, SessionSummary } from "../lib/api";

// The real Console with data hooks stubbed: two conversations, this tab showing the second.
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
});

const base = { agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: ["inbox_read" as const] };
const summary = (sid: string, title: string, waiting: number): SessionSummary => ({
  ...base, sid, title, status: "active", waiting, pinned: false, group: null, archived: false, createdAt: "2026-11-08T08:00:00Z", expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
});
const sessions = [summary("sid-a", "Check my email", 1), summary("sid-b", "Post the town hall", 1)];
const draft = (id: string, sid: string, to: string): Draft => ({
  id, sid, status: "pending", tool: "email_send", category: "case_status", categoryLabel: "Case status updates",
  args: { to, subject: "Re", body: "B" }, createdAt: "2026-11-08T08:00:03Z", urgency: "medium", score: 40, reason: "r",
});
const drafts = [draft("d-a", "sid-a", "in-a@example.com"), draft("d-a2", "sid-a", "second-a@example.com"), draft("d-b", "sid-b", "in-b@example.com")];
const messages: ChatMessage[] = [
  { id: "m1", ts: "2026-11-08T08:00:01Z", role: "user", text: "Hi" },
  { id: "m2", ts: "2026-11-08T08:00:04Z", role: "assistant", text: "Done." },
];
const chatFor: string[] = [];
// What the console hands its children, so a test can press their buttons (no DOM here).
const captured: { onSelect?: (s: SessionSummary) => void; onNext?: () => void } = {};
const opened: string[] = [];
const nav = { openId: null as string | null };

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/AuthProvider", () => ({ useSession: () => ({ username: "maya", groups: ["staffer"] }) }));
vi.mock("../lib/useSessionRevoked", () => ({ useSessionRevoked: () => false }));
vi.mock("../lib/useEventFeed", () => ({ useEventFeed: () => ({ events: [], error: null, lastPollAt: 1 }) }));
vi.mock("../lib/useReview", () => ({ useReview: () => ({ drafts, categories: [], error: null, reload: async () => {} }) }));
vi.mock("../lib/useChat", () => ({
  useChat: (sid: string | null) => {
    chatFor.push(String(sid));
    return { messages, error: null, send: async () => {} };
  },
}));
vi.mock("../lib/useSessions", () => ({ useSessions: () => ({ sessions, error: null, reload: async () => {} }) }));
vi.mock("../lib/api", () => ({ api: {} }));
vi.mock("../lib/useOpenDraft", () => ({
  useOpenDraft: () => ({ openId: nav.openId, open: (id: string) => void opened.push(id), close: () => {} }),
}));
vi.mock("../components/Sidebar", () => ({
  default: (props: { onSelect: (s: SessionSummary) => void }) => {
    captured.onSelect = props.onSelect;
    return <div>{sessions.map((s) => s.title).join(" | ")} New conversation</div>;
  },
}));
vi.mock("../components/ReviewPanel", () => ({
  default: (props: { draft: Draft; onNext?: () => void }) => {
    captured.onNext = props.onNext;
    return <div>panel {props.draft.id}</div>;
  },
}));

const { default: Console } = await import("./Console");

const card = (sid: string): SessionCard => ({ ...base, sid, expiresAt: new Date(Date.now() + 3_600_000).toISOString() });

beforeEach(() => {
  storage.clear();
  chatFor.length = 0;
  opened.length = 0;
  nav.openId = null;
});

describe("Console: several conversations", () => {
  it("lists the conversations with a New conversation button", () => {
    storage.set("mandate.card", JSON.stringify(card("sid-b")));
    const html = renderToStaticMarkup(<Console />);
    for (const shown of ["Check my email", "Post the town hall", "New conversation"]) expect(html).toContain(shown);
  });

  it("shows only the open conversation's chat and draft cards (this tab's choice)", () => {
    storage.set("mandate.card", JSON.stringify(card("sid-b")));
    const html = renderToStaticMarkup(<Console />);
    expect(chatFor).toContain("sid-b");
    expect(html).toContain("Reply to in-b@example.com");
    expect(html).not.toContain("Reply to in-a@example.com");
  });

  it("another tab on the other conversation shows that one", () => {
    storage.set("mandate.card", JSON.stringify(card("sid-a")));
    const html = renderToStaticMarkup(<Console />);
    expect(html).toContain("Reply to in-a@example.com");
    expect(html).not.toContain("Reply to in-b@example.com");
  });

  it("counts every waiting draft in the badge", () => {
    storage.set("mandate.card", JSON.stringify(card("sid-a")));
    expect(renderToStaticMarkup(<Console />)).toContain("3 waiting");
  });
});

describe("Console: switching and Next", () => {
  it("picking a conversation in the list makes this tab show it", () => {
    storage.set("mandate.card", JSON.stringify(card("sid-b")));
    renderToStaticMarkup(<Console />);
    captured.onSelect!(sessions[0]);
    expect(JSON.parse(storage.get("mandate.card")!)).toMatchObject({ sid: "sid-a", agent: "agent-1", office: "rep-x" });
    const html = renderToStaticMarkup(<Console />); // what the tab shows next
    expect(html).toContain("Reply to in-a@example.com");
    expect(html).not.toContain("Reply to in-b@example.com");
  });

  it("Next waiting opens the following draft in the list, wrapping at the end", () => {
    storage.set("mandate.card", JSON.stringify(card("sid-a")));
    nav.openId = "d-a"; // this conversation has d-a and d-a2
    renderToStaticMarkup(<Console />);
    captured.onNext!();
    expect(opened).toEqual(["d-a2"]);
    nav.openId = "d-a2";
    renderToStaticMarkup(<Console />);
    captured.onNext!();
    expect(opened).toEqual(["d-a2", "d-a"]);
  });
});
