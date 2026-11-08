import { Fragment, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import type { AutonomyCategory, Draft, SessionCard } from "../lib/api";
import { turnEvents } from "../lib/agentActivity";
import { connectionMessage } from "../lib/errors";
import { attemptSend, retryThenResend, type SendFailure } from "../lib/sendFlow";
import { STOPPED_TEXT } from "../lib/stopTask";
import type { Chat } from "../lib/useChat";
import AgentActivity from "./AgentActivity";
import ReviewCards, { draftsByReply } from "./ReviewCards";
import StartFailure from "./StartFailure";
import ConnectPrompt, { isConnectable } from "./ConnectPrompt";
import PlanCard from "./PlanCard";

const SUGGESTIONS = [
  "Check my email, answer what you can and show me what I need to review.",
  "Post on the official account: Town hall this Tuesday, 6 pm, at the Central Library.",
];

const time = (ts: string) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// After this long without a reply, stop showing "Working…": no agent is attached to the session.
export const STALL_MS = 20_000;
export const isStalled = (lastTs: string, now: number) => now - new Date(lastTs).getTime() > STALL_MS;

/** What the inline "Working… / Done" disclosure needs. `admin` also unlocks technical details (commands, ids). */
export interface ChatActivity {
  card: SessionCard | null;
  events: DecisionEvent[];
  categories: AutonomyCategory[];
  grants?: string[];
  admin: boolean;
}

/** Stop this task: revoke the session with the staffer's own login. `stopped` once it is done. */
export interface ChatStop {
  stopped: boolean;
  onStop: () => Promise<void>;
  onNewConversation: () => void;
}

/** Drafts to show as cards under the reply that made them (decided ones too), and how to open one for review. */
export interface ChatReview {
  drafts: Draft[];
  onOpen: (id: string) => void;
  onChanged: () => Promise<void>; // reload the drafts after an edit or a revision
}

// Copies one reply to the clipboard, and says so for a moment.
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }
  return (
    <button type="button" className="msg-action" onClick={copy} aria-label={copied ? "Copied" : "Copy message"}>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

// The conversation with the agent. The agent's replies come from its own process, so after the staffer sends
// a message the panel shows "Working…" until the next assistant message arrives. Each turn carries a
// disclosure with what the agent did and may do.
export default function ChatPanel({
  chat,
  sid,
  disabled,
  activity,
  review,
  onNewConversation,
  retryAgent,
  initialFailure = null,
  renewedAfter = null,
  stop: stopTask,
}: {
  chat: Chat;
  sid: string | null;
  disabled: boolean;
  activity?: ChatActivity;
  review?: ChatReview;
  onNewConversation?: () => void;
  retryAgent?: () => Promise<unknown>; // "Try again": the server resets this conversation's restart count
  initialFailure?: SendFailure | null; // a send that already failed (the render tests start from one)
  renewedAfter?: string | null; // the last message before the session renewed: a divider follows it
  stop?: ChatStop;
}) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<SendFailure | null>(initialFailure);
  const [retrying, setRetrying] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const last = chat.messages[chat.messages.length - 1];
  // "Stop" only stops the waiting on screen; the agent's own process may still answer, and then the reply shows.
  const [stoppedFor, setStoppedFor] = useState<string | null>(null);
  const waiting = last?.role === "user" && stoppedFor !== last.id;
  const [now, setNow] = useState(() => Date.now());
  const stalled = waiting && isStalled(last.ts, now);
  const admin = activity?.admin ?? false;
  const card = activity?.card ?? null;

  // Re-check every few seconds while waiting, so a missing agent shows up without a reload.
  useEffect(() => {
    if (!waiting) return;
    const id = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(id);
  }, [waiting]);

  // Follow the newest message; once stopped, the Stopped confirmation (and its way out) above the composer.
  const stoppedRef = useRef<HTMLDivElement>(null);
  const stopped = stopTask?.stopped ?? false;
  useEffect(() => {
    (stopped ? stoppedRef : endRef).current?.scrollIntoView({ block: "nearest" });
  }, [chat.messages.length, stopped]);

  // The box grows with the text, up to a cap, like other chat apps.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setFailure(null);
    const failed = await attemptSend(chat.send, trimmed);
    setFailure(failed);
    if (!failed) setDraft("");
    setSending(false);
  }

  // "Try again": the server resets this conversation's restart count (limited and audited), then the message
  // that was refused is sent again.
  async function retry() {
    if (!retryAgent || retrying || !failure) return;
    setRetrying(true);
    const failed = await retryThenResend(retryAgent, chat.send, failure.text);
    setFailure(failed);
    if (!failed) setDraft("");
    setRetrying(false);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(draft);
  }

  function stop() {
    if (last) setStoppedFor(last.id);
  }

  async function stopThisTask() {
    if (!stopTask || sending) return;
    setSending(true);
    setStopError(null);
    try {
      await stopTask.onStop();
    } catch (e) {
      setStopError(connectionMessage(e, false));
    } finally {
      setSending(false);
    }
  }

  // Enter sends, Shift+Enter starts a new line, Escape stops waiting, as in other chat apps.
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Escape" && waiting) {
      e.preventDefault();
      stop();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(draft);
    }
  }

  const placed = review ? draftsByReply(chat.messages, review.drafts) : new Map<string, Draft[]>();

  const disclosure = (fromTs: string, toTs: string | null, working: boolean) =>
    card && (
      <AgentActivity
        card={card}
        events={turnEvents(activity!.events, fromTs, toTs)}
        categories={activity!.categories}
        grants={activity!.grants}
        working={working}
        admin={admin}
      />
    );

  return (
    <section className="chat" aria-label="Chat with your assistant">
      {chat.error && (
        <p className="chat-reconnect" role="status">
          {connectionMessage(chat.error, true)}
        </p>
      )}

      <div className="chat-log" role="log" aria-live="polite" aria-label="Conversation">
        {chat.messages.length === 0 ? (
          <div className="chat-empty">
            <p className="chat-hello">What can I take off your plate?</p>
            <p className="muted">
              Ask in plain words. Emails and posts I write wait for your review until you let me send that kind alone.
            </p>
            {admin && sid && (
              <p className="muted chat-tech">
                The assistant answers here once it is running: <code>python agent/agent.py --sid {sid} --chat</code>
              </p>
            )}
          </div>
        ) : (
          chat.messages.map((m, i) => (
            <Fragment key={m.id}>
              {m.role === "user" ? (
                <div className="msg from-user">
                  <p className="bubble-text">{m.text}</p>
                  <time className="msg-time" dateTime={m.ts}>
                    {time(m.ts)}
                  </time>
                </div>
              ) : (
                <div className="msg from-agent">
                  <span className="agent-mark" aria-hidden="true" />
                  <div className="msg-body">
                    <span className="sr-only">Assistant: </span>
                    {i > 0 && disclosure(chat.messages[i - 1].ts, m.ts, false)}
                    <p className="bubble-text">{m.text}</p>
                    {admin && m.detail && <p className="msg-detail">Technical detail: {m.detail}</p>}
                      {m.connect && isConnectable(m.connect) && <ConnectPrompt provider={m.connect} />}
                    {m.plan && sid && <PlanCard key={`${m.plan.id}-${m.plan.status}`} sid={sid} plan={m.plan} />}
                    {placed.get(m.id) && <ReviewCards drafts={placed.get(m.id)!} onOpen={review!.onOpen} onChanged={review!.onChanged} />}
                    <div className="msg-foot">
                      <time className="msg-time" dateTime={m.ts}>
                        {time(m.ts)}
                      </time>
                      <CopyButton text={m.text} />
                    </div>
                  </div>
                </div>
              )}
              {m.id === renewedAfter && (
                <p className="chat-divider" role="separator">
                  <span>Your assistant's access was renewed. Everything above stays in this conversation.</span>
                </p>
              )}
            </Fragment>
          ))
        )}
        {waiting && !disabled && !stalled && (
          <div className="msg from-agent">
            <span className="agent-mark is-thinking" aria-hidden="true" />
            <div className="msg-body">
              {disclosure(last.ts, null, true) || (
                <p className="chat-working" role="status">
                  Working…
                </p>
              )}
            </div>
          </div>
        )}
        {stopTask?.stopped && (
          <div ref={stoppedRef} className="chat-task-stopped" role="status">
            <p>{STOPPED_TEXT}</p>
            <button type="button" className="btn-primary" onClick={stopTask.onNewConversation}>
              Start a new conversation
            </button>
          </div>
        )}
        {last?.role === "user" && stoppedFor === last.id && !disabled && (
          <p className="chat-stopped" role="status">
            Stopped waiting. If the assistant finishes anyway, its reply still shows here.
          </p>
        )}
        {stalled && !disabled && (
          <p className="alert" role="status">
            No reply yet: the assistant isn't answering this session. Send your message again to restart it
            {admin ? (
              <>
                {" "}
                (if the server does not start it for you, run <code>python agent/agent.py --sid {sid} --chat</code>).
              </>
            ) : (
              ". If it still doesn't answer, ask your admin."
            )}
          </p>
        )}
        <div ref={endRef} />
      </div>

      <div className="composer-dock">
        {stopTask && last?.role === "user" && !disabled && (
          <div className="stop-task-row">
            <button type="button" className="stop-task-btn" onClick={stopThisTask} disabled={sending}>
              Stop this task
            </button>
            <span className="muted">or type “stop”. The assistant loses access right away.</span>
          </div>
        )}
        {stopError && (
          <p className="alert" role="alert">
            Could not stop the task: {stopError}
          </p>
        )}
        {chat.messages.length === 0 && !disabled && (
          <div className="chat-suggestions">
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="suggestion" onClick={() => send(s)} disabled={sending}>
                {s}
              </button>
            ))}
          </div>
        )}

        <form className="composer" onSubmit={onSubmit}>
          <label htmlFor="chat-input" className="sr-only">
            Message the assistant
          </label>
          <textarea
            id="chat-input"
            ref={inputRef}
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={disabled ? "This session was revoked. The assistant can do nothing more for it." : "Ask the assistant to do something…"}
            disabled={disabled}
            maxLength={4000}
          />
          {waiting && !stalled && !disabled && !draft.trim() ? (
            <button type="button" className="send-btn is-stop" onClick={stop} aria-label="Stop waiting for the reply">
              <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                <rect x="5" y="5" width="14" height="14" rx="2.5" fill="currentColor" />
              </svg>
            </button>
          ) : (
            <button type="submit" className="send-btn" disabled={disabled || sending || !draft.trim()} aria-label={sending ? "Sending…" : "Send message"}>
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </form>
        {failure && (
          <div className="alert" role="alert">
            <p>Could not send: {failure.error}</p>
            <StartFailure
              actions={retryAgent ? failure.actions : failure.actions.filter((a) => a !== "retry")}
              onRetry={retry}
              onNewConversation={onNewConversation}
              busy={retrying}
            />
          </div>
        )}
        <p className="composer-hint">Enter to send · Shift+Enter for a new line{waiting ? " · Esc to stop waiting" : ""}</p>
      </div>
    </section>
  );
}
