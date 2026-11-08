import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, Draft, SessionCard } from "../lib/api";

// The real Console and ChatPanel with data hooks stubbed: reviewing drafts from the chat.
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
});

const SID = "sid-1";
const state = { openId: null as string | null, error: null as Error | null };
const draft = (id: string, to: string): Draft => ({
  id, status: "pending", tool: "email_send", category: "case_status", categoryLabel: "Case status updates", urgency: "medium", score: 40, reason: "r",
  args: { to, subject: `Re: ${id}`, body: `Body ${id}` }, createdAt: "2026-11-08T08:00:03Z", sid: SID,
});
const drafts = [draft("d1", "marcus.bell@example.com"), draft("d2", "rosa@example.com"), draft("d3", "kevin@example.com")];
const messages: ChatMessage[] = [
  { id: "m1", ts: "2026-11-08T08:00:01Z", role: "user", text: "Check my email" },
  { id: "m2", ts: "2026-11-08T08:00:04Z", role: "assistant", text: "I went through 3 emails." },
];

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/AuthProvider", () => ({ useSession: () => ({ username: "maya", groups: ["staffer"] }) }));
vi.mock("../lib/useSessionRevoked", () => ({ useSessionRevoked: () => false }));
vi.mock("../lib/useEventFeed", () => ({ useEventFeed: () => ({ events: [], error: null, lastPollAt: 1 }) }));
vi.mock("../lib/useReview", () => ({ useReview: () => ({ drafts, categories: [], error: null, reload: async () => {} }) }));
vi.mock("../lib/useChat", () => ({ useChat: () => ({ messages, error: state.error, send: async () => {} }) }));
vi.mock("../lib/useOpenDraft", () => ({ useOpenDraft: () => ({ openId: state.openId, open: () => {}, close: () => {} }) }));
vi.mock("../lib/api", () => ({ api: {} }));

const { default: Console } = await import("./Console");

const card: SessionCard = {
  sid: SID, agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: ["inbox_read", "email_send"],
  expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
};

beforeEach(() => {
  storage.clear();
  storage.set("mandate.card", JSON.stringify(card));
  state.openId = null;
  state.error = null;
});

describe("Console: review from the chat", () => {
  it("shows an inline card per draft under the reply and an 'N waiting' badge", () => {
    const html = renderToStaticMarkup(<Console />);
    expect(html.match(/needs your review/g)).toHaveLength(3);
    expect(html).toContain("3 waiting");
  });

  it("has no always-on review list in the rail", () => {
    const html = renderToStaticMarkup(<Console />);
    expect(html).not.toContain("Needs your review");
    expect(html).not.toContain("Approve and send"); // no draft actions until one is opened
  });

  it("opens the chosen draft, and only that one, in the side panel with a read-only recipient", () => {
    state.openId = "d2";
    const html = renderToStaticMarkup(<Console />);
    const panel = html.slice(html.indexOf('<section class="review-panel" role="dialog"')); // the side panel only:
    expect(panel).toContain("Body d2"); // draft cards in the chat now show every draft's text, on purpose
    expect(panel).not.toContain("Body d1");
    const recipient = html.match(/<input[^>]*value="rosa@example\.com"[^>]*>/)?.[0] ?? "";
    expect(recipient).toMatch(/\sreadonly=""/); // React's real attribute, not aria-readonly or a class
  });

  it("says it can't reach the server instead of the browser's 'Failed to fetch'", () => {
    state.error = new TypeError("Failed to fetch");
    const html = renderToStaticMarkup(<Console />);
    expect(html).toContain("Can&#x27;t reach HomeFront&#x27;s server. Retrying…");
    expect(html).not.toContain("Failed to fetch");
  });
});
