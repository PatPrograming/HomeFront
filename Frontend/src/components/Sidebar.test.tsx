import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionSummary } from "../lib/api";
import type { SidebarViewProps } from "./SidebarView";

// The stateful Sidebar with its view captured: press what the view would offer and check what reaches the API,
// the console and the browser's storage. (No DOM here, so the view's props are the buttons.)
const box = new Map<string, string>();
vi.stubGlobal("localStorage", { getItem: (k: string) => box.get(k) ?? null, setItem: (k: string, v: string) => void box.set(k, v) });
vi.stubGlobal("document", { getElementById: () => null });

const api = { updateSession: vi.fn(async () => ({})), deleteSession: vi.fn(async () => ({})) };
const reload = vi.fn(async () => {});
let view: SidebarViewProps;
const s = (sid: string): SessionSummary => ({
  sid, title: sid, status: "active", waiting: 0, pinned: false, group: null, archived: false, agent: "agent-1",
  actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [], createdAt: "2026-11-08T08:00:00Z", expiresAt: "2026-11-08T09:00:00Z",
});

vi.mock("../lib/api", () => ({ api }));
vi.mock("../lib/useSessions", () => ({ useSessions: () => ({ sessions: [s("open"), s("other")], error: null, reload }) }));
vi.mock("./SidebarView", () => ({
  SidebarView: (props: SidebarViewProps) => {
    view = props;
    return null;
  },
}));
const { default: Sidebar } = await import("./Sidebar");

const callbacks = { onSelect: vi.fn(), onNew: vi.fn(), onChanged: vi.fn(), onRemoved: vi.fn(), onRequestOpen: vi.fn() };
const render = () => renderToStaticMarkup(<Sidebar currentSid="open" forceExpanded={false} {...callbacks} />);

beforeEach(() => {
  box.clear();
  vi.clearAllMocks();
});

describe("Sidebar: menu actions reach the server", () => {
  it("pin, group, rename and archive are PATCHes; the lists reload after", async () => {
    render();
    await view.onAction({ kind: "pin", sid: "other", pinned: true });
    await view.onAction({ kind: "group", sid: "other", group: "Roads" });
    await view.onAction({ kind: "rename", sid: "other", title: "Monroe pothole" });
    await view.onAction({ kind: "archive", sid: "other", archived: true });
    expect(api.updateSession.mock.calls).toEqual([
      ["other", { pinned: true }],
      ["other", { group: "Roads" }],
      ["other", { title: "Monroe pothole" }],
      ["other", { archived: true }],
    ]);
    expect(reload).toHaveBeenCalledTimes(4);
    expect(callbacks.onChanged).toHaveBeenCalledTimes(4);
    expect(callbacks.onRemoved).not.toHaveBeenCalled(); // "other" is not the open conversation
  });

  it("delete is a DELETE", async () => {
    render();
    await view.onAction({ kind: "delete", sid: "other" });
    expect(api.deleteSession).toHaveBeenCalledWith("other");
  });

  it.each([
    [{ kind: "archive", sid: "open", archived: true }],
    [{ kind: "delete", sid: "open" }],
  ] as const)("%o on the open conversation tells the console to leave it", async (action) => {
    render();
    await view.onAction(action);
    expect(callbacks.onRemoved).toHaveBeenCalledWith("open");
  });
});

describe("Sidebar: collapse is saved", () => {
  it("the toggle writes the choice for this browser", () => {
    render();
    view.onToggleCollapsed();
    expect(box.get("homefront.sidebar.collapsed")).toBe("1");
  });

  it("a saved collapse shows collapsed, except in the phone drawer", () => {
    box.set("homefront.sidebar.collapsed", "1");
    render();
    expect(view.collapsed).toBe(true);
    renderToStaticMarkup(<Sidebar currentSid="open" forceExpanded {...callbacks} />);
    expect(view.collapsed).toBe(false);
  });
});
