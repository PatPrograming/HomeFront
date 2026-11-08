import type { SessionCard } from "../lib/api";
import { expiryMs } from "./IdentityCard";

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// The staff view of the agent's identity: one quiet line instead of the admin's full card (no session id, no
// countdown). The Revoked state shows here for everyone.
export default function IdentityLine({ card, revoked }: { card: SessionCard; revoked: boolean }) {
  const ends = expiryMs(card.expiresAt);
  return (
    <p className={`identity-line${revoked ? " is-revoked" : ""}`}>
      {revoked ? (
        <strong>Revoked: access was removed by the Chief of Staff</strong>
      ) : (
        <>
          {card.roleLabel && (
            <>
              <strong className="identity-role">
                {capitalize(card.actingFor)} · {card.roleLabel}
              </strong>
              <span className="identity-sep" aria-hidden="true">·</span>
            </>
          )}
          <span>
            <span className={`identity-dot${ends <= Date.now() ? " is-off" : ""}`} aria-hidden="true" />
            <code>{card.agent}</code> acts for you, office {card.office}
          </span>
          <span className="identity-sep" aria-hidden="true">·</span>
          <span>{ends > Date.now() ? `Access ends at ${clock(ends)}` : "Access ended. Your next message starts a new session."}</span>
        </>
      )}
    </p>
  );
}
