// "Email setup" on an agent card: sender details, signature and the mailing list.
import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";
import { agentsApi as api } from "../lib/agentsApi";
import EmailBranding from "./EmailBranding";
import EmailDelivery from "./EmailDelivery";
import {
  describeError,
  emailSettingsOf,
  kindLabel,
  typeAllows,
  type Agent,
  type AgentType,
  type EmailSettings,
  type EmailVerification,
} from "../lib/agents";

/** Where to edit an agent's sender and recipients (opens the editor on My agents). */
export const emailSetupPath = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/email`;

/** Shown instead of Email setup for agents without an email tool. */
export function NoEmailNote({ agent, types }: { agent: Agent; types: AgentType[] }) {
  const type = types.find((t) => t.kind === agent.kind);
  // Before the types load, assume the tool could be granted.
  const canGrant = !type || typeAllows(type, "email.send");
  return (
    <div className="mailing-list">
      <span className="field-label">Email setup</span>
      <p className="muted mailing-list-hint">
        {canGrant ? (
          <>
            This agent has no email tool, so there's no sender or recipient list to set. Grant{" "}
            <code>email.send</code> under Tool grants on the{" "}
            <Link to={`/monitoring?agent=${encodeURIComponent(agent.agent_id)}`}>Monitoring</Link> page to set
            one up.
          </>
        ) : (
          <>{kindLabel(types, agent.kind)} agents can't send email, so there's no email setup.</>
        )}
      </p>
    </div>
  );
}

// Mirrors the gateway's check (store.py); the server has the final say.
const VALID_EMAIL = /^[^@\s,;<>"']+@[^@\s,;<>"']+\.[^@\s,;<>"'.][^@\s,;<>"']*$/;
// Anything with an @ in it, from any column of a CSV or line of a text file.
const EMAILISH = /[^\s,;<>"'()[\]]+@[^\s,;<>"'()[\]]+/g;

export interface ParsedList {
  valid: string[];
  invalid: string[];
}

/** Every email-looking token in pasted text or an uploaded .csv/.txt (BOM, quotes, CRLF, headers are fine). */
export function parseEmailText(text: string): ParsedList {
  const tokens = text.replace(/^﻿/, "").match(EMAILISH) || [];
  const valid = new Set<string>();
  const invalid = new Set<string>();
  for (const raw of tokens) {
    const e = raw.trim().toLowerCase().replace(/^mailto:/, "").replace(/[.]+$/, "");
    (VALID_EMAIL.test(e) && e.length <= 254 ? valid : invalid).add(e);
  }
  return { valid: [...valid].sort(), invalid: [...invalid].sort() };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "es"}`;

function VerificationBadge({ status }: { status: EmailVerification | null | "loading" }) {
  if (status === "loading") return <span className="muted">Checking SES…</span>;
  if (!status) return null;
  if (!status.from_email) return null;
  if (status.verified === true) return <span className="ses-badge ses-ok">Verified in SES</span>;
  if (status.verified === false)
    return (
      <span className="ses-badge ses-bad" title={status.reason}>
        Not verified in SES
      </span>
    );
  return (
    <span className="ses-badge ses-unknown" title={status.reason}>
      Couldn't check SES
    </span>
  );
}

function UploadPreview({
  parsed,
  current,
  fileName,
  busy,
  onApply,
  onCancel,
}: {
  parsed: ParsedList;
  current: string[];
  fileName: string;
  busy: boolean;
  onApply: (mode: "replace" | "add") => void;
  onCancel: () => void;
}) {
  const fresh = parsed.valid.filter((e) => !current.includes(e)).length;
  return (
    <div className="upload-preview">
      <p>
        <strong>{fileName}</strong>: found {plural(parsed.valid.length, "address")}
        {current.length > 0 && ` (${fresh} not on the list yet)`}.
      </p>
      {parsed.invalid.length > 0 && (
        <p className="mailing-list-empty">
          Skipping {parsed.invalid.length} invalid: {parsed.invalid.slice(0, 5).join(", ")}
          {parsed.invalid.length > 5 ? ", …" : ""}
        </p>
      )}
      <div className="form-actions">
        <button
          className="btn btn-primary"
          type="button"
          disabled={busy || !parsed.valid.length}
          onClick={() => onApply("replace")}
        >
          Replace list
        </button>
        <button
          className="btn btn-outline"
          type="button"
          disabled={busy || !fresh}
          onClick={() => onApply("add")}
        >
          Add to list
        </button>
        <button className="btn btn-ghost" type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export default function EmailSetup({
  agent,
  onSaved,
  defaultOpen = false,
}: {
  agent: Agent;
  onSaved: (a: Agent) => void;
  defaultOpen?: boolean;
}) {
  // `?? []`: an API older than mailing lists omits the field.
  const recipients = agent.recipients ?? [];
  const settings = emailSettingsOf(agent);

  const [editing, setEditing] = useState(defaultOpen);
  const [form, setForm] = useState<EmailSettings>(settings);
  const [listDraft, setListDraft] = useState(() => recipients.join("\n"));
  const [upload, setUpload] = useState<{ name: string; parsed: ParsedList } | null>(null);
  const [verification, setVerification] = useState<EmailVerification | null | "loading">(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const checkVerification = useCallback(() => {
    setVerification("loading");
    api
      .emailVerification(agent.agent_id)
      .then((v: EmailVerification) => setVerification(v ?? null))
      // Say so instead of hiding the badge (old API without the route, network, ...).
      .catch((e) =>
        setVerification({
          from_email: settings.from_email || null,
          source: settings.from_email ? "agent" : null,
          verified: null,
          reason: `Couldn't check SES: ${describeError(e)}`,
        }),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.agent_id, settings.from_email]);

  useEffect(checkVerification, [checkVerification]);

  function open() {
    setForm(settings);
    setListDraft(recipients.join("\n"));
    setUpload(null);
    setError(null);
    setNotice(null);
    setEditing(true);
  }

  async function run(action: () => Promise<Agent>, done: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await action();
      onSaved(updated);
      setListDraft((updated.recipients ?? []).join("\n"));
      setNotice(done);
      return updated;
    } catch (e) {
      setError(describeError(e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function saveAll() {
    const parsed = parseEmailText(listDraft);
    if (parsed.invalid.length) {
      setError(`Fix or remove these addresses first: ${parsed.invalid.join(", ")}`);
      return;
    }
    const ok = await run(async () => {
      // Show the saved settings even if the list save below fails.
      onSaved(await api.setEmailSettings(agent.agent_id, form));
      return api.setRecipients(agent.agent_id, parsed.valid);
    }, "Saved.");
    if (ok) {
      setEditing(false);
      checkVerification();
    }
  }

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow picking the same file again
    if (!file) return;
    if (file.size > 2_000_000) {
      setError("That file is too large (2 MB max).");
      return;
    }
    setError(null);
    setNotice(null);
    setUpload({ name: file.name, parsed: parseEmailText(await file.text()) });
  }

  async function applyUpload(mode: "replace" | "add") {
    if (!upload) return;
    const next = mode === "replace" ? upload.parsed.valid : [...new Set([...recipients, ...upload.parsed.valid])];
    const updated = await run(
      () => api.setRecipients(agent.agent_id, next),
      mode === "replace" ? "List replaced." : "Addresses added.",
    );
    if (updated) setUpload(null);
  }

  const set = (field: keyof EmailSettings) => (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [field]: e.target.value });

  const fromLabel = settings.from_email
    ? settings.from_name
      ? `${settings.from_name} <${settings.from_email}>`
      : settings.from_email
    : verification && verification !== "loading" && verification.source === "env"
      ? `${verification.from_email} (server default)`
      : null;
  const draftCount = parseEmailText(listDraft).valid.length;

  const needsSetup = !fromLabel || recipients.length === 0;
  const reason = verification && verification !== "loading" && verification.verified !== true ? verification.reason : null;

  return (
    <div className="mailing-list" id={`email-${agent.agent_id}`}>
      <div className="mailing-list-head">
        <span className="field-label">Email setup</span>
      </div>

      {!editing && (
        <>
          <dl className="email-summary">
            <dt>From</dt>
            <dd>
              {fromLabel ?? <span className="mailing-list-empty">Not set: emails are only simulated</span>}{" "}
              <VerificationBadge status={verification} />
            </dd>
            <dt>Reply-to</dt>
            <dd>{settings.reply_to || <span className="muted">Same as sender</span>}</dd>
            <dt>Signature</dt>
            <dd>{settings.signature ? "Added to every email" : <span className="muted">None</span>}</dd>
            <dt>Logo</dt>
            <dd>{agent.email_logo ? "Shown in every email" : <span className="muted">None</span>}</dd>
            <dt>Recipients</dt>
            <dd>
              {recipients.length ? (
                plural(recipients.length, "address")
              ) : (
                <span className="mailing-list-empty">None: this agent can't email anyone</span>
              )}
            </dd>
          </dl>
          {reason && <p className="muted mailing-list-hint">{reason}</p>}
          <EmailBranding agent={agent} onSaved={onSaved} />
          <EmailDelivery
            agent={agent}
            senderEmail={
              settings.from_email ||
              (verification && verification !== "loading" ? verification.from_email : null) ||
              null
            }
            verification={verification}
            onSenderChanged={checkVerification}
          />
          <div className="form-actions">
            <button className="btn btn-primary" type="button" onClick={open}>
              {needsSetup ? "Set sender & recipients" : "Edit email setup"}
            </button>
          </div>
        </>
      )}

      {notice && !editing && <p className="muted mailing-list-hint">{notice}</p>}

      {editing && (
        <div className="email-editor">
          <label className="field">
            <span className="field-label">Sender email</span>
            <input
              className="input"
              type="email"
              value={form.from_email}
              onChange={set("from_email")}
              placeholder="notices@youragency.gov (blank = server default)"
            />
          </label>
          <label className="field">
            <span className="field-label">Sender name (optional)</span>
            <input
              className="input"
              maxLength={80}
              value={form.from_name}
              onChange={set("from_name")}
              placeholder={agent.agency}
            />
          </label>
          <label className="field">
            <span className="field-label">Reply-to (optional)</span>
            <input
              className="input"
              type="email"
              value={form.reply_to}
              onChange={set("reply_to")}
              placeholder="Where replies should go"
            />
          </label>
          <label className="field">
            <span className="field-label">Signature (optional)</span>
            <textarea
              className="textarea"
              rows={3}
              maxLength={1000}
              value={form.signature}
              onChange={set("signature")}
              placeholder="Added to the end of every email automatically"
            />
          </label>

          <div className="field">
            <span className="field-label">Mailing list</span>
            <textarea
              className="textarea"
              rows={6}
              value={listDraft}
              onChange={(e) => setListDraft(e.target.value)}
              placeholder="One address per line, or paste a comma-separated list"
              aria-label={`Mailing list for ${agent.name}`}
            />
            <div className="upload-row">
              <span className="muted mailing-list-hint">
                {plural(draftCount, "address")} · the agent can only email these.
              </span>
              <label className="btn btn-outline upload-btn">
                Upload .csv or .txt
                <input type="file" accept=".csv,.txt,text/csv,text/plain" onChange={onFile} hidden />
              </label>
            </div>
            {upload && (
              <UploadPreview
                parsed={upload.parsed}
                current={recipients}
                fileName={upload.name}
                busy={busy}
                onApply={applyUpload}
                onCancel={() => setUpload(null)}
              />
            )}
          </div>

          {error && (
            <p className="alert alert-error" role="alert">
              {error}
            </p>
          )}
          {notice && <p className="muted mailing-list-hint">{notice}</p>}

          <div className="form-actions">
            <button className="btn btn-primary" type="button" disabled={busy} onClick={saveAll}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
