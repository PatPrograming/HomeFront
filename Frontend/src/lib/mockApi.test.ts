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
