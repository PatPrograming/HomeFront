// The ALLOW / DENY feed: one row per tool call the gateway decided, newest first.
// Used live on the staff console and as the full audit log on the admin page.
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";

const time = (ts: string) => new Date(ts).toLocaleTimeString([], { hour12: false });

function Codes({ ids, label }: { ids: string[] | undefined; label: string }) {
  if (!ids?.length) return <span className="muted" aria-label={`No ${label}`}>—</span>;
  return (
    <>
      {ids.map((id) => (
        <code key={id}>{id}</code>
      ))}
    </>
  );
}

export function FeedSummary({ events }: { events: DecisionEvent[] }) {
  const denied = events.filter((e) => e.decision === "DENY").length;
  const flagged = events.filter((e) => e.guardrail?.flagged).length;
  return (
    <p className="feed-summary">
      <strong>{events.length}</strong> calls · <span className="ok-text">{events.length - denied} allowed</span> ·{" "}
      <span className="deny-text">{denied} denied</span> · {flagged} flagged by the guardrail
    </p>
  );
}

export default function EventFeed({ events, emptyText }: { events: DecisionEvent[]; emptyText: string }) {
  if (events.length === 0) return <p className="feed-empty">{emptyText}</p>;
  return (
    <div className="feed" role="table" aria-label="Tool call decisions, newest first">
      <div className="feed-row feed-head" role="row">
        {["Time", "Decision", "Tool", "Resource", "Reason", "Cedar policy", "MITRE ATLAS", "Guardrail"].map((h) => (
          <span key={h} role="columnheader">
            {h}
          </span>
        ))}
      </div>
      {events.map((e) => (
        <div key={e.id} role="row" className={`feed-row ${e.decision === "DENY" ? "is-deny" : "is-allow"}`}>
          <span role="cell" className="c-time">
            <time dateTime={e.ts}>{time(e.ts)}</time>
          </span>
          <span role="cell" className="c-decision">
            <span className={`decision ${e.decision === "DENY" ? "deny" : "allow"}`}>{e.decision}</span>
          </span>
          <span role="cell" className="c-tool">
            <code>{e.tool}</code>
          </span>
          <span role="cell" className="c-resource">{e.resource ?? <span className="muted">—</span>}</span>
          <span role="cell" className="c-reason">
            {e.reason}
            <span className="c-who"> · {e.agent} for {e.actingFor}</span>
          </span>
          <span role="cell" className="c-policy">
            <span className="cell-label">Policy </span>
            <Codes ids={e.policyIds} label="policy" />
          </span>
          <span role="cell" className="c-atlas">
            <span className="cell-label">ATLAS </span>
            <Codes ids={e.atlas} label="ATLAS technique" />
          </span>
          <span role="cell" className="c-guard">
            {e.guardrail?.flagged ? (
              <span className="flag">Flagged: {e.guardrail.categories.join(", ") || "yes"}</span>
            ) : (
              <span className="muted">Clean</span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
