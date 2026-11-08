import { useEffect, useState } from "react";
import { api } from "./api.js";
import { mergeEvents, nextCursor } from "./events.js";

// Polls GET /api/v1/events?since=<cursor> every `intervalMs` (1 s by default) and keeps the events newest
// first. A poll is skipped while the previous one is still running, so a slow API never piles up requests.
export function useEventFeed({ limit = Infinity, intervalMs = 1000 } = {}) {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [lastPollAt, setLastPollAt] = useState(null);

  useEffect(() => {
    let cursor = null;
    let busy = false;
    let stopped = false;

    async function poll() {
      if (busy) return;
      busy = true;
      try {
        const page = await api.events(cursor);
        if (stopped) return;
        cursor = nextCursor(cursor, page);
        if (page?.events?.length) setEvents((current) => mergeEvents(current, page.events, limit));
        setError(null);
        setLastPollAt(Date.now());
      } catch (e) {
        if (!stopped) setError(e);
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
