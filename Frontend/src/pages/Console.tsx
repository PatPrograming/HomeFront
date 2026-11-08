import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import AutonomyPanel from "../components/AutonomyPanel";
import ChatPanel from "../components/ChatPanel";
import EventFeed, { FeedSummary } from "../components/EventFeed";
import IdentityCard from "../components/IdentityCard";
import ReviewQueue from "../components/ReviewQueue";
import { api, type SessionCard } from "../lib/api";
import { useSession } from "../lib/AuthProvider";
import { sessionEvents } from "../lib/events";
import { needsNewSession, sendInSession } from "../lib/chatSession";
import { useChat, type Chat } from "../lib/useChat";
import { useEventFeed } from "../lib/useEventFeed";
import { useReview } from "../lib/useReview";
import { useSessionRevoked } from "../lib/useSessionRevoked";
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

// Staff view: chat with the assistant (the first message starts the session: demo step 1), review what the gateway held for you, decide what
// it may send alone, and watch every ALLOW / DENY this session gets (steps 2 and 4).
export default function Console() {
  const session = useSession();
  const [card, setCard] = useState<SessionCard | null>(() => loadCard(session.username));
  const feed = useEventFeed({ limit: 100 });
  const events = sessionEvents(feed.events, card?.sid ?? null); // the admin Audit log shows every session
  const revoked = useSessionRevoked(card?.sid ?? null) || events.some((e) => e.reason === REVOKED);
  const chat = useChat(card?.sid ?? null);
  const review = useReview();
  const reloadReview = review.reload;

  // A new chat message usually means the agent just drafted something: show it without waiting for the next poll.
  useEffect(() => {
    reloadReview();
  }, [chat.messages.length, reloadReview]);

  // No "Start" button: the first message (or the first after the session expired) starts one.
  // Errors, such as the broker refusing an offboarded staffer, show under the chat box.
  async function send(text: string) {
    if (card && !needsNewSession(card, Date.now())) return chat.send(text);
    const next = await sendInSession(card, text, Date.now(), api);
    if (next.card.sid !== card?.sid) {
      sessionStorage.setItem(CARD_KEY, JSON.stringify(next.card));
      setCard(next.card);
    }
  }
  const chatWithSession: Chat = { ...chat, send };
  const locked = new Set(review.categories.filter((c) => c.locked).map((c) => c.category));

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
        </header>

        <div className="assistant-grid">
          <ChatPanel chat={chatWithSession} sid={card?.sid ?? null} disabled={revoked} />
          <div className="assistant-side">
            <ReviewQueue drafts={review.drafts} locked={locked} onDone={review.reload} />
            <AutonomyPanel categories={review.categories} onChange={review.reload} />
          </div>
        </div>

        {card && <IdentityCard card={card} revoked={revoked} />}

        <section className="panel" aria-labelledby="feed-title">
          <div className="panel-head">
            <h2 id="feed-title">Live decisions</h2>
            <p className={`live ${feed.error ? "deny-text" : ""}`} role="status">
              {feed.error ? `Reconnecting: ${feed.error.message}` : feed.lastPollAt ? "Live, every second" : "Connecting…"}
            </p>
          </div>
          <FeedSummary events={events} />
          <EventFeed
            events={events}
            emptyText={
              card
                ? "No tool calls yet. They appear here within a second."
                : "No session yet. Send the assistant a message to start one; its tool calls appear here."
            }
          />
        </section>
      </div>
    </Layout>
  );
}
