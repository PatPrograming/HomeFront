// Plain words for errors staff see. The browser's own network errors ("Failed to fetch" in Chrome, "Load failed" in
// Safari, "NetworkError…" in Firefox) and request timeouts all mean the same thing to a staffer: the server is out
// of reach. A real answer from the server (ApiError) keeps the server's words.
function unreachable(error: unknown): boolean {
  if (error instanceof DOMException) return error.name === "TimeoutError" || error.name === "AbortError";
  return error instanceof TypeError && /failed to fetch|load failed|networkerror/i.test(error.message);
}

export function connectionMessage(error: unknown, retrying: boolean): string {
  if (unreachable(error)) {
    return retrying ? "Can't reach HomeFront's server. Retrying…" : "Can't reach HomeFront's server. Check your connection and try again.";
  }
  return error instanceof Error ? error.message : String(error);
}

export type AgentAction = "retry" | "new_conversation";

// What the server offers when the assistant can't start (it stopped too often, or isn't set up): a 503 whose detail
// carries `actions`. Anything else offers nothing.
export function agentActions(error: unknown): AgentAction[] {
  const detail = (error as { data?: { detail?: unknown } } | null)?.data?.detail;
  const actions = detail && typeof detail === "object" && "actions" in detail ? (detail as { actions: unknown }).actions : [];
  return Array.isArray(actions) ? actions.filter((a): a is AgentAction => a === "retry" || a === "new_conversation") : [];
}
