import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Chat } from "../lib/useChat";

const SUGGESTIONS = [
  "Check my email, answer what you can and show me what I need to review.",
  "Post on the official account: Town hall this Tuesday, 6 pm, at the Central Library.",
];

const time = (ts: string) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// The conversation with the agent. The agent's replies come from its own process, so after the staffer sends
// a message the panel shows "Working…" until the next assistant message arrives.
export default function ChatPanel({ chat, sid, disabled }: { chat: Chat; sid: string; disabled: boolean }) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const last = chat.messages[chat.messages.length - 1];
  const waiting = last?.role === "user";

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [chat.messages.length]);

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

  return (
    <section className="panel chat" aria-labelledby="chat-title">
      <div className="panel-head">
        <h2 id="chat-title">Chat with your assistant</h2>
        {chat.error && <p className="deny-text">Reconnecting: {chat.error.message}</p>}
      </div>

      <div className="chat-log" role="log" aria-live="polite" aria-label="Conversation">
        {chat.messages.length === 0 ? (
          <div className="chat-empty">
            <p>Ask in plain words. Emails and posts it writes wait for your review until you let it send that kind alone.</p>
            <p className="muted">
              The assistant answers here once it is running:{" "}
              <code>python agent/agent.py --sid {sid} --chat</code>
            </p>
          </div>
        ) : (
          chat.messages.map((m) => (
            <div key={m.id} className={`bubble ${m.role === "user" ? "from-user" : "from-agent"}`}>
              <span className="bubble-meta">
                {m.role === "user" ? "You" : "Assistant"} · <time dateTime={m.ts}>{time(m.ts)}</time>
              </span>
              <p className="bubble-text">{m.text}</p>
            </div>
          ))
        )}
        {waiting && !disabled && (
          <p className="chat-working" role="status">
            Working…
          </p>
        )}
        <div ref={endRef} />
      </div>

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
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={disabled ? "This session was revoked. Start a new one to chat." : "Ask the assistant to do something…"}
          disabled={disabled}
          maxLength={4000}
        />
        <button type="submit" className="btn-primary" disabled={disabled || sending || !draft.trim()}>
          {sending ? "Sending…" : "Send"}
        </button>
      </form>
      {error && (
        <p className="alert" role="alert">
          Could not send: {error}
        </p>
      )}
    </section>
  );
}
