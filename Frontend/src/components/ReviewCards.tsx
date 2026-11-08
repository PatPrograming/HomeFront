import type { ChatMessage, Draft } from "../lib/api";

// Which reply each draft belongs under: the first assistant reply at or after the draft was made. A draft newer
// than every reply (the server's clock a little ahead of the chat's) goes under the latest reply, so no draft is
// left without a card. With no reply yet, none is placed; the "N waiting" badge still opens them.
export function draftsByReply(messages: ChatMessage[], drafts: Draft[]): Map<string, Draft[]> {
  const replies = messages.filter((m) => m.role === "assistant");
  const placed = new Map<string, Draft[]>();
  if (!replies.length) return placed;
  for (const d of drafts) {
    const t = Date.parse(d.createdAt);
    const reply = replies.find((m) => Date.parse(m.ts) >= t) ?? replies[replies.length - 1];
    placed.set(reply.id, [...(placed.get(reply.id) ?? []), d]);
  }
  return placed;
}

const what = (d: Draft) => (d.tool === "email_send" ? `Reply to ${d.args.to}` : `Post on @${d.args.account}`);
const DONE: Record<Draft["status"], string> = { pending: "", sent: "Sent", rejected: "Rejected" };

// One small card per draft under the assistant's reply, like an attachment in other chat apps. "Review" opens that
// draft in the side panel. No hooks, so a test can call it and press its buttons.
export default function ReviewCards({ drafts, onOpen }: { drafts: Draft[]; onOpen: (id: string) => void }) {
  return (
    <ul className="review-cards">
      {drafts.map((d) => (
        <li key={d.id} className={`review-card${d.status === "pending" ? "" : " is-done"}`}>
          <span className="review-card-icon" aria-hidden="true">
            {d.tool === "email_send" ? "@" : "#"}
          </span>
          <span className="review-card-text">
            <span className="review-card-title" title={what(d)}>
              {what(d)}
            </span>
            <span className="review-card-meta">
              {d.categoryLabel} · {d.status === "pending" ? "needs your review" : DONE[d.status]}
            </span>
          </span>
          {d.status === "pending" && (
            <button type="button" className="btn-plain review-card-open" onClick={() => onOpen(d.id)} aria-label={`Review: ${what(d)}`}>
              Review
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
