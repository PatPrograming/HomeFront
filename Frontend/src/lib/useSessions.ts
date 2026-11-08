import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SessionSummary } from "./api";

// The staffer's conversations (GET /sessions?q=&archived=1), polled every few seconds so titles, statuses and
// waiting counts stay current, and reloaded right after a change.
export function useSessions(
  q = "",
  archived = false,
  intervalMs = 3000,
): { sessions: SessionSummary[]; error: Error | null; reload: () => Promise<void> } {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const latest = useRef(0); // typing fires a request per key; only the newest answer may show

  const reload = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const page = await api.listSessions(q, archived);
      if (mine !== latest.current) return;
      setSessions(page.sessions);
      setError(null);
    } catch (e) {
      if (mine === latest.current) setError(e instanceof Error ? e : new Error(String(e)));
    }
  }, [q, archived]);

  useEffect(() => {
    reload();
    const id = window.setInterval(reload, intervalMs);
    return () => window.clearInterval(id);
  }, [reload, intervalMs]);

  return { sessions, error, reload };
}
