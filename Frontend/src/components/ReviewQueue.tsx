import { useState } from "react";
import { api, type Draft, type DraftEdits } from "../lib/api";
import ConnectPrompt, { parseNeedsConnection, type NeedsConnection } from "./ConnectPrompt";

// Only the text can change; the recipient or account is what the gateway's policy already checked.
const EDITABLE: Record<Draft["tool"], (keyof DraftEdits)[]> = { email_send: ["subject", "body"], social_post: ["text"] };

function DraftCard({
  draft,
  locked,
  onDone,
  onGrant,
}: {
  draft: Draft;
  locked: boolean;
  onDone: () => Promise<void>;
  onGrant?: (sid: string, category: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<DraftEdits>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needs, setNeeds] = useState<NeedsConnection | null>(null);
  const isEmail = draft.tool === "email_send";

  async function decide(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      // Not sent: the account isn't connected. The draft stays here so it can be approved again after connecting.
      const missing = parseNeedsConnection(await action());
      setNeeds(missing);
      if (missing) {
        setBusy(false);
        return;
      }
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <li className="draft">
      <div className="draft-head">
        <span className="chip">{draft.categoryLabel}</span>
        <span className="muted">{isEmail ? `To ${draft.args.to}` : `Post on @${draft.args.account}`}</span>
      </div>
      {editing ? (
        EDITABLE[draft.tool].map((field) => (
          <label key={field} className="draft-field">
            <span className="muted">{field === "text" ? "Post" : field[0].toUpperCase() + field.slice(1)}</span>
            {field === "subject" ? (
              <input value={edits[field] ?? draft.args[field]} onChange={(e) => setEdits({ ...edits, [field]: e.target.value })} />
            ) : (
              <textarea rows={5} value={edits[field] ?? draft.args[field]} onChange={(e) => setEdits({ ...edits, [field]: e.target.value })} />
            )}
          </label>
        ))
      ) : (
        <>
          {isEmail && <p className="draft-subject">{draft.args.subject}</p>}
          <p className="draft-body">{isEmail ? draft.args.body : draft.args.text}</p>
        </>
      )}
      <div className="draft-actions">
        <button type="button" className="btn-primary" disabled={busy} onClick={() => decide(() => api.approveDraft(draft.id, edits))}>
          {isEmail ? "Approve and send" : "Approve and post"}
        </button>
        {!editing && (
          <button type="button" className="btn-plain" disabled={busy} onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
        <button type="button" className="btn-danger-outline" disabled={busy} onClick={() => decide(() => api.rejectDraft(draft.id))}>
          Reject
        </button>
        {draft.sid && !locked && (
          <button
            type="button"
            className="btn-plain"
            disabled={busy}
            title={`Send "${draft.categoryLabel}" without review until this session ends`}
            onClick={() => decide(async () => {
              await api.grant(draft.sid!, draft.category);
              onGrant?.(draft.sid!, draft.category);
              return api.approveDraft(draft.id, edits);
            })}
          >
            Approve and allow for this session
          </button>
        )}
      </div>
      {needs && <ConnectPrompt provider={needs.provider} note={needs.note} />}
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}

// Drafts the gateway held because the staffer has not let the agent send that kind alone.
export default function ReviewQueue({
  drafts,
  locked,
  onDone,
  onGrant,
}: {
  drafts: Draft[];
  locked: Set<string>; // categories that always need review: no "allow for this session" offer
  onDone: () => Promise<void>;
  onGrant?: (sid: string, category: string) => void; // so the chat's disclosure can list the grant
}) {
  return (
    <section className="panel" aria-labelledby="review-title">
      <div className="panel-head">
        <h2 id="review-title">Needs your review</h2>
        <span className="figure">{drafts.length}</span>
      </div>
      {drafts.length === 0 ? (
        <p className="muted">Nothing waiting. Emails and posts the assistant drafts show up here before anything is sent.</p>
      ) : (
        <ul className="drafts">
          {drafts.map((d) => (
            <DraftCard key={d.id} draft={d} locked={locked.has(d.category)} onDone={onDone} onGrant={onGrant} />
          ))}
        </ul>
      )}
    </section>
  );
}
