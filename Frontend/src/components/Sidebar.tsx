import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api, type SessionSummary } from "../lib/api";
import { connectionMessage } from "../lib/errors";
import { loadCollapsed, runAction, saveCollapsed, sections, type SidebarAction } from "../lib/sidebar";
import { useSessions } from "../lib/useSessions";

const STATUS = { active: "", revoked: "Revoked", expired: "Ended" } as const;

export interface SidebarViewProps {
  sessions: SessionSummary[];
  currentSid: string | null;
  query: string;
  archivedView: boolean;
  collapsed: boolean;
  menuFor: string | null; // whose "⋯" menu is open
  menuMode: "main" | "group";
  renamingSid: string | null;
  renameText: string;
  groupText: string;
  confirmDeleteSid: string | null;
  closedGroups: string[];
  onQuery: (q: string) => void;
  onToggleCollapsed: () => void;
  onToggleArchived: () => void;
  onNew: () => void;
  onSelect: (session: SessionSummary) => void;
  onMenu: (sid: string | null) => void;
  onMenuMode: (mode: "main" | "group") => void;
  onStartRename: (sid: string | null) => void;
  onRenameText: (text: string) => void;
  onGroupText: (text: string) => void;
  onToggleGroup: (name: string) => void;
  onAskDelete: (sid: string | null) => void;
  onAction: (action: SidebarAction) => void;
}

const Icon = ({ d }: { d: string }) => (
  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
    <path d={d} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const PLUS = "M12 5v14M5 12h14";
const PANEL = "M4 5h16v14H4zM9 5v14";

// Up and Down move between menu items; Escape closes the menu (and stops the page's own Escape handling).
function menuKeys(e: KeyboardEvent<HTMLDivElement>, close: () => void) {
  const items = [...e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]')];
  const at = items.indexOf(document.activeElement as HTMLElement);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
  } else if (e.key === "Escape") {
    e.preventDefault();
    close();
  }
}

// The conversation sidebar as markup only, no hooks, so a test can call it and press its buttons. `Sidebar`
// below holds its state.
export function SidebarView(p: SidebarViewProps) {
  if (p.collapsed) {
    return (
      <nav className="sidebar is-collapsed" aria-label="Conversations">
        <button type="button" className="icon-btn" onClick={p.onToggleCollapsed} aria-label="Expand sidebar">
          <Icon d={PANEL} />
        </button>
        <button type="button" className="icon-btn" onClick={p.onNew} aria-label="New conversation">
          <Icon d={PLUS} />
        </button>
      </nav>
    );
  }

  const groupNames = [...new Set(p.sessions.map((s) => s.group).filter((g): g is string => Boolean(g)))].sort();
  const target = p.sessions.find((s) => s.sid === p.confirmDeleteSid);

  const menu = (s: SessionSummary) => {
    const close = () => {
      p.onMenu(null);
      document.getElementById(`more-${s.sid}`)?.focus(); // back to the "⋯" that opened it
    };
    if (p.menuMode === "group") {
      const typed = p.groupText.trim();
      return (
        <div className="sidebar-menu" role="menu" aria-label={`Move ${s.title} to a group`} onKeyDown={(e) => menuKeys(e, close)}>
          {groupNames
            .filter((g) => g !== s.group)
            .map((g) => (
              <button key={g} type="button" role="menuitem" onClick={() => p.onAction({ kind: "group", sid: s.sid, group: g })}>
                {g}
              </button>
            ))}
          <input
            className="sidebar-menu-input"
            aria-label="New group name"
            placeholder="New group…"
            value={p.groupText}
            maxLength={40}
            onChange={(e) => p.onGroupText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && typed) {
                e.preventDefault();
                p.onAction({ kind: "group", sid: s.sid, group: typed });
              }
            }}
          />
          {typed && (
            <button type="button" role="menuitem" onClick={() => p.onAction({ kind: "group", sid: s.sid, group: typed })}>
              {`Move to “${typed}”`}
            </button>
          )}
          {s.group && (
            <button type="button" role="menuitem" onClick={() => p.onAction({ kind: "group", sid: s.sid, group: null })}>
              Remove from group
            </button>
          )}
          <button type="button" role="menuitem" onClick={() => p.onMenuMode("main")}>
            Back
          </button>
        </div>
      );
    }
    return (
      <div className="sidebar-menu" role="menu" aria-label={`Actions for ${s.title}`} onKeyDown={(e) => menuKeys(e, close)}>
        {p.archivedView ? (
          <button type="button" role="menuitem" onClick={() => p.onAction({ kind: "archive", sid: s.sid, archived: false })}>
            Restore
          </button>
        ) : (
          <>
            <button type="button" role="menuitem" onClick={() => p.onStartRename(s.sid)}>
              Rename
            </button>
            <button type="button" role="menuitem" onClick={() => p.onAction({ kind: "pin", sid: s.sid, pinned: !s.pinned })}>
              {s.pinned ? "Unpin" : "Pin"}
            </button>
            <button type="button" role="menuitem" onClick={() => p.onMenuMode("group")}>
              Move to group
            </button>
            <button type="button" role="menuitem" onClick={() => p.onAction({ kind: "archive", sid: s.sid, archived: true })}>
              Archive
            </button>
          </>
        )}
        <button type="button" role="menuitem" className="is-danger" onClick={() => p.onAskDelete(s.sid)}>
          Delete
        </button>
      </div>
    );
  };

  const item = (s: SessionSummary) => (
    <li key={s.sid} className="sidebar-item">
      {p.renamingSid === s.sid ? (
        <input
          className="sidebar-rename"
          aria-label="Rename conversation"
          value={p.renameText}
          maxLength={80}
          autoFocus
          onChange={(e) => p.onRenameText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              p.onAction({ kind: "rename", sid: s.sid, title: p.renameText });
            } else if (e.key === "Escape") {
              e.preventDefault();
              p.onStartRename(null);
            }
          }}
        />
      ) : (
        <button
          type="button"
          className="conversation"
          aria-current={s.sid === p.currentSid ? "true" : undefined}
          onClick={() => p.onSelect(s)}
        >
          <span className="conversation-title">{s.title}</span>
          <span className="conversation-meta">
            {STATUS[s.status] && <span className={`conversation-status is-${s.status}`}>{STATUS[s.status]}</span>}
            {s.waiting > 0 && <span className="conversation-waiting">{s.waiting} waiting</span>}
          </span>
        </button>
      )}
      <button
        type="button"
        id={`more-${s.sid}`}
        className="icon-btn item-more"
        aria-label={`More actions for ${s.title}`}
        aria-haspopup="menu"
        aria-expanded={p.menuFor === s.sid}
        onClick={() => p.onMenu(p.menuFor === s.sid ? null : s.sid)}
      >
        <span aria-hidden="true">⋯</span>
      </button>
      {p.menuFor === s.sid && menu(s)}
    </li>
  );

  const { pinned, groups, recent } = sections(p.sessions);

  return (
    <nav className="sidebar" aria-label="Conversations">
      <div className="sidebar-top">
        <button type="button" className="btn-plain new-conversation" onClick={p.onNew}>
          + New conversation
        </button>
        <button type="button" className="icon-btn sidebar-collapse" onClick={p.onToggleCollapsed} aria-label="Collapse sidebar">
          <Icon d={PANEL} />
        </button>
      </div>
      <input
        id="sidebar-search"
        className="sidebar-search"
        type="search"
        aria-label="Search conversations"
        placeholder="Search  (Ctrl+K)"
        value={p.query}
        onChange={(e) => p.onQuery(e.target.value)}
      />

      {p.archivedView ? (
        <>
          <button type="button" className="sidebar-link" onClick={p.onToggleArchived}>
            ‹ Back to conversations
          </button>
          <p className="sidebar-heading">Archived</p>
          {p.sessions.length ? <ul>{p.sessions.map(item)}</ul> : <p className="muted sidebar-empty">Nothing archived.</p>}
        </>
      ) : (
        <>
          {pinned.length > 0 && (
            <>
              <p className="sidebar-heading">Pinned</p>
              <ul>{pinned.map(item)}</ul>
            </>
          )}
          {groups.map((g) => {
            const open = !p.closedGroups.includes(g.name);
            return (
              <div key={g.name} className="sidebar-group">
                <button type="button" className="sidebar-heading sidebar-group-toggle" aria-expanded={open} onClick={() => p.onToggleGroup(g.name)}>
                  <span aria-hidden="true">{open ? "▾" : "▸"}</span> {g.name} <span className="muted">({g.items.length})</span>
                </button>
                {open && <ul>{g.items.map(item)}</ul>}
              </div>
            );
          })}
          {recent.length > 0 && (
            <>
              <p className="sidebar-heading">Recent</p>
              <ul>{recent.map(item)}</ul>
            </>
          )}
          {p.sessions.length === 0 && (
            <p className="muted sidebar-empty">{p.query ? "No conversation matches." : "No conversations yet."}</p>
          )}
          <button type="button" className="sidebar-link" onClick={p.onToggleArchived}>
            Archived
          </button>
        </>
      )}

      {target && (
        <div className="sidebar-confirm" role="alertdialog" aria-labelledby="delete-title" aria-describedby="delete-text">
          <p id="delete-title" className="sidebar-confirm-title">
            Delete “{target.title}”?
          </p>
          <p id="delete-text">
            It's hidden from your list and you can't open it again. The office's audit record is kept: every action the
            assistant took stays in the log.
          </p>
          <div className="draft-actions">
            <button type="button" className="btn-danger" onClick={() => p.onAction({ kind: "delete", sid: target.sid })}>
              Delete conversation
            </button>
            <button type="button" className="btn-plain" onClick={() => p.onAskDelete(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </nav>
  );
}

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
