import type { Draft } from "../lib/api";
import { sortDrafts, type WaitingOrder } from "../lib/waiting";

export type WaitingScope = "this" | "all";

export interface WaitingListProps {
  drafts: Draft[]; // every waiting draft of the staffer
  currentSid: string | null;
  scope: WaitingScope;
  order: WaitingOrder;
  titles: Record<string, string>; // sid -> conversation title, to name drafts from other conversations
  onScope: (scope: WaitingScope) => void;
  onOrder: (order: WaitingOrder) => void;
  onOpen: (id: string) => void;
}

const URGENCY = { high: "High", medium: "Medium", low: "Low" } as const;
const who = (d: Draft) => (d.tool === "email_send" ? d.args.to : `@${d.args.account}`);
const what = (d: Draft) => (d.tool === "email_send" ? d.args.subject : d.args.text);

// Every draft waiting for review, to pick any one: this conversation's or all of them, ranked by the server's
// urgency (with its one-line reason: category, why, age) or by time. No hooks, so a test can call it and press its buttons.
export default function WaitingList(props: WaitingListProps) {
  const { drafts, currentSid, scope, order, titles, onScope, onOrder, onOpen } = props;
  const mine = drafts.filter((d) => d.sid === currentSid);
  const shown = sortDrafts(scope === "this" ? mine : drafts, order);

  return (
    <div className="waiting-list">
      <div className="waiting-controls">
        <div className="seg" role="group" aria-label="Which drafts">
          <button type="button" aria-pressed={scope === "this"} onClick={() => onScope("this")}>
            This conversation ({mine.length})
          </button>
          <button type="button" aria-pressed={scope === "all"} onClick={() => onScope("all")}>
            All conversations ({drafts.length})
          </button>
        </div>
        <div className="seg" role="group" aria-label="Order">
          <button type="button" aria-pressed={order === "rank"} onClick={() => onOrder("rank")}>
            Most urgent
          </button>
          <button type="button" aria-pressed={order === "time"} onClick={() => onOrder("time")}>
            By time
          </button>
        </div>
      </div>

      {shown.length === 0 ? (
        <p className="muted waiting-empty">Nothing waiting here.{scope === "this" && drafts.length > 0 ? " Other conversations have drafts." : ""}</p>
      ) : (
        <ul className="waiting-rows">
          {shown.map((d) => (
            <li key={d.id}>
              <button type="button" className="waiting-row" onClick={() => onOpen(d.id)}>
                <span className={`urgency urgency-${d.urgency}`}>{URGENCY[d.urgency]}</span>
                <span className="waiting-main">
                  <span className="waiting-who">{who(d)}</span>
                  <span className="waiting-what">{what(d)}</span>
                  {/* The server's reason already names the category and the age. */}
                  <span className="waiting-reason">{d.reason}</span>
                  {d.sid !== currentSid && d.sid && titles[d.sid] && <span className="waiting-meta">In "{titles[d.sid]}"</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
