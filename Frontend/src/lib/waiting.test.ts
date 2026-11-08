import { describe, expect, it } from "vitest";
import type { Draft } from "./api";
import { sortDrafts } from "./waiting";

const d = (id: string, score: number, createdAt: string): Draft =>
  ({ id, score, createdAt, urgency: "low", reason: "", status: "pending", tool: "email_send", category: "c", categoryLabel: "C", args: {} }) as Draft;

describe("waiting: order", () => {
  const drafts = [d("old-low", 10, "2026-11-08T08:00:00Z"), d("new-high", 80, "2026-11-08T08:30:00Z"), d("mid", 40, "2026-11-08T08:10:00Z")];

  it("ranks by score, oldest first among equals", () => {
    expect(sortDrafts(drafts, "rank").map((x) => x.id)).toEqual(["new-high", "mid", "old-low"]);
    expect(sortDrafts([d("b", 5, "2026-11-08T09:00:00Z"), d("a", 5, "2026-11-08T08:00:00Z")], "rank").map((x) => x.id)).toEqual(["a", "b"]);
  });

  it("orders by time, oldest first", () => {
    expect(sortDrafts(drafts, "time").map((x) => x.id)).toEqual(["old-low", "mid", "new-high"]);
  });
});
