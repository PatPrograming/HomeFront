import type { SessionSummary } from "../lib/api";

const STATUS = { active: "", revoked: "Revoked", expired: "Ended" } as const;

// The staffer's conversations, newest first, each its own session with its own agent. No hooks, so a test can
// call it and press its buttons. Which one is open is per browser tab, so two tabs can show two conversations.
export default function ConversationList({
  sessions,
  currentSid,
  onSelect,
  onNew,
}: {
  sessions: SessionSummary[];
  currentSid: string | null;
  onSelect: (sid: string) => void;
  onNew: () => void;
}) {
  const newest = [...sessions].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return (
    <nav className="conversations" aria-label="Conversations">
      <button type="button" className="btn-plain new-conversation" onClick={onNew}>
        + New conversation
      </button>
      <ul>
        {newest.map((s) => (
          <li key={s.sid}>
            <button
              type="button"
              className="conversation"
              aria-current={s.sid === currentSid ? "true" : undefined}
              onClick={() => onSelect(s.sid)}
            >
              <span className="conversation-title">{s.title}</span>
              <span className="conversation-meta">
                {STATUS[s.status] && <span className={`conversation-status is-${s.status}`}>{STATUS[s.status]}</span>}
                {s.waiting > 0 && <span className="conversation-waiting">{s.waiting} waiting</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
