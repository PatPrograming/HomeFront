import { agentActions, connectionMessage, type AgentAction } from "./errors";

// What the chat box shows after a message could not be sent: the reason, and the ways back the server offered.
export interface SendFailure {
  error: string;
  actions: AgentAction[];
  text: string; // the message that was refused, so Try again resends it even if the box was cleared
}

function failureOf(e: unknown, text: string): SendFailure {
  return { error: connectionMessage(e, false), actions: agentActions(e), text };
}

// Send once. null means it went through.
export async function attemptSend(send: (text: string) => Promise<void>, text: string): Promise<SendFailure | null> {
  try {
    await send(text);
    return null;
  } catch (e) {
    return failureOf(e, text);
  }
}

// "Try again": ask the server to reset this conversation's restart count first, and only then resend the message it
// refused. If the server refuses the retry (too soon, too often), say why and what is still possible.
export async function retryThenResend(
  retryAgent: () => Promise<unknown>,
  send: (text: string) => Promise<void>,
  text: string,
): Promise<SendFailure | null> {
  try {
    await retryAgent();
  } catch (e) {
    return failureOf(e, text);
  }
  return attemptSend(send, text);
}
