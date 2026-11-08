import type { Draft, DraftPatch } from "../lib/api";

export interface DraftCardViewProps {
  draft: Draft;
  editing: boolean;
  edits: DraftPatch;
  instruction: string; // "make it shorter", typed by the staffer
  busy: boolean;
  error: string | null;
  onOpen: (id: string) => void; // the review path: approve, allow for this session, reject
  onEdit: (editing: boolean) => void;
  onChange: (patch: DraftPatch) => void;
  onSave: (edits: DraftPatch) => void;
  onInstruction: (text: string) => void;
  onRevise: (instruction: string) => void;
}

const DONE: Record<Draft["status"], string> = { pending: "needs your review", sent: "Sent", rejected: "Rejected" };
// Without the server's list, the text is editable and the recipient or account is not.
const DEFAULT_EDITABLE: Record<Draft["tool"], string[]> = { email_send: ["subject", "body"], social_post: ["text"] };

const platformNames = (d: Draft, ids: string[]) =>
  ids.map((id) => d.platformOptions?.find((p) => p.id === id)?.label ?? id).join(", ");
export const draftTitle = (d: Draft) =>
  d.tool === "email_send" ? `Reply to ${d.args.to}` : `Post on ${d.platforms?.length ? platformNames(d, d.platforms) : `@${d.args.account}`}`;

// A held draft in the chat, like an artifact: the version, where it goes, the text; edit it in place or ask the
// agent for a change; approve it through the review panel. Markup only (no hooks), so a test can press its buttons.
export function DraftCardView(p: DraftCardViewProps) {
  const d = p.draft;
  const editable = new Set(d.editable ?? DEFAULT_EDITABLE[d.tool]);
  const value = (field: "subject" | "body" | "text" | "to") => p.edits[field] ?? d.args[field] ?? "";
  const platforms = p.edits.platforms ?? d.platforms ?? [];
  const pending = d.status === "pending";
  const locked = !pending || d.revising || p.busy;

  return (
    <article className={`draft-card${pending ? "" : " is-done"}${d.revising ? " is-revising" : ""}`} aria-label={draftTitle(d)}>
      <header className="draft-card-head">
        <span className="draft-version">Draft {d.version ?? 1}</span>
        <span className="draft-card-title">{draftTitle(d)}</span>
        <span className="draft-card-meta">
          {d.categoryLabel} · {d.revising ? "Revising…" : DONE[d.status]}
        </span>
      </header>

      {p.editing && pending ? (
        <div className="draft-card-fields">
          {d.tool === "email_send" ? (
            <>
              <label className="draft-field">
                <span className="muted">To</span>
                <input aria-label="Recipient" value={value("to")} readOnly={!editable.has("to")} className={editable.has("to") ? undefined : "is-readonly"}
                  onChange={(e) => p.onChange({ to: e.target.value })} />
              </label>
              <label className="draft-field">
                <span className="muted">Subject</span>
                <input aria-label="Subject" value={value("subject")} readOnly={!editable.has("subject")} onChange={(e) => p.onChange({ subject: e.target.value })} />
              </label>
              <label className="draft-field">
                <span className="muted">Message</span>
                <textarea aria-label="Message" rows={6} value={value("body")} readOnly={!editable.has("body")} onChange={(e) => p.onChange({ body: e.target.value })} />
              </label>
            </>
          ) : (
            <>
              <div className="draft-field">
                <span className="muted">Post on</span>
                <div className="platform-chips" role="group" aria-label="Platforms">
                  {(d.platformOptions ?? []).map((o) => {
                    const on = platforms.includes(o.id);
                    return (
                      <button key={o.id} type="button" className="platform-chip" aria-pressed={on} disabled={!editable.has("platforms")}
                        onClick={() => p.onChange({ platforms: on ? platforms.filter((x) => x !== o.id) : [...platforms, o.id] })}>
                        {o.label}
                      </button>
                    );
                  })}
                </div>
              </div>
              <label className="draft-field">
                <span className="muted">Post</span>
                <textarea aria-label="Post text" rows={5} value={value("text")} readOnly={!editable.has("text")} onChange={(e) => p.onChange({ text: e.target.value })} />
              </label>
            </>
          )}
          <div className="draft-actions">
            <button type="button" className="btn-primary" disabled={p.busy} onClick={() => p.onSave(p.edits)}>
              Save edits
            </button>
            <button type="button" className="btn-plain" disabled={p.busy} onClick={() => p.onEdit(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="draft-card-body">
          {d.tool === "email_send" && <p className="draft-card-to">To {d.args.to}</p>}
          {d.tool === "email_send" && <p className="draft-card-subject">{d.args.subject}</p>}
          <p className="draft-card-text">{d.tool === "email_send" ? d.args.body : d.args.text}</p>
        </div>
      )}

      {pending && (
        <footer className="draft-card-foot">
          {!p.editing && (
            <form
              className="draft-revise"
              onSubmit={(e) => {
                e.preventDefault();
                p.onRevise(p.instruction);
              }}
            >
              <input
                aria-label="Ask for changes to this draft"
                placeholder="Ask for a change…"
                value={p.instruction}
                maxLength={500}
                disabled={locked}
                onChange={(e) => p.onInstruction(e.target.value)}
              />
              <button type="button" className="btn-plain" disabled={locked || !p.instruction.trim()} onClick={() => p.onRevise(p.instruction)}>
                Revise
              </button>
            </form>
          )}
          <div className="draft-actions">
            {!p.editing && !d.revising && (
              <button type="button" className="btn-plain" disabled={p.busy} onClick={() => p.onEdit(true)}>
                Edit
              </button>
            )}
            <button type="button" className="btn-primary" onClick={() => p.onOpen(d.id)} aria-label={`Review: ${draftTitle(d)}`}>
              Review and send
            </button>
          </div>
        </footer>
      )}
      {p.error && (
        <p className="alert" role="alert">
          {p.error}
        </p>
      )}
    </article>
  );
}
