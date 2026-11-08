import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import AutonomyPanel from "../components/AutonomyPanel";
import ChatPanel from "../components/ChatPanel";
import EventFeed, { FeedSummary } from "../components/EventFeed";
import IdentityCard from "../components/IdentityCard";
import IdentityLine from "../components/IdentityLine";
import ReviewQueue from "../components/ReviewQueue";
import { api, type SessionCard } from "../lib/api";
import { useSession } from "../lib/AuthProvider";
import { sessionEvents } from "../lib/events";
import { isAdmin } from "../lib/roles";
import { needsNewSession, sendInSession } from "../lib/chatSession";
import { useChat, type Chat } from "../lib/useChat";
import { useEventFeed } from "../lib/useEventFeed";
import { useReview } from "../lib/useReview";
import { useSessionRevoked } from "../lib/useSessionRevoked";
import "./Console.css";

const CARD_KEY = "mandate.card"; // the running session's card, so a reload keeps the sid on screen
const REVOKED = "session revoked / delegator inactive";
const GRANTS_KEY = "mandate.grants"; // { [sid]: categories } granted with "Approve and allow for this session"

// The API has no "list grants" route, so the console remembers the grants this tab made, per session.
function loadGrants(): Record<string, string[]> {
  try {
    return JSON.parse(sessionStorage.getItem(GRANTS_KEY) ?? "{}") as Record<string, string[]>;
  } catch {
    return {};
  }
}

// Only the same signed-in user gets their saved card back.
function loadCard(username: string): SessionCard | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(CARD_KEY) ?? "null") as SessionCard | null;
    return saved?.actingFor === username ? saved : null;
  } catch {
    return null;
  }
}

// Staff view (admins also get Technical details below): chat with the assistant (the first message starts the session: demo step 1), review what the gateway held for you, decide what
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
  // A revoked session stays on screen as proof; this lets the staffer leave it and start over (the broker
  // still refuses if they are offboarded).
  function newConversation() {
    sessionStorage.removeItem(CARD_KEY);
    setCard(null);
  }
  const chatWithSession: Chat = { ...chat, send };
  const locked = new Set(review.categories.filter((c) => c.locked).map((c) => c.category));
  const admin = isAdmin(session);
  const [railOpen, setRailOpen] = useState(false);
  const [grants, setGrants] = useState(loadGrants);

  function granted(sid: string, category: string) {
    const next = { ...grants, [sid]: [...new Set([...(grants[sid] ?? []), category])] };
    sessionStorage.setItem(GRANTS_KEY, JSON.stringify(next));
    setGrants(next);
  }

  // The review drawer (under 1024 px) closes on Escape, like other dialogs.
  useEffect(() => {
    if (!railOpen) return;
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setRailOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [railOpen]);

  return (
    <Layout>
      <div className="mandate-console assistant-page">
        <div className="assistant-shell">
          <div className="assistant-main">
            <header className="assistant-head">
              <div>
                <h1>Your assistant</h1>
                {card ? (
                  <IdentityLine card={card} revoked={revoked} />
                ) : (
                  <p className="identity-line">It acts for you, with only the tools your office allows.</p>
                )}
              </div>
              <button
                type="button"
                className="rail-toggle"
                aria-expanded={railOpen}
                aria-controls="assistant-rail"
                onClick={() => setRailOpen(true)}
              >
                Review
                {review.drafts.length > 0 && <span className="count-badge">{review.drafts.length}</span>}
              </button>
            </header>

            {card && revoked && (
              <p className="revoked-banner" role="status">
                <span>This conversation's session was revoked. The assistant can do nothing more in it.</span>
                <button type="button" className="btn-primary" onClick={newConversation}>
                  Start a new conversation
                </button>
              </p>
            )}

            <ChatPanel
              chat={chatWithSession}
              sid={card?.sid ?? null}
              disabled={revoked}
              activity={{ card, events, categories: review.categories, grants: card ? grants[card.sid] ?? [] : [], admin }}
            />
          </div>

          {/* Right rail on a wide screen; a drawer opened by "Review" on smaller ones. */}
          <aside id="assistant-rail" className={`rail${railOpen ? " is-open" : ""}`} aria-label="Review and permissions">
            <div className="rail-close-row">
              <button type="button" className="icon-btn" onClick={() => setRailOpen(false)} aria-label="Close review panel">
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            <ReviewQueue drafts={review.drafts} locked={locked} onDone={review.reload} onGrant={granted} />
            <AutonomyPanel categories={review.categories} onChange={review.reload} />
          </aside>
          {railOpen && <div className="rail-backdrop" onClick={() => setRailOpen(false)} aria-hidden="true" />}
        </div>

        {/* Admins only: the raw identity card and every ALLOW / DENY with its Cedar policy and ATLAS ids. */}
        {admin && (
          <section className="tech" aria-labelledby="tech-title">
            <div className="tech-head">
              <h2 id="tech-title">Technical details</h2>
              <p className="muted">Visible to admins only. Staff see the plain-words summary in the chat.</p>
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
          </section>
        )}
      </div>
    </Layout>
  );
}
