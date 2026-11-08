import { describe, expect, it, vi } from "vitest";
import type { SessionCard } from "./api";
import { sendInSession } from "./chatSession";

const NOW = Date.parse("2026-11-07T22:00:00Z");
const card = (sid: string, expiresAt: string): SessionCard => ({
  sid, agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [], expiresAt,
});

function deps(newSid = "sid-new") {
  return {
    startSession: vi.fn(async () => card(newSid, "2026-11-07T22:15:00Z")),
    sendMessage: vi.fn(async (sid: string, text: string) => ({ id: "m1", ts: "t", role: "user" as const, text })),
  };
}

describe("the first chat message starts the session", () => {
  it("starts a session through the broker, then sends the message in it", async () => {
    const d = deps();
    const out = await sendInSession(null, "Check my email", NOW, d);
    expect(d.startSession).toHaveBeenCalledTimes(1);
    expect(d.sendMessage).toHaveBeenCalledWith("sid-new", "Check my email");
    expect(out.card.sid).toBe("sid-new");
  });

  it("reuses a live session", async () => {
    const d = deps();
    await sendInSession(card("sid-live", "2026-11-07T22:10:00Z"), "hi", NOW, d);
    expect(d.startSession).not.toHaveBeenCalled();
    expect(d.sendMessage).toHaveBeenCalledWith("sid-live", "hi");
  });

  it("starts a new session when the old one expired", async () => {
    const d = deps();
    await sendInSession(card("sid-old", "2026-11-07T21:59:00Z"), "hi", NOW, d);
    expect(d.startSession).toHaveBeenCalledTimes(1);
    expect(d.sendMessage).toHaveBeenCalledWith("sid-new", "hi");
  });

  it("sends nothing when the broker refuses (e.g. an offboarded staffer)", async () => {
    const d = deps();
    d.startSession.mockRejectedValueOnce(new Error("staffer is offboarded"));
    await expect(sendInSession(null, "hi", NOW, d)).rejects.toThrow("offboarded");
    expect(d.sendMessage).not.toHaveBeenCalled();
  });
});
