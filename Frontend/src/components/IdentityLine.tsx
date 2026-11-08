import type { SessionCard } from "../lib/api";

// The staff view of the agent's identity: one quiet line, named by the conversation's task. No raw agent id and no expiry clock (both are in the
// admin's Technical details); the console still renews an expired session on the next message. The Revoked state
// shows here for everyone.
export default function IdentityLine({ card, revoked, task }: { card: SessionCard; revoked: boolean; task?: string }) {
  return (
    <p className={`identity-line${revoked ? " is-revoked" : ""}`}>
      {revoked ? (
        <strong>Revoked: access was removed by the Chief of Staff</strong>
      ) : (
        <>
          <span>
            <span className="identity-dot" aria-hidden="true" />
            Your assistant acts for you, office {card.office}
          </span>
          {task && <span className="identity-task">Working on: {task}</span>}
        </>
      )}
    </p>
  );
}
