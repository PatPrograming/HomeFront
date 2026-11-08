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

describe("mock per-session grants", () => {
  beforeEach(() => store.clear());

  async function ask(sid: string, text: string): Promise<string> {
    await mockRequest("POST", `/sessions/${sid}/messages`, { text }, MAYA);
    vi.advanceTimersByTime(2000);
    const page = (await mockRequest("GET", `/sessions/${sid}/messages`, undefined, MAYA)) as { messages: { text: string }[] };
    return page.messages[page.messages.length - 1].text;
  }

  it("a grant sends that category alone in this session only, and offboarding closes it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const { sid } = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    expect(await ask(sid, "Check my email")).toContain("Waiting for your review");
    expect(await mockRequest("POST", `/sessions/${sid}/grants`, { category: "case_status" }, MAYA)).toMatchObject({ scope: "this session only" });
    expect(await ask(sid, "Any new mail?")).toMatch(/Handled:\n• Reply to \w+ .*: sent/);

    await expect(mockRequest("POST", `/sessions/${sid}/grants`, { category: "staff_memo" }, MAYA)).rejects.toMatchObject({ status: 403 });
    await expect(mockRequest("POST", `/sessions/${sid}/grants`, { category: "case_status" }, DANA)).rejects.toMatchObject({ status: 404 });

    await mockRequest("POST", "/staff/maya/offboard", {}, CHIEF);
    await expect(mockRequest("POST", `/sessions/${sid}/grants`, { category: "case_status" }, MAYA)).rejects.toMatchObject({ status: 403 });
    await expect(mockRequest("POST", `/sessions/${sid}/messages`, { text: "hi" }, MAYA)).rejects.toMatchObject({ status: 403 });
    vi.useRealTimers();
  });
});

describe("mock forbidden request", () => {
  beforeEach(() => store.clear());

  it("refuses a bulk export, naming the policy, with one DENY event and no draft", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const { sid } = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("POST", `/sessions/${sid}/messages`, { text: "Export all constituent records" }, MAYA);
    vi.advanceTimersByTime(2000);
    const page = (await mockRequest("GET", `/sessions/${sid}/messages`, undefined, MAYA)) as { messages: { text: string }[] };
    expect(page.messages[page.messages.length - 1].text).toContain("forbid-bulk-export");
    const { events } = (await mockRequest("GET", "/events", undefined, MAYA)) as { events: { sid: string; tool: string; decision: string }[] };
    const exports = events.filter((e) => e.sid === sid && e.tool === "records_bulk_export");
    expect(exports.map((e) => e.decision)).toEqual(["DENY"]); // denied once, not retried
    expect(((await mockRequest("GET", "/drafts", undefined, MAYA)) as { drafts: unknown[] }).drafts).toEqual([]);
    vi.useRealTimers();
  });
});

describe("mock GET /sessions: several conversations", () => {
  beforeEach(() => store.clear());

  it("keeps earlier conversations open, lists the caller's own with title and waiting count", async () => {
    vi.useFakeTimers({ toFake: ["Date"] }); // only the clock: mockRequest still waits its 120 ms
    const a = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    const b = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("POST", "/sessions", {}, DANA);
    await mockRequest("POST", `/sessions/${a.sid}/messages`, { text: "Check my email please" }, MAYA);
    vi.advanceTimersByTime(2000);
    await mockRequest("GET", `/sessions/${a.sid}/messages`, undefined, MAYA); // the scripted reply holds drafts
    const { sessions } = (await mockRequest("GET", "/sessions", undefined, MAYA)) as {
      sessions: { sid: string; title: string; status: string; waiting: number; agent: string }[];
    };
    expect(sessions.map((s) => s.sid).sort()).toEqual([a.sid, b.sid].sort()); // not Dana's
    const first = sessions.find((s) => s.sid === a.sid)!;
    expect(first).toMatchObject({ title: "Check my email please", status: "active", agent: "agent-1" });
    expect(first.waiting).toBeGreaterThan(0);
    expect(sessions.find((s) => s.sid === b.sid)).toMatchObject({ title: "New conversation", status: "active", waiting: 0 });
    vi.useRealTimers();
  });

  it("each conversation keeps its own chat", async () => {
    vi.useFakeTimers({ toFake: ["Date"] }); // only the clock: mockRequest still waits its 120 ms
    const a = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    const b = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("POST", `/sessions/${a.sid}/messages`, { text: "only in A" }, MAYA);
    const inB = (await mockRequest("GET", `/sessions/${b.sid}/messages`, undefined, MAYA)) as { messages: { text: string }[] };
    expect(inB.messages).toEqual([]);
    vi.useRealTimers();
  });

  it("caps open conversations per person", async () => {
    vi.useFakeTimers({ toFake: ["Date"] }); // only the clock: mockRequest still waits its 120 ms
    for (let i = 0; i < 5; i++) await mockRequest("POST", "/sessions", {}, MAYA);
    await expect(mockRequest("POST", "/sessions", {}, MAYA)).rejects.toMatchObject({ status: 409 });
    vi.useRealTimers();
  });

  it("ranks waiting drafts with urgency, score and reason", async () => {
    vi.useFakeTimers({ toFake: ["Date"] }); // only the clock: mockRequest still waits its 120 ms
    const a = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("POST", `/sessions/${a.sid}/messages`, { text: "Check my email" }, MAYA);
    vi.advanceTimersByTime(2000);
    await mockRequest("GET", `/sessions/${a.sid}/messages`, undefined, MAYA);
    const { drafts } = (await mockRequest("GET", "/drafts", undefined, MAYA)) as { drafts: { score: number; urgency: string; reason: string }[] };
    expect(drafts.length).toBeGreaterThan(1);
    expect(drafts.map((d) => d.score)).toEqual([...drafts.map((d) => d.score)].sort((x, y) => y - x));
    for (const d of drafts) expect(["high", "medium", "low"]).toContain(d.urgency);
    expect(drafts[0].reason).toMatch(/Case status updates/);
  });
});

describe("mock sidebar routes", () => {
  beforeEach(() => store.clear());
  type List = { sessions: { sid: string; title: string; pinned: boolean; group: string | null; archived: boolean }[] };
  const list = async (query = "") => ((await mockRequest("GET", `/sessions${query}`, undefined, MAYA)) as List).sessions;

  it("names a conversation short, from what the staffer asked first", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const a = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("POST", `/sessions/${a.sid}/messages`, { text: "Can you please check my email and answer what you can?" }, MAYA);
    const title = (await list())[0].title;
    expect(title.split(" ").length).toBeLessThanOrEqual(5);
    expect(title).toMatch(/^Check my email/);
    vi.useRealTimers();
  });

  it("renames, pins (pinned first), groups, searches, archives, restores and soft-deletes", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const a = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    vi.advanceTimersByTime(1000);
    const b = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("PATCH", `/sessions/${a.sid}`, { title: "Monroe pothole", pinned: true, group: "Roads" }, MAYA);
    expect((await list())[0]).toMatchObject({ sid: a.sid, title: "Monroe pothole", pinned: true, group: "Roads" });
    expect((await list("?q=monroe")).map((s) => s.sid)).toEqual([a.sid]);

    await mockRequest("PATCH", `/sessions/${b.sid}`, { archived: true }, MAYA);
    expect((await list()).map((s) => s.sid)).toEqual([a.sid]);
    expect((await list("?archived=1")).map((s) => s.sid)).toEqual([b.sid]);
    await mockRequest("PATCH", `/sessions/${b.sid}`, { archived: false }, MAYA);
    expect((await list()).map((s) => s.sid).sort()).toEqual([a.sid, b.sid].sort());

    await mockRequest("DELETE", `/sessions/${b.sid}`, undefined, MAYA);
    expect((await list()).map((s) => s.sid)).toEqual([a.sid]);
    expect(await list("?archived=1")).toEqual([]);
    vi.useRealTimers();
  });

  it("only the owner can change a conversation", async () => {
    const a = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await expect(mockRequest("PATCH", `/sessions/${a.sid}`, { title: "x" }, DANA)).rejects.toMatchObject({ status: 404 });
    await expect(mockRequest("DELETE", `/sessions/${a.sid}`, undefined, DANA)).rejects.toMatchObject({ status: 404 });
  });
});

describe("mock sidebar auto titles", () => {
  it.each([
    ["Check my email, answer what you can and show me what I need to review.", "Check my email"],
    ["Post on the official account: Town hall this Tuesday", "Post on the official account"],
    ["Could you please look up case 7 about the Monroe pothole", "Look up case 7"],
  ])("%s → %s", async (text, title) => {
    const { autoTitle } = await import("./mockApi");
    expect(autoTitle(text)).toBe(title);
  });
});

describe("mock draft edits (Lane H's routes)", () => {
  beforeEach(() => store.clear());
  type D = { id: string; version: number; revising: boolean; args: Record<string, string>; editable: string[] };
  const drafts = async () => ((await mockRequest("GET", "/drafts", undefined, MAYA)) as { drafts: D[] }).drafts;

  async function heldReply(): Promise<D> {
    vi.useFakeTimers({ toFake: ["Date"] });
    const a = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("POST", `/sessions/${a.sid}/messages`, { text: "Check my email" }, MAYA);
    vi.advanceTimersByTime(2000);
    await mockRequest("GET", `/sessions/${a.sid}/messages`, undefined, MAYA);
    return (await drafts())[0];
  }

  it("saves the staffer's edits as the next version, and refuses the recipient", async () => {
    const d = await heldReply();
    expect(d).toMatchObject({ version: 1, revising: false, editable: ["subject", "body", "to"] });
    await mockRequest("PATCH", `/drafts/${d.id}`, { body: "Thanks, we are on it." }, MAYA);
    expect((await drafts()).find((x) => x.id === d.id)).toMatchObject({ version: 2, args: { body: "Thanks, we are on it." } });
    await expect(mockRequest("PATCH", `/drafts/${d.id}`, { to: "someone@else.example" }, MAYA)).rejects.toMatchObject({ status: 403 });
    vi.useRealTimers();
  });

  it("only the version on screen can be edited or approved", async () => {
    const d = await heldReply();
    await expect(mockRequest("PATCH", `/drafts/${d.id}`, { body: "x", expectedVersion: 7 }, MAYA)).rejects.toMatchObject({
      status: 409, data: { error: "stale_version" },
    });
    await mockRequest("PATCH", `/drafts/${d.id}`, { body: "x", expectedVersion: 1 }, MAYA);
    await expect(mockRequest("POST", `/drafts/${d.id}/approve`, { expectedVersion: 1 }, MAYA)).rejects.toMatchObject({ status: 409 });
    await expect(mockRequest("POST", `/drafts/${d.id}/approve`, { expectedVersion: 2 }, MAYA)).resolves.toMatchObject({ status: "sent" });
    vi.useRealTimers();
  });

  it("a post goes to one platform the server supports", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const a = (await mockRequest("POST", "/sessions", {}, MAYA)) as { sid: string };
    await mockRequest("POST", `/sessions/${a.sid}/messages`, { text: "Post on the official account: Town hall Tuesday" }, MAYA);
    vi.advanceTimersByTime(2000);
    await mockRequest("GET", `/sessions/${a.sid}/messages`, undefined, MAYA);
    const post = ((await drafts()) as (D & { platform: string })[]).find((x) => x.editable.includes("platform"))!;
    expect(post.platform).toBe("x");
    await mockRequest("PATCH", `/drafts/${post.id}`, { platform: "bluesky" }, MAYA);
    expect(((await drafts()) as (D & { platform: string })[]).find((x) => x.id === post.id)!.platform).toBe("bluesky");
    await expect(mockRequest("PATCH", `/drafts/${post.id}`, { platform: "reddit" }, MAYA)).rejects.toMatchObject({ status: 422 });
    vi.useRealTimers();
  });

  it("'make it shorter' gives a shorter next version a moment later", async () => {
    const d = await heldReply();
    await mockRequest("POST", `/drafts/${d.id}/revise`, { instruction: "make it shorter" }, MAYA);
    expect((await drafts()).find((x) => x.id === d.id)!.revising).toBe(true);
    vi.advanceTimersByTime(2000);
    const after = (await drafts()).find((x) => x.id === d.id)!;
    expect(after).toMatchObject({ version: 2, revising: false });
    expect(after.args.body.length).toBeLessThan(d.args.body.length);
    vi.useRealTimers();
  });
});
