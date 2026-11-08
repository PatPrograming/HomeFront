import { useState } from "react";
import Layout from "../components/Layout";
import EventFeed, { FeedSummary } from "../components/EventFeed";
import IdentityCard from "../components/IdentityCard";
import { api, type SessionCard } from "../lib/api";
import { useSession } from "../lib/AuthProvider";
import { useEventFeed } from "../lib/useEventFeed";
import "./Console.css";

const CARD_KEY = "mandate.card"; // the running session's card, so a reload keeps the sid on screen
const REVOKED = "session revoked / delegator inactive";

// Only the same signed-in user gets their saved card back.
function loadCard(username: string): SessionCard | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(CARD_KEY) ?? "null") as SessionCard | null;
    return saved?.actingFor === username ? saved : null;
  } catch {
    return null;
  }
}

// Staff view: start the assistant (demo step 1), then watch every ALLOW / DENY it gets (steps 2 and 4).
export default function Console() {
  const session = useSession();
  const [card, setCard] = useState<SessionCard | null>(() => loadCard(session.username));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const feed = useEventFeed({ limit: 100 });
  const revoked = card !== null && feed.events.some((e) => e.sid === card.sid && e.reason === REVOKED);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const next = await api.startSession();
      sessionStorage.setItem(CARD_KEY, JSON.stringify(next));
      setCard(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Layout>
      <div className="mandate-console">
        <header className="title-row">
          <div>
            <h1>Your AI assistant</h1>
            <p className="lede">
              Signed in as <strong>{session.username}</strong>. The assistant acts for you, with only the tools
              your office allows, for 15 minutes at a time.
            </p>
          </div>
          <button type="button" className="btn-primary" onClick={start} disabled={busy}>
            {busy ? "Starting…" : card ? "Start a new session" : "Start assistant"}
          </button>
        </header>

        {error && (
          <p className="alert" role="alert">
            Could not start the assistant: {error}
          </p>
        )}

        {card ? (
          <IdentityCard card={card} revoked={revoked} />
        ) : (
          <section className="panel identity-empty">
            <h2>No assistant running</h2>
            <p>Start the assistant to get its identity card and the session id to hand to the agent.</p>
          </section>
        )}

        <section className="panel" aria-labelledby="feed-title">
          <div className="panel-head">
            <h2 id="feed-title">Live decisions</h2>
            <p className={`live ${feed.error ? "deny-text" : ""}`} role="status">
              {feed.error ? `Reconnecting: ${feed.error.message}` : feed.lastPollAt ? "Live, every second" : "Connecting…"}
            </p>
          </div>
          <FeedSummary events={feed.events} />
          <EventFeed events={feed.events} emptyText="No tool calls yet. They appear here within a second." />
        </section>
      </div>
    </Layout>
  );
}
