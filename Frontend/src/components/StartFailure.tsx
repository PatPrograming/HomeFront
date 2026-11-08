import type { AgentAction } from "../lib/errors";

// The way back when the assistant can't start: try again (the server limits how often), or a fresh conversation.
export default function StartFailure({
  actions,
  onRetry,
  onNewConversation,
  busy,
}: {
  actions: AgentAction[];
  onRetry: () => void;
  onNewConversation?: () => void;
  busy: boolean;
}) {
  const showNew = actions.includes("new_conversation") && onNewConversation;
  if (!actions.includes("retry") && !showNew) return null;
  return (
    <div className="start-failure">
      {actions.includes("retry") && (
        <button type="button" className="btn-primary" onClick={onRetry} disabled={busy}>
          {busy ? "Trying…" : "Try again"}
        </button>
      )}
      {showNew && (
        <button type="button" className="btn-plain" onClick={onNewConversation}>
          Start a new conversation
        </button>
      )}
    </div>
  );
}
