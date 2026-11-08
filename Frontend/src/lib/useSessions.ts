import { useCallback, useEffect, useState } from "react";
import { api, type SessionSummary } from "./api";

// The staffer's conversations (GET /sessions), polled every few seconds so titles, statuses and waiting counts
// stay current, and reloaded right after starting one.
export function useSessions(intervalMs = 3000): { sessions: SessionSummary[]; error: Error | null; reload: () => Promise<void> } {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [error, setError] = useState<Error | null>(null);

  const reload = useCallback(async () => {
    try {
      setSessions((await api.listSessions()).sessions);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e : new Error(String(e)));
    }
  }, []);

  useEffect(() => {
    reload();
    const id = window.setInterval(reload, intervalMs);
    return () => window.clearInterval(id);
  }, [reload, intervalMs]);

  return { sessions, error, reload };
}
