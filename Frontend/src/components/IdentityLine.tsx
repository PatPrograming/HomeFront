import type { SessionCard } from "../lib/api";
import { expiryMs, revokedText } from "./IdentityCard";

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

// The staff view of the agent's identity: one quiet line instead of the admin's full card (no session id, no
// countdown). The Revoked state shows here for everyone.
export default function IdentityLine({ card, revoked, stopped }: { card: SessionCard; revoked: boolean; stopped?: boolean }) {
  const ends = expiryMs(card.expiresAt);
  return (
    <p className={`identity-line${revoked ? " is-revoked" : ""}`}>
      {revoked ? (
        <strong>{revokedText(stopped)}</strong>
      ) : (
        <>
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
