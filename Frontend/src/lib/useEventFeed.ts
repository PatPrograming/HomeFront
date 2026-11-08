import { useEffect, useState } from "react";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import { api } from "./api";
import { mergeEvents, nextCursor } from "./events";

// Polls GET /api/v1/events?since=<cursor> every `intervalMs` (1 s by default) and keeps the events newest
// first. A poll is skipped while the previous one is still running, so a slow API never piles up requests.
export interface EventFeed {
  events: DecisionEvent[];
  error: Error | null;
  lastPollAt: number | null;
}

export function useEventFeed({ limit = Infinity, intervalMs = 1000 }: { limit?: number; intervalMs?: number } = {}): EventFeed {
  const [events, setEvents] = useState<DecisionEvent[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [lastPollAt, setLastPollAt] = useState<number | null>(null);

  useEffect(() => {
    let cursor: string | null = null;
    let busy = false;
    let stopped = false;

    async function poll() {
      if (busy) return;
      busy = true;
      try {
        const page = await api.events(cursor);
        if (stopped) return;
        cursor = nextCursor(cursor, page);
        const incoming = page?.events ?? [];
        if (incoming.length) setEvents((current) => mergeEvents(current, incoming, limit));
        setError(null);
        setLastPollAt(Date.now());
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
  }, [limit, intervalMs]);

  return { events, error, lastPollAt };
}
