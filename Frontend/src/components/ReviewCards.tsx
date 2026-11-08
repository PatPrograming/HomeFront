import type { Draft } from "../lib/api";

// The drafts one turn of the chat produced: created after the previous message and up to the reply.
export function draftsForTurn(drafts: Draft[], fromTs: string, toTs: string): Draft[] {
  const from = Date.parse(fromTs);
  const to = Date.parse(toTs);
  return drafts.filter((d) => {
    const t = Date.parse(d.createdAt);
    return t > from && t <= to;
  });
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
