import { useEffect, useRef, useState } from "react";
import Layout from "../components/Layout";
import AgentAccess from "../components/AgentAccess";
import AutonomyPanel from "../components/AutonomyPanel";
import ChatPanel from "../components/ChatPanel";
import EventFeed, { FeedSummary } from "../components/EventFeed";
import IdentityCard from "../components/IdentityCard";
import IdentityLine from "../components/IdentityLine";
import ReviewPanel from "../components/ReviewPanel";
import Sidebar from "../components/Sidebar";
import WaitingList, { type WaitingScope } from "../components/WaitingList";
import { api, type ChatMessage, type Draft, type SessionCard, type SessionSummary } from "../lib/api";
import { connectionMessage } from "../lib/errors";
import { useOpenDraft } from "../lib/useOpenDraft";
import { useSession } from "../lib/AuthProvider";
import { sessionEvents } from "../lib/events";
import { isAdmin } from "../lib/roles";
import { needsNewSession, SessionRevokedError, sendInSession, type StartedCard } from "../lib/chatSession";
import { isStopPhrase } from "../lib/stopTask";
import { useChat, type Chat } from "../lib/useChat";
import { useEventFeed } from "../lib/useEventFeed";
import { useReview } from "../lib/useReview";
import { useSessionRevoked } from "../lib/useSessionRevoked";
import { useSessions } from "../lib/useSessions";
import { useEarlier } from "../lib/useEarlier";
import { useToggle } from "../lib/useToggle";
import { sortDrafts, type WaitingOrder } from "../lib/waiting";
import "./Console.css";

// The open conversation's card, per browser tab (sessionStorage), so a reload keeps it and two tabs can show two
// different conversations.
const CARD_KEY = "mandate.card";
const REVOKED = "session revoked / delegator inactive";
const STOPPED_KEY = "mandate.stopped"; // sid of the task the staffer stopped in this tab
const GRANTS_KEY = "mandate.grants"; // { [sid]: categories } granted with "Approve and allow for this session"
// A session lasts an hour; the next message then starts a new one. So the conversation doesn't lose what came
// before, this tab keeps the earlier sessions' messages (they can't change once a session has ended) under the
// new sid: { [sid]: { sids: earlier sids, messages } }.
const HISTORY_KEY = "mandate.history";

interface Earlier {
  sids: string[];
  messages: ChatMessage[];
}

function loadHistory(): Record<string, Earlier> {
  try {
    return JSON.parse(sessionStorage.getItem(HISTORY_KEY) ?? "{}") as Record<string, Earlier>;
  } catch {
    return {};
  }
}

// The API has no "list grants" route, so the console remembers the grants this tab made, per session.
function loadGrants(): Record<string, string[]> {
  try {
    return JSON.parse(sessionStorage.getItem(GRANTS_KEY) ?? "{}") as Record<string, string[]>;
  } catch {
    return {};
  }
}

// A listing row carries the session's card fields; keep only those.
function toCard(row: SessionSummary): SessionCard {
  const { title: _t, status: _s, waiting: _w, createdAt: _c, pinned: _p, group: _g, archived: _a, earlier: _e, ...card } = row;
  return card;
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
  const conversations = useSessions();
  const [history, setHistory] = useState(loadHistory);
  // The conversation's earlier sessions: from the server's listing (Lane E's `earlier`), else this tab's history.
  const row = card ? conversations.sessions.find((s) => s.sid === card.sid) : undefined;
  const fromServer = row?.earlier?.length ? [...row.earlier].reverse() : null; // oldest first
  const local = card ? history[card.sid] : undefined;
  const serverMessages = useEarlier(fromServer ?? []);
  const earlierMessages = fromServer ? serverMessages : (local?.messages ?? []);
  const chain = [...(fromServer ?? local?.sids ?? []), ...(card ? [card.sid] : [])]; // this conversation's sessions, oldest first
  // Lane E: a continued session starts without the one-time permissions of the last one; say so once, quietly.
  const notice = (card as StartedCard | null)?.grantsReset ? "New session: one-time permissions were reset" : null;
  // The task this tab stopped, kept across a reload so the page still says who stopped it.
  const [stoppedSid, setStoppedSid] = useState<string | null>(() => sessionStorage.getItem(STOPPED_KEY));
  const stopped = card !== null && stoppedSid === card.sid;
  // The approved plan's tools per session: once set, the activity list shows only those.
  const [planTools, setPlanTools] = useState<Record<string, SessionCard["planTools"]>>({});
  const polledRevoked = useSessionRevoked(card?.sid ?? null, 2000, (s) =>
    setPlanTools((prev) => (JSON.stringify(prev[s.sid]) === JSON.stringify(s.planTools ?? null) ? prev : { ...prev, [s.sid]: s.planTools ?? null })),
  );
  // The broker refused to continue this conversation because its session was revoked (code `revoked`).
  const [refusedSid, setRefusedSid] = useState<string | null>(null);
  const revoked = stopped || polledRevoked || (card !== null && refusedSid === card.sid) || sessionEvents(feed.events, card?.sid ?? null).some((e) => e.reason === REVOKED);
  const events = feed.events.filter((e) => chain.includes(e.sid)); // the admin Audit log shows every session
  const chat = useChat(card?.sid ?? null);
  const review = useReview();
  const reloadReview = review.reload;

  // A new chat message usually means the agent just drafted something: show it without waiting for the next poll.
  useEffect(() => {
    reloadReview();
  }, [chat.messages.length, reloadReview]);

  // No "Start" button: the first message (or the first after the session expired) starts one.
  // Errors, such as the broker refusing an offboarded staffer, show under the chat box.
  // A typed stop phrase never reaches the agent: the console revokes the session itself.
  async function send(text: string) {
    if (card && isStopPhrase(text)) return stopTask();
    if (card && !needsNewSession(card, Date.now())) return chat.send(text);
    // The session's hour is up: keep what was said so far above the new session's messages.
    const kept: Earlier | null = card ? { sids: chain, messages: shownMessages } : null;
    let next: Awaited<ReturnType<typeof sendInSession>>;
    try {
      next = await sendInSession(card, text, Date.now(), {
        startSession: api.startSession,
        sendMessage: api.sendMessage,
        findContinuation: async (oldSid) => {
          const newest = (await api.listSessions()).sessions.find((s) => s.earlier?.includes(oldSid));
          return newest ? toCard(newest) : null;
        },
      });
    } catch (e) {
      if (e instanceof SessionRevokedError) {
        setRefusedSid(e.sid); // show the Revoked banner and "Start a new conversation", not a send error
        return;
      }
      throw e;
    }
    if (next.card.sid !== card?.sid) {
      if (kept?.messages.length) {
        const all = { ...history, [next.card.sid]: kept };
        sessionStorage.setItem(HISTORY_KEY, JSON.stringify(all));
        setHistory(all);
      }
      showCard(next.card);
      conversations.reload();
    }
  }
  // Stop this task (button or typed phrase): the staffer's own login revokes the session, so the agent's next
  // tool call is denied and its process is ended. Errors show under the chat box.
  async function stopTask() {
    if (!card) return;
    await api.revokeSession(card.sid);
    sessionStorage.setItem(STOPPED_KEY, card.sid);
    setStoppedSid(card.sid);
  }
  function showCard(next: SessionCard | null) {
    if (next) sessionStorage.setItem(CARD_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(CARD_KEY);
    setCard(next);
    setConvOpen(false);
    reviewing.close();
  }
  // Switching conversations shows that session's chat, drafts and identity line (the listing carries its card).
  function selectConversation(picked: SessionSummary) {
    showCard(toCard(picked));
  }
  // A revoked session stays on screen as proof; this lets the staffer leave it and start over (the broker
  // still refuses if they are offboarded).
  function openConversations() {
    if (window.matchMedia("(max-width: 1279px)").matches) setConvOpen(true);
  }
  function newConversation() {
    showCard(null);
  }
  const shownMessages = [...earlierMessages, ...chat.messages];
  const chatWithSession: Chat = { ...chat, messages: shownMessages, send };
  const locked = new Set(review.categories.filter((c) => c.locked).map((c) => c.category));
  const admin = isAdmin(session);
  const [railOpen, setRailOpen] = useToggle(false); // the Sending rules popover
  const [convOpen, setConvOpen] = useState(false); // the conversation menu under 1280 px
  const [waitingOpen, setWaitingOpen] = useState(false);
  const [scope, setScope] = useState<WaitingScope>("this");
  const [order, setOrder] = useState<WaitingOrder>("rank");
  const mine = review.drafts.filter((d) => d.sid !== undefined && chain.includes(d.sid));
  const titles = Object.fromEntries(conversations.sessions.map((c: SessionSummary) => [c.sid, c.title]));
  const reviewing = useOpenDraft();
  const openDraft = review.drafts.find((d) => d.id === reviewing.openId) ?? null; // a draft decided elsewhere closes
  // Drafts decided in this tab keep their card in the chat, marked Sent or Rejected.
  const [decided, setDecidedState] = useState<Record<string, Draft>>(() => {
    try {
      return JSON.parse(localStorage.getItem("homefront.decided") ?? "{}") as Record<string, Draft>;
    } catch {
      return {};
    }
  });
  // Kept across reloads, so a sent X post keeps its Open on X link.
  const setDecided = (next: Record<string, Draft>) => {
    setDecidedState(next);
    try {
      localStorage.setItem("homefront.decided", JSON.stringify(next));
    } catch {
      /* storage blocked: the card still shows until reload */
    }
  };
  const chatDrafts = [...mine, ...Object.values(decided).filter((d) => d.sid !== undefined && chain.includes(d.sid) && !mine.some((p) => p.id === d.id))].sort(
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

  // The Sending rules popover takes the focus when it opens (its close button), for keyboard and screen readers.
  useEffect(() => {
    if (railOpen) document.querySelector<HTMLElement>('#sending-rules [aria-label="Close sending rules"]')?.focus();
  }, [railOpen]);

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
          {/* The staffer's conversations: a left sidebar from 1280 px (collapsible), a drawer ("Conversations") below. */}
          <aside id="conversations" className={`conv-rail${convOpen ? " is-open" : ""}`}>
            <div className="conv-close-row">
              <button type="button" className="icon-btn" onClick={() => setConvOpen(false)} aria-label="Close conversations">
                <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            <Sidebar
              currentSid={card?.sid ?? null}
              forceExpanded={convOpen}
              onSelect={selectConversation}
              onNew={newConversation}
              onChanged={conversations.reload}
              onRemoved={newConversation}
              onRequestOpen={openConversations}
            />
          </aside>
          {convOpen && <div className="rail-backdrop conv-backdrop" onClick={() => setConvOpen(false)} aria-hidden="true" />}

          <div className="assistant-main">
            <header className="assistant-head">
              <div>
                <h1>Your assistant</h1>
                {card ? (
                  <IdentityLine card={card} revoked={revoked} task={row?.title} stopped={stopped} />
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
                {/* What it may send without asking: one click away on every screen, so the chat keeps the width. */}
                <div className="rules-anchor">
                  <button
                    type="button"
                    className="rules-toggle"
                    aria-expanded={railOpen}
                    aria-controls="sending-rules"
                    onClick={() => setRailOpen(!railOpen)}
                  >
                    Sending rules
                  </button>
                  {railOpen && (
                    <div id="sending-rules" className="rules-popover" role="dialog" aria-label="Sending rules">
                      <div className="waiting-popover-head">
                        <h2>Sending rules</h2>
                        <button type="button" className="icon-btn" onClick={() => setRailOpen(false)} aria-label="Close sending rules">
                          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                            <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                      </div>
                      <AutonomyPanel categories={review.categories} onChange={review.reload} />
                    </div>
                  )}
                </div>
              </div>
            </header>

            {notice && !revoked && (
              <p className="session-notice" role="status">
                {notice}
              </p>
            )}

            {card && revoked && !stopped && (
              <p className="revoked-banner" role="status">
                <span>This conversation's session was revoked. The assistant can do nothing more in it.</span>
                <button type="button" className="btn-primary" onClick={newConversation}>
                  Start a new conversation
                </button>
              </p>
            )}

            <ChatPanel
              renewedAfter={earlierMessages[earlierMessages.length - 1]?.id ?? null}
              chat={chatWithSession}
              sid={card?.sid ?? null}
              disabled={revoked}
              stop={card ? { stopped, onStop: stopTask, onNewConversation: newConversation } : undefined}
              activity={{ card: card && { ...card, planTools: planTools[card.sid] ?? card.planTools }, events, categories: review.categories, grants: card ? grants[card.sid] ?? [] : [], admin }}
              review={{ drafts: chatDrafts, onOpen: openReview, onChanged: review.reload }}
              onNewConversation={newConversation}
              retryAgent={card ? () => api.retryAgent(card.sid) : undefined}
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

        </div>

        {/* Admins only: the raw identity card and every ALLOW / DENY with its Cedar policy and ATLAS ids. */}
        {admin && (
          <section className="tech" aria-labelledby="tech-title">
            <div className="tech-head">
              <h2 id="tech-title">Technical details</h2>
              <p className="muted">Visible to admins only. Staff see the plain-words summary in the chat.</p>
            </div>
            <AgentAccess />
            {card && <IdentityCard card={card} revoked={revoked} stopped={stopped} />}
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
