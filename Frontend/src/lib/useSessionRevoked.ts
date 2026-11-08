import { useEffect, useState } from "react";
import { api } from "./api";

// Polls GET /api/v1/sessions/{sid} every `intervalMs` (2 s by default) while a session is shown, and turns
// true once the API says the session is no longer active (the Chief of Staff offboarded the staffer).
// Revoked is final, so polling stops there. A failed poll changes nothing; the next one tries again.
export function useSessionRevoked(sid: string | null, intervalMs = 2000): boolean {
  const [revokedSid, setRevokedSid] = useState<string | null>(null);

  useEffect(() => {
    if (!sid) return;
    let busy = false;
    let stopped = false;
    let id = 0;

    async function poll() {
      if (busy || !sid) return;
      busy = true;
      try {
        const session = await api.sessionStatus(sid);
        if (!stopped && session.status !== "active") {
          setRevokedSid(sid);
          stopped = true;
          window.clearInterval(id);
        }
      } catch {
        // keep the last known state; the feed's "session revoked" DENY also marks the card
      } finally {
        busy = false;
      }
    }

    poll();
    id = window.setInterval(poll, intervalMs);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [sid, intervalMs]);

  return sid !== null && revokedSid === sid;
}
