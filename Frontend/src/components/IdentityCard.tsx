import { useEffect, useState } from "react";
import { TOOL_NAMES } from "../../../packages/mandate-contracts/ts/index";
import type { SessionCard } from "../lib/api";

// expiresAt may arrive as an ISO string or as epoch seconds/milliseconds; normalise to milliseconds.
export function expiryMs(expiresAt: string | number): number {
  if (typeof expiresAt === "number") return expiresAt < 1e12 ? expiresAt * 1000 : expiresAt;
  return Date.parse(expiresAt);
}

// The current time, ticking every second until `paused` (a revoked session's countdown stops).
function useNow(paused: boolean, intervalMs = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (paused) return;
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [paused, intervalMs]);
  return now;
}

function countdown(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// The agent's delegated identity, as returned by POST /api/v1/sessions. The countdown is the session's
// one-hour lifetime (mandate tokens inside it last 15 minutes each).
// `revoked` is true once GET /sessions/{sid} says revoked, or the feed shows a "session revoked" DENY.
export default function IdentityCard({ card, revoked }: { card: SessionCard; revoked: boolean }) {
  const now = useNow(revoked);
  const [copied, setCopied] = useState(false);
  const left = expiryMs(card.expiresAt) - now;
  const blocked = TOOL_NAMES.filter((t) => !card.allowedTools.includes(t));

  async function copySid() {
    try {
      await navigator.clipboard.writeText(card.sid);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className={`panel identity${revoked ? " is-revoked" : ""}`} aria-labelledby="identity-title">
      <div className="panel-head">
        <h2 id="identity-title">
          <code>{card.agent}</code> is acting for <strong>{card.actingFor}</strong>
        </h2>
        <p className={`expiry ${left <= 0 || revoked ? "deny-text" : ""}`} aria-live={revoked ? "polite" : "off"}>
          {revoked ? (
            <strong>Revoked: access was removed by the Chief of Staff</strong>
          ) : left > 0 ? (
            <>
              Session expires in <strong className="figure">{countdown(left)}</strong>
            </>
          ) : (
            <strong>Session expired</strong>
          )}
        </p>
      </div>
      <dl className="facts">
        <div>
          <dt>Office</dt>
          <dd>{card.office}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{card.mode}</dd>
        </div>
        <div className="sid">
          <dt>{revoked ? "Session id (revoked, no longer valid)" : "Session id (give this to the agent)"}</dt>
          <dd>
            <code className={revoked ? "sid-revoked" : undefined}>{card.sid}</code>
            <button type="button" className="icon-btn" onClick={copySid} aria-label="Copy session id">
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <rect x="8" y="8" width="12" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
                <path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" fill="none" stroke="currentColor" strokeWidth="2" />
              </svg>
            </button>
            <span role="status" className="copied">{copied ? "Copied" : ""}</span>
          </dd>
        </div>
        <div className="tools">
          <dt>Allowed tools</dt>
          <dd>
            {card.allowedTools.map((t) => (
              <code key={t} className="tool-ok">{t}</code>
            ))}
          </dd>
        </div>
        {blocked.length > 0 && (
          <div className="tools">
            <dt>Never allowed</dt>
            <dd>
              {blocked.map((t) => (
                <code key={t} className="tool-no">{t}</code>
              ))}
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}
