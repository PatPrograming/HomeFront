import { describe, expect, it, vi } from "vitest";
import type { SessionSummary } from "./api";
import { loadCollapsed, runAction, saveCollapsed, sections } from "./sidebar";

const s = (sid: string, over: Partial<SessionSummary> = {}): SessionSummary => ({
  sid, title: sid, status: "active", waiting: 0, pinned: false, group: null, archived: false,
  agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [],
  createdAt: "2026-11-08T08:00:00Z", expiresAt: "2026-11-08T09:00:00Z", ...over,
});

describe("sidebar sections", () => {
  it("puts pinned first, then groups by name, then the rest newest first", () => {
    const out = sections([
      s("old", { createdAt: "2026-11-08T07:00:00Z" }),
      s("new", { createdAt: "2026-11-08T09:00:00Z" }),
      s("pinned-grouped", { pinned: true, group: "Housing" }),
      s("b1", { group: "Veterans" }),
      s("a1", { group: "Housing" }),
    ]);
    expect(out.pinned.map((x) => x.sid)).toEqual(["pinned-grouped"]); // pinned wins over its group
    expect(out.groups.map((g) => [g.name, g.items.map((x) => x.sid)])).toEqual([["Housing", ["a1"]], ["Veterans", ["b1"]]]);
    expect(out.recent.map((x) => x.sid)).toEqual(["new", "old"]);
  });
});

describe("sidebar actions call the right API", () => {
  const api = () => ({ updateSession: vi.fn(async () => ({})), deleteSession: vi.fn(async () => ({})) });

  it.each([
    [{ kind: "rename", sid: "x", title: "Housing follow-up" }, { title: "Housing follow-up" }],
    [{ kind: "pin", sid: "x", pinned: true }, { pinned: true }],
    [{ kind: "pin", sid: "x", pinned: false }, { pinned: false }],
    [{ kind: "group", sid: "x", group: "Veterans" }, { group: "Veterans" }],
    [{ kind: "group", sid: "x", group: null }, { group: null }],
    [{ kind: "archive", sid: "x", archived: true }, { archived: true }],
    [{ kind: "archive", sid: "x", archived: false }, { archived: false }],
  ] as const)("%o → PATCH %o", async (action, patch) => {
    const fake = api();
    await runAction(fake, action);
    expect(fake.updateSession).toHaveBeenCalledWith("x", patch);
    expect(fake.deleteSession).not.toHaveBeenCalled();
  });

  it("delete → DELETE", async () => {
    const fake = api();
    await runAction(fake, { kind: "delete", sid: "x" });
    expect(fake.deleteSession).toHaveBeenCalledWith("x");
    expect(fake.updateSession).not.toHaveBeenCalled();
  });

  it("a blank rename is ignored", async () => {
    const fake = api();
    await runAction(fake, { kind: "rename", sid: "x", title: "   " });
    expect(fake.updateSession).not.toHaveBeenCalled();
  });
});

describe("sidebar collapse is remembered per browser", () => {
  it("saves and loads it", () => {
    const box = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => box.get(k) ?? null, setItem: (k: string, v: string) => void box.set(k, v) });
    expect(loadCollapsed()).toBe(false);
    saveCollapsed(true);
    expect(loadCollapsed()).toBe(true);
    saveCollapsed(false);
    expect(loadCollapsed()).toBe(false);
  });

  it("works when storage is blocked (private window)", () => {
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    expect(loadCollapsed()).toBe(false);
    expect(() => saveCollapsed(true)).not.toThrow();
  });
});
