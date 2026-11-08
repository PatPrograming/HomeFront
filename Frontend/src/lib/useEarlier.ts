import { useEffect, useState } from "react";
import { api, type ChatMessage } from "./api";

// The messages of a conversation's earlier sessions (Lane E: GET /sessions lists them as `earlier`). An ended
// session can't change, so they load once per set of sids. Owner only on the server, like every chat read.
export function useEarlier(sids: string[]): ChatMessage[] {
  const key = sids.join(",");
  const [loaded, setLoaded] = useState<{ key: string; messages: ChatMessage[] }>({ key: "", messages: [] });

  useEffect(() => {
    if (!key) return;
    let stopped = false;
    Promise.all(key.split(",").map((sid) => api.messages(sid, null)))
      .then((pages) => {
        if (stopped) return;
        const messages = pages.flatMap((p) => p.messages).sort((a, b) => (a.ts < b.ts ? -1 : 1));
        setLoaded({ key, messages });
      })
      .catch(() => {}); // the tab's own history (or nothing) shows meanwhile; the next listing retries
    return () => {
      stopped = true;
    };
  }, [key]);

  return loaded.key === key ? loaded.messages : [];
}
