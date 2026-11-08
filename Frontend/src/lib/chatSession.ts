import { expiryMs } from "../components/IdentityCard";
import type { ChatMessage, SessionCard } from "./api";

// The chat replaces "Start a session": the staffer's first message starts one through the broker
// (POST /sessions, same rules as before: their Cognito login, an active staffer; the agent later
// needs its own client credentials for the mandate token). A session lasts an hour; after that the next message
// continues the conversation in a fresh session (Lane E: POST /sessions {continues: <old sid>}), so it keeps
// its title and history. A revoked one is never continued: the console keeps showing Revoked.
export type StartedCard = SessionCard & { continuesFrom?: string; grantsReset?: boolean };

export interface ChatDeps {
  startSession: (continues?: string) => Promise<StartedCard>;
  sendMessage: (sid: string, text: string) => Promise<ChatMessage>;
  // Another tab already continued this conversation: find its newest session (from GET /sessions).
  findContinuation?: (oldSid: string) => Promise<SessionCard | null>;
}

// The broker refused to continue because the old session was revoked (or its staffer offboarded). The console shows
// the Revoked banner with "Start a new conversation" instead of an error, and sends nothing.
export class SessionRevokedError extends Error {
  constructor(readonly sid: string) {
    super("This conversation's session was revoked.");
    this.name = "SessionRevokedError";
  }
}

export function needsNewSession(card: SessionCard | null, now: number): boolean {
  return card === null || expiryMs(card.expiresAt) <= now;
}

const refusal = (e: unknown) => {
  const err = e as { status?: number; data?: { code?: string; detail?: { code?: string } } };
  return { status: err.status, code: err.data?.code ?? err.data?.detail?.code };
};

// The session to send in after `card` has expired: its continuation, or what the broker's answer points to.
async function continued(card: SessionCard, deps: ChatDeps): Promise<StartedCard> {
  try {
    return await deps.startSession(card.sid);
  } catch (e) {
    const { status, code } = refusal(e);
    if (code === "revoked") throw new SessionRevokedError(card.sid);
    if (status === 409 && code === "still_active") return card; // our clock ran ahead of the server's
    if (status === 409 && code === "already_continued") {
      const newest = await deps.findContinuation?.(card.sid);
      if (newest) return newest;
    }
    if (status === 403 || status === 404 || (status === 409 && code === "already_continued")) return deps.startSession();
    throw e;
  }
}

export async function sendInSession(
  card: SessionCard | null,
  text: string,
  now: number,
  deps: ChatDeps,
): Promise<{ card: StartedCard; message: ChatMessage }> {
  const live = !needsNewSession(card, now) ? card! : card ? await continued(card, deps) : await deps.startSession();
  const message = await deps.sendMessage(live.sid, text);
  return { card: live, message };
}
