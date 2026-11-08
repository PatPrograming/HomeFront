import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import type { AutonomyCategory, SessionCard } from "../lib/api";
import { describeEvent, permissionLines } from "../lib/agentActivity";

const count = (n: number) => (n === 0 ? "no actions" : n === 1 ? "1 action" : `${n} actions`);

// The "Working… / Done" line under a turn of the chat, like a tool-use disclosure in other chat apps. Opening it
// shows, in plain words, which agent acts for you, what it may do this session and what it did in this turn.
// Admins also get a link to the full audit log (policy and ATLAS ids live there, not here).
export default function AgentActivity({
  card,
  events,
  categories,
  working,
  admin,
}: {
  card: SessionCard;
  events: DecisionEvent[];
  categories: AutonomyCategory[];
  working: boolean;
  admin: boolean;
}) {
  const { allowed, never } = permissionLines(card, categories);
  const blocked = events.filter((e) => e.decision === "DENY").length;

  return (
    <details className={`activity${working ? " is-working" : ""}`}>
      <summary>
        <span className={working ? "activity-dot" : "activity-check"} aria-hidden="true" />
        <span role={working ? "status" : undefined}>{working ? "Working…" : `Done · ${count(events.length)}`}</span>
        {blocked > 0 && <span className="activity-blocked">{blocked} blocked</span>}
        <svg className="chevron" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="sr-only">: show what the assistant did and may do</span>
      </summary>
      <div className="activity-body">
        <p className="activity-who">
          <strong>{card.agent}</strong> · Acting for you ({card.actingFor}) · Office {card.office}
        </p>

        <p className="activity-label">What it did</p>
        {events.length ? (
          <ul className="activity-list">
            {events.map((e) => {
              const line = describeEvent(e);
              return (
                <li key={e.id} className={line.blocked ? "is-blocked" : undefined}>
                  {line.text}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted">{working ? "Nothing yet. Each step shows here as it happens." : "It answered without using any tools."}</p>
        )}

        <p className="activity-label">What it may do this session</p>
        <ul className="activity-list is-allowed">
          {allowed.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
        {never.length > 0 && (
          <>
            <p className="activity-label">Never allowed</p>
            <ul className="activity-list is-never">
              {never.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </>
        )}

        {admin && (
          <a className="activity-tech" href="/admin">
            Technical details
          </a>
        )}
      </div>
    </details>
  );
}
