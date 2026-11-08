import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { SessionSummary } from "../lib/api";
import { SidebarView, type SidebarViewProps } from "./SidebarView";

const s = (sid: string, title: string, over: Partial<SessionSummary> = {}): SessionSummary => ({
  sid, title, status: "active", waiting: 0, pinned: false, group: null, archived: false,
  agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [],
  createdAt: "2026-11-08T08:00:00Z", expiresAt: "2026-11-08T09:00:00Z", ...over,
});
const sessions = [
  s("p", "Town hall post", { pinned: true }),
  s("g", "Monroe pothole", { group: "Roads", waiting: 2 }),
  s("r", "Inbox check", { status: "revoked" }),
];
const props = (over: Partial<SidebarViewProps> = {}): SidebarViewProps => ({
  sessions, currentSid: "g", query: "", archivedView: false, collapsed: false, menuFor: null, menuMode: "main",
  renamingSid: null, renameText: "", groupText: "", confirmDeleteSid: null, closedGroups: [],
  onQuery: () => {}, onToggleCollapsed: () => {}, onToggleArchived: () => {}, onNew: () => {}, onSelect: () => {},
  onMenu: () => {}, onMenuMode: () => {}, onStartRename: () => {}, onRenameText: () => {}, onGroupText: () => {},
  onToggleGroup: () => {}, onAskDelete: () => {}, onAction: () => {}, ...over,
});

type Props = { onClick?: () => void; onChange?: (e: { target: { value: string } }) => void; onKeyDown?: (e: { key: string; preventDefault: () => void }) => void; children?: ReactNode; "aria-label"?: string };
function all(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(all);
  if (!isValidElement(node)) return [];
  const el = node as ReactElement<Props>;
  return [el, ...all(el.props.children)];
}
const text = (node: ReactNode) => renderToStaticMarkup(<>{node}</>).replace(/<[^>]+>/g, "");
const button = (tree: ReactNode, label: string) =>
  all(tree).find((e) => e.type === "button" && (e.props["aria-label"] === label || text(e.props.children).trim() === label))!;
const render = (over: Partial<SidebarViewProps> = {}) => renderToStaticMarkup(<SidebarView {...props(over)} />);

describe("SidebarView: a Claude-style conversation sidebar", () => {
  it("has New conversation, a labelled search box, and Pinned, group and Recent sections", () => {
    const html = render();
    expect(html).toContain("New conversation");
    expect(html).toMatch(/<input[^>]*aria-label="Search conversations"/);
    const order = ["Pinned", "Town hall post", "Roads", "Monroe pothole", "Recent", "Inbox check"].map((t) => html.indexOf(t));
    expect(order.every((i, k) => i >= 0 && (k === 0 || i > order[k - 1]))).toBe(true);
    expect(html).toContain("2 waiting");
    expect(html).toContain("Revoked");
    expect(html).toMatch(/aria-current="true"[^>]*>[\s\S]*?Monroe pothole/);
  });

  it("typing in search reports the query", () => {
    const onQuery = vi.fn();
    const input = all(SidebarView(props({ onQuery }))).find((e) => e.type === "input")!;
    input.props.onChange!({ target: { value: "pot" } });
    expect(onQuery).toHaveBeenCalledWith("pot");
  });

  it("a group can be folded", () => {
    expect(render({ closedGroups: ["Roads"] })).not.toContain("Monroe pothole");
    const onToggleGroup = vi.fn();
    all(SidebarView(props({ onToggleGroup }))).find((e) => e.type === "button" && text(e.props.children).includes("Roads"))!.props.onClick!();
    expect(onToggleGroup).toHaveBeenCalledWith("Roads");
  });

  it("each item has a labelled ⋯ menu with Rename, Pin, Move to group, Archive and Delete", () => {
    expect(render()).toContain('aria-label="More actions for Monroe pothole"');
    const html = render({ menuFor: "g" });
    expect(html).toContain('role="menu"');
    for (const item of ["Rename", "Pin", "Move to group", "Archive", "Delete"]) expect(html).toMatch(new RegExp(`role="menuitem"[^>]*>${item}<`));
    expect(render({ menuFor: "p" })).toMatch(/role="menuitem"[^>]*>Unpin</);
  });

  it("the menu's Delete asks first; it never deletes on its own", () => {
    const onAction = vi.fn();
    const onAskDelete = vi.fn();
    button(SidebarView(props({ menuFor: "g", onAction, onAskDelete })), "Delete").props.onClick!();
    expect(onAskDelete).toHaveBeenCalledWith("g");
    expect(onAction).not.toHaveBeenCalled();
  });

  it("menu items send the matching action", () => {
    const onAction = vi.fn();
    const tree = SidebarView(props({ menuFor: "g", onAction }));
    button(tree, "Pin").props.onClick!();
    button(tree, "Archive").props.onClick!();
    expect(onAction.mock.calls.map((c) => c[0])).toEqual([
      { kind: "pin", sid: "g", pinned: true },
      { kind: "archive", sid: "g", archived: true },
    ]);
  });

  it("Move to group lists groups, takes a new name, or removes it from its group", () => {
    const onAction = vi.fn();
    const tree = SidebarView(props({ menuFor: "g", menuMode: "group", groupText: "Veterans", onAction }));
    const html = renderToStaticMarkup(<>{tree}</>);
    expect(html).toContain("Roads");
    button(tree, "Move to “Veterans”").props.onClick!();
    button(tree, "Remove from group").props.onClick!();
    expect(onAction.mock.calls.map((c) => c[0])).toEqual([
      { kind: "group", sid: "g", group: "Veterans" },
      { kind: "group", sid: "g", group: null },
    ]);
  });

  it("Rename edits the title in place: Enter saves, Escape cancels", () => {
    const onAction = vi.fn();
    const onStartRename = vi.fn();
    const tree = SidebarView(props({ renamingSid: "g", renameText: "Pothole on Monroe", onAction, onStartRename }));
    const input = all(tree).find((e) => e.type === "input" && e.props["aria-label"] === "Rename conversation")!;
    input.props.onKeyDown!({ key: "Enter", preventDefault: () => {} });
    expect(onAction).toHaveBeenCalledWith({ kind: "rename", sid: "g", title: "Pothole on Monroe" });
    input.props.onKeyDown!({ key: "Escape", preventDefault: () => {} });
    expect(onStartRename).toHaveBeenCalledWith(null);
  });

  it("Delete asks first, saying the conversation is hidden but the office's audit record is kept", () => {
    const onAction = vi.fn();
    const tree = SidebarView(props({ confirmDeleteSid: "g", onAction }));
    const html = renderToStaticMarkup(<>{tree}</>);
    expect(html).toContain('role="alertdialog"');
    expect(html).toMatch(/hidden from your list/);
    expect(html).toMatch(/audit record is kept/);
    button(tree, "Delete conversation").props.onClick!();
    expect(onAction).toHaveBeenCalledWith({ kind: "delete", sid: "g" });
  });

  it("collapsed, it keeps only expand and new-conversation buttons", () => {
    const html = render({ collapsed: true });
    expect(html).toContain('aria-label="Expand sidebar"');
    expect(html).toContain('aria-label="New conversation"');
    expect(html).not.toContain("Monroe pothole");
    expect(render()).toContain('aria-label="Collapse sidebar"');
  });

  it("the Archived view offers Restore", () => {
    const archived = [s("a", "Old newsletter", { archived: true })];
    const onAction = vi.fn();
    const tree = SidebarView(props({ sessions: archived, archivedView: true, menuFor: "a", onAction }));
    expect(renderToStaticMarkup(<>{tree}</>)).toContain("Back to conversations");
    button(tree, "Restore").props.onClick!();
    expect(onAction).toHaveBeenCalledWith({ kind: "archive", sid: "a", archived: false });
  });
});
