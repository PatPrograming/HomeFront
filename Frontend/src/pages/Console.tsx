import { useEffect, useRef, useState } from "react";
import Layout from "../components/Layout";
import AutonomyPanel from "../components/AutonomyPanel";
import ChatPanel from "../components/ChatPanel";
import ConversationList from "../components/ConversationList";
import EventFeed, { FeedSummary } from "../components/EventFeed";
import IdentityCard from "../components/IdentityCard";
import IdentityLine from "../components/IdentityLine";
import ReviewPanel from "../components/ReviewPanel";
import WaitingList, { type WaitingScope } from "../components/WaitingList";
import { api, type Draft, type SessionCard, type SessionSummary } from "../lib/api";
import { connectionMessage } from "../lib/errors";
import { useOpenDraft } from "../lib/useOpenDraft";
import { useSession } from "../lib/AuthProvider";
import { sessionEvents } from "../lib/events";
import { isAdmin } from "../lib/roles";
import { needsNewSession, sendInSession } from "../lib/chatSession";
import { useChat, type Chat } from "../lib/useChat";
import { useEventFeed } from "../lib/useEventFeed";
import { useReview } from "../lib/useReview";
import { useSessionRevoked } from "../lib/useSessionRevoked";
import { useSessions } from "../lib/useSessions";
import { sortDrafts, type WaitingOrder } from "../lib/waiting";
import "./Console.css";

// The open conversation's card, per browser tab (sessionStorage), so a reload keeps it and two tabs can show two
// different conversations.
const CARD_KEY = "mandate.card";
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
  const conversations = useSessions();
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
      showCard(next.card);
      conversations.reload();
    }
  }
  function showCard(next: SessionCard | null) {
    if (next) sessionStorage.setItem(CARD_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(CARD_KEY);
    setCard(next);
    setConvOpen(false);
    reviewing.close();
  }
  // Switching conversations shows that session's chat, drafts and identity line (the listing carries its card).
  function selectConversation(sid: string) {
    const picked = conversations.sessions.find((c) => c.sid === sid);
    if (!picked) return;
    const { title: _title, status: _status, waiting: _waiting, createdAt: _createdAt, ...next } = picked;
    showCard(next);
  }
  // A revoked session stays on screen as proof; this lets the staffer leave it and start over (the broker
  // still refuses if they are offboarded).
  function newConversation() {
    showCard(null);
  }
  const chatWithSession: Chat = { ...chat, send };
  const locked = new Set(review.categories.filter((c) => c.locked).map((c) => c.category));
  const admin = isAdmin(session);
  const [railOpen, setRailOpen] = useState(false);
  const [convOpen, setConvOpen] = useState(false); // the conversation menu under 1280 px
  const [waitingOpen, setWaitingOpen] = useState(false);
  const [scope, setScope] = useState<WaitingScope>("this");
  const [order, setOrder] = useState<WaitingOrder>("rank");
  const mine = review.drafts.filter((d) => d.sid === card?.sid);
  const titles = Object.fromEntries(conversations.sessions.map((c: SessionSummary) => [c.sid, c.title]));
  const reviewing = useOpenDraft();
  const openDraft = review.drafts.find((d) => d.id === reviewing.openId) ?? null; // a draft decided elsewhere closes
  // Drafts decided in this tab keep their card in the chat, marked Sent or Rejected.
  const [decided, setDecided] = useState<Record<string, Draft>>({});
  const chatDrafts = [...mine, ...Object.values(decided).filter((d) => d.sid === card?.sid && !mine.some((p) => p.id === d.id))].sort(
    (a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id), // a decided card keeps its place
  );
  const openerRef = useRef<HTMLElement | null>(null);

  function openReview(id: string) {
    if (!reviewing.openId) openerRef.current = document.activeElement as HTMLElement | null;
    setRailOpen(false);
    setWaitingOpen(false);
    reviewing.open(id);
  }
  function openWaiting() {
    setScope(mine.length ? "this" : "all");
    setWaitingOpen(true);
  }
  // "Next waiting" walks the list as the staffer last saw it (scope and order).
  const queue = sortDrafts(scope === "this" && mine.length ? mine : review.drafts, order);
  const position = openDraft ? queue.findIndex((d) => d.id === openDraft.id) : -1;
  function closeReview() {
    reviewing.close();
    // Back to the card or badge that opened it; once decided, that button is gone, so to the message box.
    // Wait for the re-render that removes it.
    const opener = openerRef.current;
    window.setTimeout(() => (opener?.isConnected ? opener : document.getElementById("chat-input"))?.focus(), 0);
  }
  const [grants, setGrants] = useState(loadGrants);

  function granted(sid: string, category: string) {
    const next = { ...grants, [sid]: [...new Set([...(grants[sid] ?? []), category])] };
    sessionStorage.setItem(GRANTS_KEY, JSON.stringify(next));
    setGrants(next);
  }

  // The review panel and the settings drawer close on Escape, like other dialogs (the composer uses Escape first,
  // to stop waiting).
  const reviewOpen = openDraft !== null;
  useEffect(() => {
    if (!railOpen && !reviewOpen && !waitingOpen && !convOpen) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      setRailOpen(false);
      setWaitingOpen(false);
      setConvOpen(false);
      if (reviewOpen) closeReview();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <Layout>
      <div className="mandate-console assistant-page">
        <div className={`assistant-shell${openDraft ? " has-review" : ""}`}>
          {/* The staffer's conversations: a left rail from 1280 px, a menu ("Conversations") below. */}
          <aside id="conversations" className={`conv-rail${convOpen ? " is-open" : ""}`}>
            <div className="rail-close-row conv-close-row">
              <button type="button" className="icon-btn" onClick={() => setConvOpen(false)} aria-label="Close conversations">
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            <ConversationList
              sessions={conversations.sessions}
              currentSid={card?.sid ?? null}
              onSelect={selectConversation}
              onNew={newConversation}
            />
          </aside>
          {convOpen && <div className="rail-backdrop conv-backdrop" onClick={() => setConvOpen(false)} aria-hidden="true" />}

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
              <div className="head-actions">
                <button
                  type="button"
                  className="conv-toggle"
                  aria-expanded={convOpen}
                  aria-controls="conversations"
                  onClick={() => setConvOpen(true)}
                >
                  Conversations
                </button>
                {review.drafts.length > 0 && (
                  <div className="waiting-anchor">
                    <button
                      type="button"
                      className="waiting-badge"
                      aria-expanded={waitingOpen}
                      aria-controls="waiting-popover"
                      onClick={() => (waitingOpen ? setWaitingOpen(false) : openWaiting())}
                    >
                      <span className="waiting-dot" aria-hidden="true" />
                      {review.drafts.length} waiting
                      <span className="sr-only">for your review: choose one</span>
                    </button>
                    {/* A popover under the badge on a wide screen, a sheet on a phone. */}
                    {waitingOpen && (
                      <div id="waiting-popover" className="waiting-popover" role="dialog" aria-label="Waiting for your review">
                        <div className="waiting-popover-head">
                          <h2>Waiting for your review</h2>
                          <button type="button" className="icon-btn" onClick={() => setWaitingOpen(false)} aria-label="Close the waiting list">
                            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                              <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                            </svg>
                          </button>
                        </div>
                        <WaitingList
                          drafts={review.drafts}
                          currentSid={card?.sid ?? null}
                          scope={scope}
                          order={order}
                          titles={titles}
                          onScope={setScope}
                          onOrder={setOrder}
                          onOpen={openReview}
                        />
                      </div>
                    )}
                  </div>
                )}
                <button
                  type="button"
                  className="rail-toggle"
                  aria-expanded={railOpen}
                  aria-controls="assistant-rail"
                  onClick={() => setRailOpen(true)}
                >
                  Sending rules
                </button>
              </div>
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
              review={{ drafts: chatDrafts, onOpen: openReview }}
            />
          </div>

          {/* The draft opened from the chat: a side panel on a wide screen, a full-screen sheet on a phone. */}
          {openDraft && (
            <div className="review-slot">
              <ReviewPanel
                draft={openDraft}
                locked={locked.has(openDraft.category)}
                onDone={review.reload}
                onClose={closeReview}
                onGrant={granted}
                onDecided={(status) => setDecided({ ...decided, [openDraft.id]: { ...openDraft, status } })}
                position={position >= 0 ? { index: position + 1, total: queue.length } : undefined}
                onNext={() => queue.length > 1 && openReview(queue[(position + 1) % queue.length].id)}
              />
            </div>
          )}

          {/* What it may send alone: a right rail on a wide screen (hidden while a draft is open), a drawer on smaller ones. */}
          <aside
            id="assistant-rail"
            className={`rail${railOpen ? " is-open" : ""}${openDraft ? " is-covered" : ""}`}
            aria-label="Sending rules"
          >
            <div className="rail-close-row">
              <button type="button" className="icon-btn" onClick={() => setRailOpen(false)} aria-label="Close sending rules">
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </button>
            </div>
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
                  {feed.error ? connectionMessage(feed.error, true) : feed.lastPollAt ? "Live, every second" : "Connecting…"}
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
