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

// Lane E: an expired session is continued (same conversation, fresh one-hour session), not replaced by a blank one.
const refusal = (status: number, code: string) => Object.assign(new Error(code), { status, data: { message: code, code } });

describe("an expired session is continued", () => {
  const old = card("sid-old", "2026-11-07T21:59:00Z");

  it("asks the broker to continue the old sid", async () => {
    const d = deps();
    await sendInSession(old, "hi", NOW, d);
    expect(d.startSession).toHaveBeenCalledWith("sid-old");
    expect(d.sendMessage).toHaveBeenCalledWith("sid-new", "hi");
  });

  it("still active on the server: keeps using the old sid", async () => {
    const d = deps();
    d.startSession.mockRejectedValueOnce(refusal(409, "still_active"));
    const out = await sendInSession(old, "hi", NOW, d);
    expect(d.sendMessage).toHaveBeenCalledWith("sid-old", "hi");
    expect(out.card.sid).toBe("sid-old");
  });

  it("already continued in another tab: uses that newest session", async () => {
    const d = { ...deps(), findContinuation: vi.fn(async () => card("sid-other-tab", "2026-11-07T23:00:00Z")) };
    d.startSession.mockRejectedValueOnce(refusal(409, "already_continued"));
    const out = await sendInSession(old, "hi", NOW, d);
    expect(d.findContinuation).toHaveBeenCalledWith("sid-old");
    expect(d.sendMessage).toHaveBeenCalledWith("sid-other-tab", "hi");
    expect(out.card.sid).toBe("sid-other-tab");
  });

  it.each([[403, "revoked"], [404, "not_found"]])("%i (%s): starts a fresh conversation instead", async (status, code) => {
    const d = deps();
    d.startSession.mockRejectedValueOnce(refusal(status, code));
    const out = await sendInSession(old, "hi", NOW, d);
    expect(d.startSession).toHaveBeenLastCalledWith();
    expect(out.card.sid).toBe("sid-new");
  });
});
