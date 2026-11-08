import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Draft } from "../lib/api";
import WaitingList, { type WaitingListProps } from "./WaitingList";

const draft = (id: string, sid: string, score: number, urgency: Draft["urgency"], to: string, createdAt: string): Draft => ({
  id, sid, score, urgency, reason: `Case status updates · ${urgency} because · waiting 40 min`, status: "pending", tool: "email_send", category: "case_status",
  categoryLabel: "Case status updates", args: { to, subject: `Subject ${id}`, body: "" }, createdAt,
});
const drafts = [
  draft("low", "sid-a", 20, "low", "low@example.com", "2026-11-08T08:00:00Z"),
  draft("high", "sid-a", 75, "high", "high@example.com", "2026-11-08T08:20:00Z"),
  draft("other", "sid-b", 50, "medium", "other@example.com", "2026-11-08T08:10:00Z"),
];
const props = (over: Partial<WaitingListProps> = {}): WaitingListProps => ({
  drafts, currentSid: "sid-a", scope: "this", order: "rank", titles: { "sid-b": "Post the town hall" },
  onScope: () => {}, onOrder: () => {}, onOpen: () => {}, ...over,
});

function buttons(node: ReactNode): ReactElement<{ onClick: () => void; children?: ReactNode }>[] {
  if (Array.isArray(node)) return node.flatMap(buttons);
  if (!isValidElement(node)) return [];
  const el = node as ReactElement<{ children?: ReactNode; onClick: () => void }>;
  return [...(el.type === "button" ? [el] : []), ...buttons(el.props.children)];
}
const text = (node: ReactNode): string => renderToStaticMarkup(<>{node}</>).replace(/<[^>]+>/g, "");

describe("WaitingList: pick which draft to review", () => {
  it("lists this conversation's drafts, most urgent first, with urgency, recipient, subject and the server's reason", () => {
    const html = renderToStaticMarkup(<WaitingList {...props()} />);
    expect(html.indexOf("high@example.com")).toBeLessThan(html.indexOf("low@example.com"));
    expect(html).not.toContain("other@example.com");
    for (const shown of ["High", "Low", "Subject high", "Case status updates · high because · waiting 40 min"]) expect(html).toContain(shown);
  });

  it("orders by time when asked", () => {
    const html = renderToStaticMarkup(<WaitingList {...props({ order: "time" })} />);
    expect(html.indexOf("low@example.com")).toBeLessThan(html.indexOf("high@example.com"));
  });

  it("'All conversations' shows every waiting draft, naming the other conversation", () => {
    const html = renderToStaticMarkup(<WaitingList {...props({ scope: "all" })} />);
    expect(html).toContain("other@example.com");
    expect(html).toContain("Post the town hall");
    expect(html.indexOf("high@example.com")).toBeLessThan(html.indexOf("other@example.com"));
  });

  it("clicking a row opens that draft; the toggles change order and scope", () => {
    const onOpen = vi.fn();
    const onOrder = vi.fn();
    const onScope = vi.fn();
    const all = buttons(WaitingList(props({ onOpen, onOrder, onScope })));
    all.find((b) => text(b.props.children).includes("low@example.com"))!.props.onClick();
    expect(onOpen).toHaveBeenCalledWith("low");
    all.find((b) => text(b.props.children) === "By time")!.props.onClick();
    expect(onOrder).toHaveBeenCalledWith("time");
    all.find((b) => text(b.props.children).startsWith("All conversations"))!.props.onClick();
    expect(onScope).toHaveBeenCalledWith("all");
  });
});
