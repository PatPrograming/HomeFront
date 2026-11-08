import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import type { AutonomyCategory, SessionCard } from "../lib/api";
import { turnEvents } from "../lib/agentActivity";
import type { Chat } from "../lib/useChat";
import AgentActivity from "./AgentActivity";
import ConnectPrompt, { isConnectable } from "./ConnectPrompt";

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

// The conversation with the agent. The agent's replies come from its own process, so after the staffer sends
// a message the panel shows "Working…" until the next assistant message arrives. Each turn carries a
// disclosure with what the agent did and may do.
export default function ChatPanel({
  chat,
  sid,
  disabled,
  activity,
}: {
  chat: Chat;
  sid: string | null;
  disabled: boolean;
  activity?: ChatActivity;
}) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const last = chat.messages[chat.messages.length - 1];
  const waiting = last?.role === "user";
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

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [chat.messages.length]);

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
    setError(null);
    try {
      await chat.send(trimmed);
      setDraft("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(draft);
  }

  // Enter sends, Shift+Enter starts a new line, as in other chat apps.
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(draft);
    }
  }

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
      {chat.error && <p className="chat-reconnect">Reconnecting: {chat.error.message}</p>}

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
          chat.messages.map((m, i) =>
            m.role === "user" ? (
              <div key={m.id} className="msg from-user">
                <p className="bubble-text">{m.text}</p>
                <time className="msg-time" dateTime={m.ts}>
                  {time(m.ts)}
                </time>
              </div>
            ) : (
              <div key={m.id} className="msg from-agent">
                <span className="agent-mark" aria-hidden="true" />
                <div className="msg-body">
                  <span className="sr-only">Assistant: </span>
                  {i > 0 && disclosure(chat.messages[i - 1].ts, m.ts, false)}
                  <p className="bubble-text">{m.text}</p>
                  {m.connect && isConnectable(m.connect) && <ConnectPrompt provider={m.connect} />}
                  <time className="msg-time" dateTime={m.ts}>
                    {time(m.ts)}
                  </time>
                </div>
              </div>
            ),
          )
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
          <button type="submit" className="send-btn" disabled={disabled || sending || !draft.trim()} aria-label={sending ? "Sending…" : "Send message"}>
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M12 19V5M5 12l7-7 7 7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </form>
        {error && (
          <p className="alert" role="alert">
            Could not send: {error}
          </p>
        )}
        <p className="composer-hint">Enter to send · Shift+Enter for a new line</p>
      </div>
    </section>
  );
}
