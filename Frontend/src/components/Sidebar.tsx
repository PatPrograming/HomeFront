import { useEffect, useRef, useState } from "react";
import { api, type SessionSummary } from "../lib/api";
import { connectionMessage } from "../lib/errors";
import { loadCollapsed, runAction, saveCollapsed, type SidebarAction } from "../lib/sidebar";
import { useSessions } from "../lib/useSessions";
import { SidebarView } from "./SidebarView";

// The sidebar with its state: search (as you type, on the server), collapse (remembered per browser), the open
// menu, rename and delete confirmation. Ctrl/Cmd+K focuses search. Changes go to the server, then both lists reload.
export default function Sidebar({
  currentSid,
  forceExpanded,
  onSelect,
  onNew,
  onChanged,
  onRemoved,
  onRequestOpen,
}: {
  currentSid: string | null;
  forceExpanded: boolean; // in the phone drawer the sidebar is always full
  onSelect: (session: SessionSummary) => void;
  onNew: () => void;
  onChanged: () => void; // reload the console's own list
  onRemoved: (sid: string) => void; // the open conversation was archived or deleted
  onRequestOpen: () => void; // Ctrl+K on a phone opens the drawer
}) {
  const [query, setQuery] = useState("");
  const [archivedView, setArchivedView] = useState(false);
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [menuMode, setMenuMode] = useState<"main" | "group">("main");
  const [renamingSid, setRenamingSid] = useState<string | null>(null);
  const [renameText, setRenameText] = useState("");
  const [groupText, setGroupText] = useState("");
  const [confirmDeleteSid, setConfirmDeleteSid] = useState<string | null>(null);
  const [closedGroups, setClosedGroups] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const list = useSessions(query, archivedView);
  const navRef = useRef<HTMLDivElement>(null);
  const isCollapsed = collapsed && !forceExpanded;

  function openMenu(sid: string | null) {
    setMenuFor(sid);
    setMenuMode("main");
    setGroupText("");
  }

  // Ctrl/Cmd+K: show the sidebar and put the cursor in search.
  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCollapsed(false);
        saveCollapsed(false);
        onRequestOpen();
        window.setTimeout(() => document.getElementById("sidebar-search")?.focus(), 50);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onRequestOpen]);

  // An open menu takes the focus on its first item, and a click anywhere else closes it.
  useEffect(() => {
    if (!menuFor) return;
    navRef.current?.querySelector<HTMLElement>('[role="menu"] [role="menuitem"], [role="menu"] input')?.focus();
    function onDown(e: MouseEvent) {
      const t = e.target as HTMLElement;
      if (!t.closest(".sidebar-menu") && !t.closest(".item-more")) setMenuFor(null);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuFor, menuMode]);

  // Rename starts with the whole title selected, so typing replaces it, as in other chat apps.
  useEffect(() => {
    if (renamingSid) navRef.current?.querySelector<HTMLInputElement>(".sidebar-rename")?.select();
  }, [renamingSid]);

  async function act(action: SidebarAction) {
    setError(null);
    try {
      await runAction(api, action);
      const gone = action.kind === "delete" || (action.kind === "archive" && action.archived);
      if (gone && action.sid === currentSid) onRemoved(action.sid);
      setMenuFor(null);
      setRenamingSid(null);
      setConfirmDeleteSid(null);
      if (action.kind !== "rename" && action.kind !== "delete") document.getElementById(`more-${action.sid}`)?.focus();
      await list.reload();
      onChanged();
    } catch (e) {
      setError(connectionMessage(e, false));
    }
  }

  return (
    <div ref={navRef} className="sidebar-wrap">
      <SidebarView
        sessions={list.sessions}
        currentSid={currentSid}
        query={query}
        archivedView={archivedView}
        collapsed={isCollapsed}
        menuFor={menuFor}
        menuMode={menuMode}
        renamingSid={renamingSid}
        renameText={renameText}
        groupText={groupText}
        confirmDeleteSid={confirmDeleteSid}
        closedGroups={closedGroups}
        onQuery={setQuery}
        onToggleCollapsed={() => {
          saveCollapsed(!collapsed);
          setCollapsed(!collapsed);
        }}
        onToggleArchived={() => {
          setArchivedView(!archivedView);
          openMenu(null);
        }}
        onNew={onNew}
        onSelect={onSelect}
        onMenu={openMenu}
        onMenuMode={setMenuMode}
        onStartRename={(sid) => {
          setRenamingSid(sid);
          setRenameText(list.sessions.find((s) => s.sid === sid)?.title ?? "");
          setMenuFor(null);
        }}
        onRenameText={setRenameText}
        onGroupText={setGroupText}
        onToggleGroup={(name) =>
          setClosedGroups(closedGroups.includes(name) ? closedGroups.filter((g) => g !== name) : [...closedGroups, name])
        }
        onAskDelete={(sid) => {
          setConfirmDeleteSid(sid);
          setMenuFor(null);
        }}
        onAction={act}
      />
      {error && !isCollapsed && (
        <p className="alert sidebar-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
