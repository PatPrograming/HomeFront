import { useState } from "react";
import Layout from "../components/Layout.jsx";
import EventFeed, { FeedSummary } from "../components/EventFeed.jsx";
import IdentityCard from "../components/IdentityCard.jsx";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/AuthProvider.jsx";
import { useEventFeed } from "../lib/useEventFeed.js";
import "./Console.css";

// Staff view: start the assistant (demo step 1), then watch every ALLOW / DENY it gets (steps 2 and 4).
export default function Console() {
  const { session } = useAuth();
  const [card, setCard] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const feed = useEventFeed({ limit: 100 });

  async function start() {
    setBusy(true);
    setError(null);
    try {
      setCard(await api.startSession());
    } catch (e) {
      setError(e.message || String(e));
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
          <IdentityCard card={card} />
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
