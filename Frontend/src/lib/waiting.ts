import type { Draft } from "./api";

export type WaitingOrder = "rank" | "time";

// Ranked: the server's score, highest first, the oldest first among equals. By time: oldest first.
export function sortDrafts(drafts: Draft[], order: WaitingOrder): Draft[] {
  const byTime = (a: Draft, b: Draft) => a.createdAt.localeCompare(b.createdAt);
  return [...drafts].sort(order === "rank" ? (a, b) => b.score - a.score || byTime(a, b) : byTime);
}
