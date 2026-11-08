import { useCallback, useEffect, useState } from "react";
import { api, type AgentStatus, type ChatMessage } from "./api";

// The console chat for one session. Polls GET /sessions/{sid}/messages?after=<cursor> every second, like the
// decision feed, because the agent posts its replies from its own process. `send` shows the staffer's message
// as soon as the API has stored it.
export interface Chat {
  messages: ChatMessage[];
  error: Error | null;
  // From the last send: whether the backend started the agent itself (null until the first send)
  agent: AgentStatus | null;
  send: (text: string) => Promise<void>;
}

function merge(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const byId = new Map(current.map((m) => [m.id, m]));
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort((a, b) => (a.ts < b.ts ? -1 : 1));
}

export function useChat(sid: string | null, intervalMs = 1000): Chat {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [agent, setAgent] = useState<AgentStatus | null>(null);

  useEffect(() => {
    setMessages([]);
    setAgent(null);
    if (!sid) return;
    let cursor: string | null = null;
    let busy = false;
    let stopped = false;

    async function poll() {
      if (busy || !sid) return;
      busy = true;
      try {
        const page = await api.messages(sid, cursor);
        if (stopped) return;
        cursor = page.next ?? cursor;
        if (page.messages.length) setMessages((current) => merge(current, page.messages));
        setError(null);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        busy = false;
      }
    }

    poll();
    const id = window.setInterval(poll, intervalMs);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [sid, intervalMs]);

  const send = useCallback(
    async (text: string) => {
      if (!sid) return;
      const { agent: status, ...message } = await api.sendMessage(sid, text);
      setMessages((current) => merge(current, [message]));
      setAgent(status ?? null);
    },
    [sid],
  );

  return { messages, error, agent, send };
}
