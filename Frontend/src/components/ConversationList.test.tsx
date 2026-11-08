import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { SessionSummary } from "../lib/api";
import ConversationList from "./ConversationList";

const s = (sid: string, title: string, status: SessionSummary["status"], waiting: number): SessionSummary => ({
  sid, title, status, waiting, agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: ["inbox_read"],
  createdAt: "2026-11-08T08:00:00Z", expiresAt: "2026-11-08T09:00:00Z",
});
const sessions = [s("sid-a", "Check my email", "active", 3), s("sid-b", "Post the town hall", "revoked", 0)];

function buttons(node: ReactNode): ReactElement<{ onClick: () => void; children?: ReactNode }>[] {
  if (Array.isArray(node)) return node.flatMap(buttons);
  if (!isValidElement(node)) return [];
  const el = node as ReactElement<{ children?: ReactNode; onClick: () => void }>;
  return [...(el.type === "button" ? [el] : []), ...buttons(el.props.children)];
}

describe("ConversationList: several conversations", () => {
  it("shows each conversation's title, status and waiting count, and marks the open one", () => {
    const html = renderToStaticMarkup(<ConversationList sessions={sessions} currentSid="sid-a" onSelect={() => {}} onNew={() => {}} />);
    for (const shown of ["Check my email", "Post the town hall", "3 waiting", "Revoked", "New conversation"]) expect(html).toContain(shown);
    expect(html).toMatch(/aria-current="true"[^>]*>[\s\S]*?Check my email/);
  });

  it("New conversation and picking one call back", () => {
    const onSelect = vi.fn();
    const onNew = vi.fn();
    const [newBtn, , second] = buttons(ConversationList({ sessions, currentSid: "sid-a", onSelect, onNew }));
    newBtn.props.onClick();
    second.props.onClick();
    expect(onNew).toHaveBeenCalled();
    expect(onSelect).toHaveBeenCalledWith("sid-b");
  });
});
