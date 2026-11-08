import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ChatMessage, Draft } from "../lib/api";
import ReviewCards, { draftsByReply } from "./ReviewCards";

vi.mock("../lib/api", () => ({ api: {} })); // the cards' edit and revise calls aren't made here

const draft = (id: string, createdAt: string, to: string): Draft => ({
  id, status: "pending", tool: "email_send", category: "case_status", categoryLabel: "Case status updates", urgency: "medium", score: 40, reason: "r",
  args: { to, subject: "Re: x", body: "y" }, createdAt, sid: "sid-1",
});
const drafts = [draft("d1", "2026-11-08T08:00:02Z", "marcus.bell@example.com"), draft("d2", "2026-11-08T08:00:09Z", "rosa@example.com")];

// Every element in a rendered tree (ReviewCards has no hooks, so calling it is rendering it).
function all(node: ReactNode): ReactElement[] {
  if (!isValidElement(node)) return Array.isArray(node) ? node.flatMap(all) : [];
  const el = node as ReactElement<{ children?: ReactNode }>;
  return [el, ...all(el.props.children)];
}

describe("ReviewCards: review from the chat", () => {
  it("puts each draft under the first reply at or after it, and a late one under the latest reply", () => {
    const replies: ChatMessage[] = [
      { id: "u1", ts: "2026-11-08T08:00:00Z", role: "user", text: "a" },
      { id: "a1", ts: "2026-11-08T08:00:05Z", role: "assistant", text: "b" },
      { id: "u2", ts: "2026-11-08T08:00:06Z", role: "user", text: "c" },
      { id: "a2", ts: "2026-11-08T08:00:08Z", role: "assistant", text: "d" },
    ];
    const late = draft("d3", "2026-11-08T08:00:30Z", "late@example.com"); // a server clock ahead of the chat's
    const placed = draftsByReply(replies, [...drafts, late]);
    expect(placed.get("a1")?.map((d) => d.id)).toEqual(["d1"]);
    expect(placed.get("a2")?.map((d) => d.id)).toEqual(["d2", "d3"]);
    expect(draftsByReply([], drafts).size).toBe(0); // no reply yet: the badge still reaches them
  });

  it("never puts a draft under an earlier turn's reply while its own turn is still running", () => {
    const running: ChatMessage[] = [
      { id: "u1", ts: "2026-11-08T08:00:00Z", role: "user", text: "check email" },
      { id: "a1", ts: "2026-11-08T08:00:05Z", role: "assistant", text: "done" },
      { id: "u2", ts: "2026-11-08T08:00:06Z", role: "user", text: "post about the pandemic" },
    ];
    const fresh = draft("d9", "2026-11-08T08:00:07Z", "x@example.com"); // made by the turn still running
    expect(draftsByReply(running, [fresh]).get("a1")).toBeUndefined();
  });

  it("renders one editable draft card per draft, saying who it is for and that it needs review", () => {
    const html = renderToStaticMarkup(<ReviewCards drafts={drafts} onOpen={() => {}} onChanged={async () => {}} />);
    expect(html).toContain("Reply to marcus.bell@example.com");
    expect(html).toContain("Reply to rosa@example.com");
    expect(html.match(/needs your review/g)).toHaveLength(2);
  });

  it("each card gets its own draft and the console's open and reload callbacks", () => {
    const onOpen = vi.fn();
    const onChanged = vi.fn(async () => {});
    const cards = all(ReviewCards({ drafts, onOpen, onChanged })).filter((e) => (e.props as { draft?: Draft }).draft);
    expect(cards.map((c) => (c.props as { draft: Draft }).draft.id)).toEqual(["d1", "d2"]);
    for (const c of cards) expect(c.props).toMatchObject({ onOpen, onChanged });
  });

  it("shows a decided draft as done, without a Review button", () => {
    const html = renderToStaticMarkup(<ReviewCards drafts={[{ ...drafts[0], status: "sent" }]} onOpen={() => {}} onChanged={async () => {}} />);
    expect(html).toContain("Sent");
    expect(html).not.toContain("needs your review");
    expect(html).not.toContain("Review and send");
  });
});
