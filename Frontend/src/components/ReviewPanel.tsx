import { useEffect, useRef, useState } from "react";
import { api, type ApproveResult, type Draft, type DraftEdits } from "../lib/api";
import { approveOutcome } from "../lib/approve";
import { staleMessage } from "./DraftCard";
import { connectionMessage } from "../lib/errors";
import { draftTitle } from "./DraftCardView";

// Only the text can change; the recipient or account is what the gateway's policy already checked.
const EDITABLE: Record<Draft["tool"], (keyof DraftEdits)[]> = { email_send: ["subject", "body"], social_post: ["text"] };
const FIELD: Record<keyof DraftEdits, string> = { subject: "Subject", body: "Message", text: "Post" };

// One held draft, opened from its card in the chat: in a side panel on a wide screen, a full-screen sheet on a
// phone (like an artifact in other chat apps). The recipient is shown read-only; approval is the staffer's own API
// call; a locked category never offers "allow for this session".
export default function ReviewPanel({
  draft,
  locked,
  onDone,
  onClose,
  onGrant,
  onDecided,
  position,
  onNext,
}: {
  draft: Draft;
  locked: boolean;
  onDone: () => Promise<void>;
  onClose: () => void;
  onGrant?: (sid: string, category: string) => void; // so the chat's disclosure can list the grant
  onDecided?: (status: "sent" | "rejected") => void; // so the draft's card in the chat can say what happened
  position?: { index: number; total: number }; // where this draft is in the waiting list (1-based)
  onNext?: () => void; // open the next waiting draft without going back to the list
}) {
  const [edits, setEdits] = useState<DraftEdits>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connect, setConnect] = useState<{ label: string; url: string; note: string } | null>(null);
  const [shareUrl, setShareUrl] = useState<string | null>(null); // approved X post: the staffer presses Post on X
  const headingRef = useRef<HTMLHeadingElement>(null);
  const isEmail = draft.tool === "email_send";
  const title = draftTitle(draft);

  // A different draft opened in the same panel starts clean, with focus on its title for screen readers.
  useEffect(() => {
    setEdits({});
    setError(null);
    setShareUrl(null);
    headingRef.current?.focus();
  }, [draft.id]);

  async function decide(action: () => Promise<unknown>, status: "sent" | "rejected") {
    setBusy(true);
    setError(null);
    setConnect(null);
    try {
      const result = await action();
      // X isn't connected: the approved text opens in X's compose page, and the staffer presses Post there.
      if (status === "sent" && result && (result as ApproveResult).status === "share_link") {
        const url = (result as { url: string }).url;
        window.open(url, "_blank", "noopener");
        setShareUrl(url);
        onDecided?.(status);
        await onDone();
        return;
      }
      // Lane I: a platform that isn't connected yet; nothing was sent, so the draft stays open here.
      const outcome = status === "sent" && result ? approveOutcome(result as ApproveResult) : { done: true as const };
      if (!outcome.done) {
        setConnect(outcome.connect);
        return;
      }
      onDecided?.(status);
      await onDone();
      onClose();
    } catch (e) {
      setError(staleMessage(e) ? "This draft changed since you opened it. Close it and open it again to see the latest version." : connectionMessage(e, false));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="review-panel" role="dialog" aria-labelledby="review-panel-title">
      <header className="review-panel-head">
        <div>
          <p className="review-panel-kind">
            Draft {draft.version ?? 1} · {draft.categoryLabel} · needs your review
          </p>
          <h2 id="review-panel-title" ref={headingRef} tabIndex={-1}>
            {title}
          </h2>
        </div>
        <div className="review-panel-nav">
          {position && onNext && position.total > 1 && (
            <>
              <span className="muted review-position">
                {position.index} of {position.total}
              </span>
              <button type="button" className="btn-plain review-next" onClick={onNext}>
                Next waiting
              </button>
            </>
          )}
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close review">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </header>

      <div className="review-panel-body">
        <label className="draft-field">
          <span className="muted">{isEmail ? "To" : "Account"} (set by the office's policy, can't be changed)</span>
          <input value={isEmail ? draft.args.to : `@${draft.args.account}`} readOnly aria-readonly="true" className="is-readonly" />
        </label>
        {EDITABLE[draft.tool].map((field) => (
          <label key={field} className="draft-field">
            <span className="muted">{FIELD[field]}</span>
            {field === "subject" ? (
              <input value={edits[field] ?? draft.args[field]} onChange={(e) => setEdits({ ...edits, [field]: e.target.value })} />
            ) : (
              <textarea rows={10} value={edits[field] ?? draft.args[field]} onChange={(e) => setEdits({ ...edits, [field]: e.target.value })} />
            )}
          </label>
        ))}
      </div>

      <footer className="review-panel-actions">
        {shareUrl && (
          <p className="alert" role="status">
            Opened on X — press Post there.{" "}
            <a className="btn-primary" href={shareUrl} target="_blank" rel="noopener noreferrer">
              Open on X
            </a>
          </p>
        )}
        {connect && (
          <p className="alert" role="status">
            {connect.note} <a href={connect.url}>Connect {connect.label}</a>
          </p>
        )}
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
        <div className="draft-actions">
          <button type="button" className="btn-primary" disabled={busy || !!shareUrl} onClick={() => decide(() => api.approveDraft(draft.id, edits, draft.version), "sent")}>
            {isEmail ? "Approve and send" : "Approve and post"}
          </button>
          {draft.sid && !locked && (
            <button
              type="button"
              className="btn-plain"
              disabled={busy}
              title={`Send "${draft.categoryLabel}" without review until this session ends`}
              onClick={() =>
                decide(async () => {
                  await api.grant(draft.sid!, draft.category);
                  onGrant?.(draft.sid!, draft.category);
                  return api.approveDraft(draft.id, edits, draft.version);
                }, "sent")
              }
            >
              Approve and allow for this session
            </button>
          )}
          <button type="button" className="btn-danger-outline" disabled={busy} onClick={() => decide(() => api.rejectDraft(draft.id), "rejected")}>
            Reject
          </button>
        </div>
      </footer>
    </section>
  );
}
