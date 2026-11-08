import { useCallback, useEffect, useState } from "react";
import { api, type AutonomyCategory, type Draft } from "./api";

// The review queue (drafts the gateway held) and what the agent may send alone. Polls every 2 s, because the
// agent adds drafts while it works, and reloads right after each of the staffer's own decisions.
export interface Review {
  drafts: Draft[];
  categories: AutonomyCategory[];
  error: Error | null;
  reload: () => Promise<void>;
}

export function useReview(intervalMs = 2000): Review {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [categories, setCategories] = useState<AutonomyCategory[]>([]);
  const [error, setError] = useState<Error | null>(null);

  const reload = useCallback(async () => {
    try {
      const [queue, autonomy] = await Promise.all([api.drafts(), api.autonomy()]);
      setDrafts(queue.drafts);
      setCategories(autonomy.categories);
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

  return { drafts, categories, error, reload };
}
