import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage, SessionCard } from "../lib/api";
import type { Chat } from "../lib/useChat";

// Stop this task: the console, never the agent, turns the staffer's typed "stop" into POST /sessions/{sid}/revoke.
// Data hooks are stubbed (no DOM); the panel stub hands back what the Console gives the chat box.
const storage = new Map<string, string>();
vi.stubGlobal("sessionStorage", {
  getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => void storage.set(k, v),
  removeItem: (k: string) => void storage.delete(k),
});

const chatSent: string[] = [];
const messages: ChatMessage[] = [];
let panel: { chat: Chat; stop?: { onStop: () => Promise<void> } } | null = null;
const revokeSession = vi.fn();
const startSession = vi.fn();

vi.mock("../components/Layout", () => ({ default: ({ children }: { children: unknown }) => children }));
vi.mock("../lib/AuthProvider", () => ({ useSession: () => ({ username: "maya" }) }));
vi.mock("../lib/useSessionRevoked", () => ({ useSessionRevoked: () => false }));
vi.mock("../lib/useEventFeed", () => ({ useEventFeed: () => ({ events: [], error: null, lastPollAt: null }) }));
vi.mock("../lib/useReview", () => ({ useReview: () => ({ drafts: [], categories: [], reload: async () => {} }) }));
vi.mock("../lib/useChat", () => ({
  useChat: () => ({ messages, error: null, send: async (t: string) => void chatSent.push(t) }),
}));
vi.mock("../components/ChatPanel", () => ({
  default: (props: { chat: Chat; stop?: { onStop: () => Promise<void> } }) => {
    panel = props;
    return <div>chat</div>;
  },
}));
vi.mock("../lib/api", () => ({
  api: { revokeSession: (sid: string) => revokeSession(sid), startSession: () => startSession(), sendMessage: vi.fn() },
}));

const { default: Console } = await import("./Console");

const card: SessionCard = {
  sid: "sid-live", agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [],
  expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
};

describe("Console: Stop this task", () => {
  beforeEach(() => {
    storage.clear();
    storage.set("mandate.card", JSON.stringify(card));
    chatSent.length = 0;
    messages.length = 0;
    panel = null;
    revokeSession.mockReset().mockResolvedValue({ sid: "sid-live", status: "revoked", alreadyRevoked: false });
    startSession.mockReset();
  });

  it.each(["stop", "Cancel", "revoke this task", "  STOP!  ", "Stop this task."])(
    "the staffer typing %j revokes the session and nothing reaches the agent",
    async (typed) => {
      renderToStaticMarkup(<Console />);
      await panel!.chat.send(typed);
      expect(revokeSession).toHaveBeenCalledWith("sid-live");
      expect(chatSent).toEqual([]);
      expect(startSession).not.toHaveBeenCalled();
    },
  );

  it("a task that merely mentions stopping is still sent to the agent", async () => {
    renderToStaticMarkup(<Console />);
    await panel!.chat.send("Stop emailing Jane and draft a reply to Lee");
    expect(revokeSession).not.toHaveBeenCalled();
    expect(chatSent).toEqual(["Stop emailing Jane and draft a reply to Lee"]);
  });

  it("the Stop this task control revokes the session with the staffer's login", async () => {
    renderToStaticMarkup(<Console />);
    await panel!.stop!.onStop();
    expect(revokeSession).toHaveBeenCalledWith("sid-live");
  });

  it("an agent message saying stop triggers nothing", () => {
    const ts = new Date().toISOString();
    messages.push(
      { id: "u1", ts, role: "user", text: "Check my email" },
      { id: "a1", ts, role: "assistant", text: "stop" },
      { id: "a2", ts, role: "assistant", text: "revoke this task" },
    );
    renderToStaticMarkup(<Console />);
    expect(revokeSession).not.toHaveBeenCalled();
  });

  it("without a conversation, 'stop' is just a message (there is nothing to revoke)", async () => {
    storage.clear();
    startSession.mockResolvedValue({ ...card, sid: "sid-new" });
    renderToStaticMarkup(<Console />);
    expect(panel!.stop).toBeUndefined();
    await panel!.chat.send("stop").catch(() => {});
    expect(revokeSession).not.toHaveBeenCalled();
  });
});

describe("Console: a stopped task says who stopped it", () => {
  it("after a reload the header still reads 'you stopped this task', not the Chief of Staff", () => {
    storage.clear();
    storage.set("mandate.card", JSON.stringify(card));
    storage.set("mandate.stopped", "sid-live");
    const html = renderToStaticMarkup(<Console />);
    expect(html).toContain("Revoked: you stopped this task");
    expect(html).not.toContain("removed by the Chief of Staff");
  });

  it("the stop is remembered for the next render", async () => {
    storage.clear();
    storage.set("mandate.card", JSON.stringify(card));
    revokeSession.mockResolvedValue({ sid: "sid-live", status: "revoked", alreadyRevoked: false });
    renderToStaticMarkup(<Console />);
    await panel!.chat.send("stop");
    expect(storage.get("mandate.stopped")).toBe("sid-live");
  });
});
