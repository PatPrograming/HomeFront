import { expiryMs } from "../components/IdentityCard";
import type { AgentStatus, ChatMessage, SessionCard } from "./api";

// The chat replaces "Start a session": the staffer's first message starts one through the broker
// (POST /sessions, same rules as before: their Cognito login, an active staffer; the agent later
// needs its own client credentials for the mandate token). An expired session is replaced the same
// way. A revoked one is not: the console keeps showing Revoked and the chat stays closed.
export interface ChatDeps {
  startSession: () => Promise<SessionCard>;
  sendMessage: (sid: string, text: string) => Promise<ChatMessage & { agent?: AgentStatus }>;
}

export function needsNewSession(card: SessionCard | null, now: number): boolean {
  return card === null || expiryMs(card.expiresAt) <= now;
}

export async function sendInSession(
  card: SessionCard | null,
  text: string,
  now: number,
  deps: ChatDeps,
): Promise<{ card: SessionCard; message: ChatMessage & { agent?: AgentStatus } }> {
  const live = needsNewSession(card, now) ? await deps.startSession() : card!;
  const message = await deps.sendMessage(live.sid, text);
  return { card: live, message };
}
