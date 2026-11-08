import type { ApproveResult } from "./api";

// What approving did: sent, or (Lane I) the platform must be connected first; then nothing was sent.
export function approveOutcome(result: ApproveResult): { done: true } | { done: false; connect: { label: string; url: string; note: string } } {
  if (result.status === "needs_connection") return { done: false, connect: { label: result.label, url: result.connectUrl, note: result.note } };
  return { done: true };
}
