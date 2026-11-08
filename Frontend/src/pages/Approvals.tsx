import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Layout from "../components/Layout";
import { agentsApi as api } from "../lib/agentsApi";
import { useAuth } from "../lib/AuthProvider";
import { describeError, toolAction, type Approval } from "../lib/agents";
import { emailSetupPath } from "./EmailSetup";
import "./Agents.css";
import "./Approvals.css";

// Fields shown as multi-line text areas.
const LONG_FIELDS = new Set(["body", "text"]);

const FIELD_LABELS: Record<string, string> = {
  to: "To",
  subject: "Subject",
  body: "Message",
  platform: "Platform",
  text: "Text",
  reply_to: "Replying to",
};

const STATUS_LABELS: Record<string, string> = {
  executed: "Sent",
  rejected: "Rejected",
  failed: "Failed",
  expired: "Expired",
  approved: "Sending…",
  pending: "Pending",
};

const time = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : "—");

function ApprovalCard({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  const [args, setArgs] = useState<Record<string, string>>(approval.arguments);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fields = Object.keys(approval.input_schema?.properties || approval.arguments);
  const edited = fields.some((f) => (args[f] ?? "") !== (approval.arguments[f] ?? ""));

  async function decide(kind: "approve" | "reject") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "approve") {
        await api.approve(approval.approval_id, { ...(edited ? { arguments: args } : {}), ...(note ? { note } : {}) });
      } else {
        await api.reject(approval.approval_id, note || undefined);
      }
      onDone();
    } catch (e) {
      setError(describeError(e));
      setBusy(null);
    }
  }

  return (
    <li className="approval-card">
      <div className="approval-head">
        <div>
          <p className="approval-what">
            <strong>{approval.agent?.name || approval.agent_id}</strong> wants to {toolAction(approval.tool)}
          </p>
          <p className="muted approval-sub">
            <code>{approval.tool}</code> · requested {time(approval.requested_at)} · expires {time(approval.expires_at)}
          </p>
        </div>
        {approval.trace_id && (
          <Link className="trace-link" to={`/activity?trace=${encodeURIComponent(approval.trace_id)}`}>
            View trace
          </Link>
        )}
      </div>

      {approval.recipients && (
        <p className="approval-audience">
          Goes to <strong>{approval.recipients.length}</strong> address
          {approval.recipients.length === 1 ? "" : "es"} on the mailing list
          {approval.recipients.length > 0 && (
            <span className="muted">
              {" "}
              ({approval.recipients.slice(0, 5).join(", ")}
              {approval.recipients.length > 5 ? `, +${approval.recipients.length - 5} more` : ""})
            </span>
          )}
        </p>
      )}

      {approval.sender && (
        <dl className="approval-sender">
          <dt>From</dt>
          <dd>
            {approval.sender.simulated ? (
              <span className="muted">No sender set: this will only be simulated, nothing is delivered</span>
            ) : (
              <>
                {approval.sender.from}
                {approval.sender.from_source === "env" && <span className="muted"> (server default)</span>}
              </>
            )}
          </dd>
          {approval.sender.reply_to && (
            <>
              <dt>Reply-to</dt>
              <dd>{approval.sender.reply_to}</dd>
            </>
          )}
          {approval.sender.signature && (
            <>
              <dt>Signature</dt>
              <dd>
                <pre className="approval-signature">{approval.sender.signature}</pre>
                <span className="muted">Added below the message automatically.</span>
              </dd>
            </>
          )}
          <dt />
          <dd>
            <Link to={emailSetupPath(approval.agent_id)}>Change sender/recipients</Link>
          </dd>
        </dl>
      )}

      {approval.delivery?.sandbox === true && (approval.delivery.unverified?.length ?? 0) > 0 && (
        <p className="sandbox-warning" role="status">
          <strong>SES sandbox:</strong> {approval.delivery.unverified!.length} of{" "}
          {approval.delivery.recipient_count ?? approval.delivery.unverified!.length} recipient
          {(approval.delivery.recipient_count ?? 0) === 1 ? "" : "s"} aren't verified, so SES will reject them:{" "}
          {approval.delivery.unverified!.slice(0, 5).join(", ")}
          {approval.delivery.unverified!.length > 5 ? ", …" : ""}. You can still approve.{" "}
          <Link to={emailSetupPath(approval.agent_id)}>Verify recipients</Link>
        </p>
      )}
      {approval.delivery?.sandbox === true && approval.delivery.unverified === null && (
        <p className="muted approval-sub">SES sandbox: couldn't check which recipients are verified.</p>
      )}

      <div className="approval-fields">
        {fields.map((f) => {
          const label = FIELD_LABELS[f] || f;
          const hint = approval.input_schema?.properties?.[f]?.description;
          const changed = (args[f] ?? "") !== (approval.arguments[f] ?? "");
          return (
            <label key={f} className="field">
              <span className="field-label">
                {label} {changed && <span className="edited-tag">edited</span>}
              </span>
              {LONG_FIELDS.has(f) ? (
                <textarea
                  className="textarea"
                  rows={5}
                  value={args[f] ?? ""}
                  onChange={(e) => setArgs({ ...args, [f]: e.target.value })}
                />
              ) : (
                <input
                  className="input"
                  value={args[f] ?? ""}
                  onChange={(e) => setArgs({ ...args, [f]: e.target.value })}
                  title={hint}
                />
              )}
            </label>
          );
        })}
      </div>

      <label className="field">
        <span className="field-label">Note (optional)</span>
        <input
          className="input"
          maxLength={1000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Recorded in the activity log"
        />
      </label>

      {error && (
        <p className="alert alert-error" role="alert">
          {error}
        </p>
      )}

      <div className="form-actions">
        <button className="btn btn-primary" type="button" disabled={!!busy} onClick={() => decide("approve")}>
          {busy === "approve" ? "Sending…" : edited ? "Approve with edits" : "Approve and send"}
        </button>
        <button className="btn btn-outline danger" type="button" disabled={!!busy} onClick={() => decide("reject")}>
          {busy === "reject" ? "Rejecting…" : "Reject"}
        </button>
        {edited && (
          <button className="btn btn-ghost" type="button" onClick={() => setArgs(approval.arguments)}>
            Undo edits
          </button>
        )}
      </div>
    </li>
  );
}

function HistoryRow({ approval, you }: { approval: Approval; you?: string }) {
  const decidedBy =
    approval.decided_by && you && approval.decided_by === `user:${you}` ? "you" : approval.decided_by || "—";
  return (
    <li className={`history-row status-${approval.status}`}>
      <span className={`status-badge status-${approval.status}`}>{STATUS_LABELS[approval.status] || approval.status}</span>
      <span>
        <strong>{approval.agent?.name || approval.agent_id}</strong> · <code>{approval.tool}</code>
        {approval.edited && <span className="edited-tag">edited</span>}
      </span>
      <span className="muted">
        {approval.status === "expired" ? `requested ${time(approval.requested_at)}` : `by ${decidedBy}, ${time(approval.decided_at)}`}
      </span>
      {approval.note && <span className="history-note">“{approval.note}”</span>}
      {approval.error && <span className="history-note error">{approval.error}</span>}
      {approval.trace_id && (
        <Link className="trace-link" to={`/activity?trace=${encodeURIComponent(approval.trace_id)}`}>
          Trace
        </Link>
      )}
    </li>
  );
}

export default function Approvals() {
  const { session: user } = useAuth();
  const [all, setAll] = useState<Approval[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      api
        .approvals()
        .then((a: Approval[]) => {
          setAll(a);
          setError(null);
        })
        .catch((e: unknown) => setError(describeError(e))),
    [],
  );

  useEffect(() => {
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [load]);

  const pending = (all || []).filter((a) => a.status === "pending");
  const history = (all || []).filter((a) => a.status !== "pending");

  return (
    <Layout>
      <div className="agents-page approvals-page">
        <div className="container">
          <p className="eyebrow">Human in the loop · AML.M0029</p>
          <h1>Approvals</h1>
          <p className="lede">
            Agents can't send email, post or reply on their own. Each of those actions waits here until someone
            approves it. You can edit the draft first, and every decision is recorded in the activity log.
          </p>

          {error && <p className="alert alert-error">Couldn't load approvals: {error}</p>}
          {!all && !error && <p className="muted">Loading…</p>}

          {all && (
            <>
              <h2 className="section-title">
                Waiting for you <span className="count">{pending.length}</span>
              </h2>
              {pending.length === 0 ? (
                <div className="empty">
                  <p>Nothing waiting. When an agent tries to send an email or post, it appears here.</p>
                </div>
              ) : (
                <ul className="approval-list">
                  {pending.map((a) => (
                    <ApprovalCard key={a.approval_id} approval={a} onDone={load} />
                  ))}
                </ul>
              )}

              {history.length > 0 && (
                <>
                  <h2 className="section-title">History</h2>
                  <ul className="history-list">
                    {history.map((a) => (
                      <HistoryRow key={a.approval_id} approval={a} you={user?.username} />
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </Layout>
  );
}
