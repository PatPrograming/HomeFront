import type { SessionPatch, SessionSummary } from "./api";

// The conversation sidebar's logic, kept apart from the markup so it can be tested without a browser.

export interface Sections {
  pinned: SessionSummary[];
  groups: { name: string; items: SessionSummary[] }[];
  recent: SessionSummary[];
}

const newestFirst = (a: SessionSummary, b: SessionSummary) => b.createdAt.localeCompare(a.createdAt);

// Pinned first (a pinned conversation shows there, not in its group), then each group by name, then the rest.
export function sections(sessions: SessionSummary[]): Sections {
  const pinned = sessions.filter((s) => s.pinned).sort(newestFirst);
  const grouped = new Map<string, SessionSummary[]>();
  for (const s of sessions) if (!s.pinned && s.group) grouped.set(s.group, [...(grouped.get(s.group) ?? []), s]);
  const groups = [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, items]) => ({ name, items: items.sort(newestFirst) }));
  const recent = sessions.filter((s) => !s.pinned && !s.group).sort(newestFirst);
  return { pinned, groups, recent };
}

export type SidebarAction =
  | { kind: "rename"; sid: string; title: string }
  | { kind: "pin"; sid: string; pinned: boolean }
  | { kind: "group"; sid: string; group: string | null }
  | { kind: "archive"; sid: string; archived: boolean }
  | { kind: "delete"; sid: string };

interface SidebarApi {
  updateSession: (sid: string, patch: SessionPatch) => Promise<unknown>;
  deleteSession: (sid: string) => Promise<unknown>;
}

// Each menu action is one call to Lane E's routes: PATCH for the changes, DELETE (soft) for delete.
export async function runAction(api: SidebarApi, action: SidebarAction): Promise<void> {
  switch (action.kind) {
    case "rename":
      if (action.title.trim()) await api.updateSession(action.sid, { title: action.title.trim() });
      return;
    case "pin":
      await api.updateSession(action.sid, { pinned: action.pinned });
      return;
    case "group":
      await api.updateSession(action.sid, { group: action.group });
      return;
    case "archive":
      await api.updateSession(action.sid, { archived: action.archived });
      return;
    case "delete":
      await api.deleteSession(action.sid);
  }
}

// Collapsed or not is a per-browser convenience; private windows can block storage, so never let that break the page.
const COLLAPSED_KEY = "homefront.sidebar.collapsed";

export function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // storage blocked: the choice lasts until reload
  }
}
