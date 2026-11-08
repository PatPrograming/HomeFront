import { beforeEach, describe, expect, it, vi } from "vitest";

// mockApi keeps its fake world in localStorage; node has none, so give it an in-memory one.
const store = new Map<string, string>();
vi.stubGlobal("localStorage", {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
});

const { mockRequest } = await import("./mockApi");

function token(username: string, groups: string[]): string {
  const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, "");
  return `${b64({ alg: "none" })}.${b64({ username, "cognito:groups": groups })}.sig`;
}
const MAYA = token("maya", ["staffer"]);
const CHIEF = token("chiefofstaff", ["admin"]);
const DANA = token("dana", ["staffer"]);

describe("mock GET /sessions/{sid}", () => {
  beforeEach(() => store.clear());

  it("reads active, then revoked after the Chief of Staff offboards Maya", async () => {
    const card = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    expect(await mockRequest("GET", `/sessions/${card.sid}`, undefined, MAYA)).toMatchObject({ sid: card.sid, status: "active" });
    await mockRequest("POST", "/staff/maya/offboard", {}, CHIEF);
    expect(await mockRequest("GET", `/sessions/${card.sid}`, undefined, MAYA)).toMatchObject({ status: "revoked" });
    expect(await mockRequest("GET", `/sessions/${card.sid}`, undefined, CHIEF)).toMatchObject({ status: "revoked" });
  });

  it("is 404 for another staffer and for an unknown session", async () => {
    const card = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await expect(mockRequest("GET", `/sessions/${card.sid}`, undefined, DANA)).rejects.toMatchObject({ status: 404 });
    await expect(mockRequest("GET", "/sessions/nope", undefined, MAYA)).rejects.toMatchObject({ status: 404 });
  });
});

describe("mock chat console", () => {
  beforeEach(() => store.clear());

  type Page = { messages: { role: string; text: string }[] };
  type Drafts = { drafts: { id: string; category: string }[] };
  type Autonomy = { categories: { category: string; level: string; suggestAuto: boolean }[] };

  async function ask(sid: string, text: string): Promise<string> {
    await mockRequest("POST", `/sessions/${sid}/messages`, { text }, MAYA);
    vi.advanceTimersByTime(2000);
    const page = (await mockRequest("GET", `/sessions/${sid}/messages`, undefined, MAYA)) as Page;
    return page.messages[page.messages.length - 1].text;
  }

  it("holds replies, offers autonomy after 3 unchanged approvals, then sends that kind alone", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const { sid } = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };

    expect(await ask(sid, "Check my email")).toContain("Waiting for your review");
    const { drafts } = (await mockRequest("GET", "/drafts", undefined, MAYA)) as Drafts;
    expect(drafts.map((d) => d.category)).toEqual(["case_status", "case_status", "case_status", "case_status"]);

    for (const d of drafts.slice(0, 3)) await mockRequest("POST", `/drafts/${d.id}/approve`, {}, MAYA);
    const offer = (await mockRequest("GET", "/autonomy", undefined, MAYA)) as Autonomy;
    expect(offer.categories.find((c) => c.category === "case_status")?.suggestAuto).toBe(true);

    await mockRequest("PUT", "/autonomy/case_status", { level: "auto" }, MAYA);
    expect(await ask(sid, "Any new mail?")).toMatch(/Handled:\n• Reply to \w+ .*: sent/);
    vi.useRealTimers();
  });

  it("never lets a locked category send alone, and keeps another staffer out", async () => {
    const { sid } = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await expect(mockRequest("PUT", "/autonomy/sensitive", { level: "auto" }, MAYA)).rejects.toMatchObject({ status: 403 });
    await expect(mockRequest("GET", `/sessions/${sid}/messages`, undefined, DANA)).rejects.toMatchObject({ status: 404 });
  });
});

describe("mock post drafts", () => {
  beforeEach(() => store.clear());

  it("drafts only the post text, whether the request uses a colon or 'saying'", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const { sid } = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("POST", `/sessions/${sid}/messages`, { text: "I want to make a post on X saying office hours moved to Friday" }, MAYA);
    vi.advanceTimersByTime(2000);
    await mockRequest("GET", `/sessions/${sid}/messages`, undefined, MAYA);
    const { drafts } = (await mockRequest("GET", "/drafts", undefined, MAYA)) as { drafts: { args: Record<string, string> }[] };
    expect(drafts[0].args.text).toBe("office hours moved to Friday");
    vi.useRealTimers();
  });
});
