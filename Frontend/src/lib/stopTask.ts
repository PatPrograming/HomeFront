// What the staffer can type to stop the running task. The console catches these itself and calls
// POST /sessions/{sid}/revoke: the words never reach the agent, so a compromised agent cannot ignore them.
// Only a whole message counts ("stop", "Cancel.", "revoke this task"), so "stop emailing Jane" is still a task.
const STOP_PHRASES = new Set([
  "stop",
  "cancel",
  "revoke this task",
  "stop this task",
  "cancel this task",
]);

export function isStopPhrase(text: string): boolean {
  const words = text.trim().toLowerCase().replace(/[.!]+$/, "").replace(/\s+/g, " ");
  return STOP_PHRASES.has(words);
}

export const STOPPED_TEXT = "Stopped. The assistant can do nothing more in this conversation.";
