import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AutonomyCategory } from "../lib/api";

// "What it can send without asking" lives behind a Sending rules button on every screen; the chat is the main thing.
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
});
const rules = { open: false };
const categories = [{ category: "case_status", label: "Case status updates", level: "review", locked: false, approvedUnchanged: 0, edited: 0, rejected: 0, suggestAuto: false }] as AutonomyCategory[];

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/AuthProvider", () => ({ useSession: () => ({ username: "maya", groups: ["staffer"] }) }));
vi.mock("../lib/useSessionRevoked", () => ({ useSessionRevoked: () => false }));
vi.mock("../lib/useEventFeed", () => ({ useEventFeed: () => ({ events: [], error: null, lastPollAt: 1 }) }));
vi.mock("../lib/useReview", () => ({ useReview: () => ({ drafts: [], categories, error: null, reload: async () => {} }) }));
vi.mock("../lib/useSessions", () => ({ useSessions: () => ({ sessions: [], error: null, reload: async () => {} }) }));
vi.mock("../lib/useChat", () => ({ useChat: () => ({ messages: [], error: null, send: async () => {} }) }));
vi.mock("../lib/useToggle", () => ({ useToggle: () => [rules.open, () => {}] }));
vi.mock("../components/Sidebar", () => ({ default: () => null }));
vi.mock("../lib/api", () => ({ api: {} }));

const { default: Console } = await import("./Console");

beforeEach(() => {
  storage.clear();
  rules.open = false;
});

describe("Console: sending rules behind a button", () => {
  it("shows no always-on panel, only a labelled Sending rules button", () => {
    const html = renderToStaticMarkup(<Console />);
    expect(html).not.toContain("What it can send without asking");
    expect(html).toMatch(/<button[^>]*aria-expanded="false"[^>]*>Sending rules/);
    expect(html).not.toContain('class="rail'); // no right rail: the chat takes the main width
  });

  it("opened, it shows the autonomy choices in a dialog", () => {
    rules.open = true;
    const html = renderToStaticMarkup(<Console />);
    expect(html).toMatch(/role="dialog"[^>]*aria-label="Sending rules"/);
    expect(html).toContain("What it can send without asking");
    expect(html).toContain("Case status updates");
  });
});
