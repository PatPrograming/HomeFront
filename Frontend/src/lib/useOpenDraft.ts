import { useCallback, useState } from "react";

// Which draft is open in the review side panel (null: none). Its own hook so the console's tests can open one.
export function useOpenDraft(): { openId: string | null; open: (id: string) => void; close: () => void } {
  const [openId, setOpenId] = useState<string | null>(null);
  const close = useCallback(() => setOpenId(null), []);
  return { openId, open: setOpenId, close };
}
