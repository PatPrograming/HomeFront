import { useState } from "react";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import Layout from "../components/Layout";
import EventFeed, { FeedSummary } from "../components/EventFeed";
import { STAFFERS, type Staffer } from "../config";
import { api, type OffboardResult } from "../lib/api";
import { useEventFeed } from "../lib/useEventFeed";
import "./Console.css";

const REVOKED = "session revoked / delegator inactive";

// Staffers to show: the known list plus anyone who appears in the audit log.
function staffersFrom(events: DecisionEvent[]): Staffer[] {
  const known = new Map(STAFFERS.map((s) => [s.id, s]));
  for (const e of events) {
    if (!known.has(e.actingFor)) known.set(e.actingFor, { id: e.actingFor, name: e.actingFor, office: e.office });
  }
  return [...known.values()];
}

interface StafferRowProps {
  staffer: Staffer;
  events: DecisionEvent[];
  result: OffboardResult | undefined;
  onOffboard: (id: string) => Promise<void>;
}

function StafferRow({ staffer, events, result, onOffboard }: StafferRowProps) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mine = events.filter((e) => e.actingFor === staffer.id);
  // Only the offboard answer says a staffer is inactive (the API has no "list staff" route yet). A revoked
  // last call is shown as a fact, not as the staffer's status: it can come from an old, replaced session.
  const offboarded = result !== undefined;
  const lastRevoked = mine[0]?.reason === REVOKED;

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onOffboard(staffer.id);
      setConfirming(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="staffer">
      <div className="staffer-who">
        <strong>{staffer.name}</strong>
        <span className="muted">
          {" "}
          <code>{staffer.id}</code> · office {staffer.office}
        </span>
        <p className="staffer-activity">
          {mine.length ? `${mine.length} agent calls, last at ${new Date(mine[0].ts).toLocaleTimeString([], { hour12: false })}` : "No agent calls yet"}
          {result ? (
            <span className="deny-text">
              {" · "}Offboarded, {result.revokedSessions} session{result.revokedSessions === 1 ? "" : "s"} revoked
            </span>
          ) : (
            lastRevoked && <span className="deny-text">{" · "}Last call denied: session revoked</span>
          )}
        </p>
        {error && (
          <p className="alert" role="alert">
            Offboard failed: {error}
          </p>
        )}
      </div>
      <div className="staffer-actions">
        {confirming ? (
          <>
            <p className="confirm-text">
              Offboard {staffer.name}? Their assistant&apos;s next call will be denied.
            </p>
            <button type="button" className="btn-danger" onClick={confirm} disabled={busy}>
              {busy ? "Offboarding…" : `Yes, offboard ${staffer.name}`}
            </button>
            <button type="button" className="btn-plain" onClick={() => setConfirming(false)} disabled={busy}>
              Cancel
            </button>
          </>
        ) : (
          <button type="button" className="btn-danger-outline" onClick={() => setConfirming(true)}>
            {offboarded ? "Offboard again" : "Offboard"}
          </button>
        )}
      </div>
    </li>
  );
}

// Admin view (Cognito group "admin" only): cut a staffer's access (demo step 3) and read the full audit log.
export default function Admin() {
  const feed = useEventFeed();
  const [results, setResults] = useState<Record<string, OffboardResult>>({});

  async function offboard(id: string) {
    const res = await api.offboard(id);
    setResults((r) => ({ ...r, [id]: res }));
  }

  return (
    <Layout>
      <div className="mandate-console">
        <header className="title-row">
          <div>
            <h1>Chief of Staff</h1>
            <p className="lede">
              Offboarding a staffer marks them inactive and revokes their assistant&apos;s sessions. The gateway
              checks this on every tool call, so the very next call is denied.
            </p>
          </div>
        </header>

        <section className="panel" aria-labelledby="staff-title">
          <div className="panel-head">
            <h2 id="staff-title">Staff with an assistant</h2>
          </div>
          <ul className="staffers">
            {staffersFrom(feed.events).map((s) => (
              <StafferRow key={s.id} staffer={s} events={feed.events} result={results[s.id]} onOffboard={offboard} />
            ))}
          </ul>
        </section>

        <section className="panel" aria-labelledby="audit-title">
          <div className="panel-head">
            <h2 id="audit-title">Audit log</h2>
            <p className={`live ${feed.error ? "deny-text" : ""}`} role="status">
              {feed.error ? `Reconnecting: ${feed.error.message}` : "Full history, live"}
            </p>
          </div>
          <FeedSummary events={feed.events} />
          <EventFeed events={feed.events} emptyText="The audit log is empty." />
        </section>
      </div>
    </Layout>
  );
}
