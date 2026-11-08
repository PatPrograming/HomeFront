import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Draft } from "../lib/api";
import ReviewCards, { draftsForTurn } from "./ReviewCards";

const draft = (id: string, createdAt: string, to: string): Draft => ({
  id, status: "pending", tool: "email_send", category: "case_status", categoryLabel: "Case status updates",
  args: { to, subject: "Re: x", body: "y" }, createdAt, sid: "sid-1",
});
const drafts = [draft("d1", "2026-11-08T08:00:02Z", "marcus.bell@example.com"), draft("d2", "2026-11-08T08:00:09Z", "rosa@example.com")];

// All the <button> elements in a rendered element tree (ReviewCards has no hooks, so calling it is rendering it).
function buttons(node: ReactNode): ReactElement<{ onClick: () => void; "aria-label"?: string }>[] {
  if (!isValidElement(node)) return Array.isArray(node) ? node.flatMap(buttons) : [];
  const el = node as ReactElement<{ children?: ReactNode; onClick: () => void }>;
  return [...(el.type === "button" ? [el] : []), ...buttons(el.props.children)];
}

describe("ReviewCards: review from the chat", () => {
  it("takes the drafts a turn produced", () => {
    expect(draftsForTurn(drafts, "2026-11-08T08:00:00Z", "2026-11-08T08:00:05Z").map((d) => d.id)).toEqual(["d1"]);
  });

  it("renders one card per draft, saying who it is for and that it needs review", () => {
    const html = renderToStaticMarkup(<ReviewCards drafts={drafts} onOpen={() => {}} />);
    expect(html).toContain("Reply to marcus.bell@example.com");
    expect(html).toContain("Reply to rosa@example.com");
    expect(html.match(/needs your review/g)).toHaveLength(2);
  });

  it("clicking a card's Review opens that draft", () => {
    const onOpen = vi.fn();
    const [, second] = buttons(ReviewCards({ drafts, onOpen }));
    second.props.onClick();
    expect(onOpen).toHaveBeenCalledWith("d2");
  });

  it("shows a decided draft as done, without a Review button", () => {
    const html = renderToStaticMarkup(<ReviewCards drafts={[{ ...drafts[0], status: "sent" }]} onOpen={() => {}} />);
    expect(html).toContain("Sent");
    expect(html).not.toContain("needs your review");
  });
});
