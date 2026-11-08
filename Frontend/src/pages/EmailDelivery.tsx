// "Can this agent actually deliver?": SES sender verification, sandbox recipients,
// IAM permissions and a real test send. Every AWS action here is a button.
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api.js";
import {
  describeError,
  type Agent,
  type EmailAccount,
  type EmailPermissions,
  type EmailVerification,
  type RecipientStatus,
  type RecipientVerification,
  type TestEmailResult,
} from "../lib/agents";

const STATUS_TEXT: Record<string, string> = {
  verified: "verified",
  pending: "link sent, not clicked yet",
  failed: "verification failed",
  not_requested: "not verified",
};

const needsRequest = (s: RecipientStatus) => s === "not_requested" || s === "failed";

function Permission({ label, value }: { label: string; value: boolean | null | undefined }) {
  if (value === true) return <span className="ses-badge ses-ok">{label}: allowed</span>;
  if (value === false) return <span className="ses-badge ses-bad">{label}: not allowed</span>;
  return <span className="ses-badge ses-unknown">{label}: unknown</span>;
}

export default function EmailDelivery({
  agent,
  senderEmail,
  verification,
  onSenderChanged,
}: {
  agent: Agent;
  senderEmail: string | null;
  verification: EmailVerification | null | "loading";
  onSenderChanged: () => void;
}) {
  const recipients = agent.recipients ?? [];
  const [account, setAccount] = useState<EmailAccount | null>(null);
  const [perms, setPerms] = useState<EmailPermissions | null>(null);
  const [recips, setRecips] = useState<RecipientVerification | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testTo, setTestTo] = useState("");
  const [test, setTest] = useState<TestEmailResult | null>(null);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(() => {
    api.emailAccount().then(setAccount).catch(() => setAccount(null));
    api.emailPermissions().then(setPerms).catch(() => setPerms(null));
    if (recipients.length) {
      api.recipientVerification(agent.agent_id).then(setRecips).catch(() => setRecips(null));
    } else {
      setRecips(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.agent_id, recipients.join(",")]);

  useEffect(load, [load]);

  const targets = [...new Set([...(senderEmail ? [senderEmail] : []), ...recipients])];
  useEffect(() => {
    if (!targets.includes(testTo)) setTestTo(targets[0] ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targets.join(",")]);

  if (!senderEmail) return null;

  async function act<T>(name: string, fn: () => Promise<T>): Promise<T | null> {
    setBusy(name);
    setError(null);
    setNotice(null);
    try {
      return await fn();
    } catch (e) {
      setError(describeError(e));
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function verifySender() {
    const out = await act("sender", () => api.verifySender(agent.agent_id));
    if (out) {
      setNotice(
        out.status === "verified"
          ? out.message
          : `${out.message} Check the inbox for ${out.address}, click the AWS link, then press Refresh status.`,
      );
      onSenderChanged();
    }
  }

  async function verifyRecipients() {
    const todo = (recips?.recipients ?? []).filter((r) => needsRequest(r.status)).map((r) => r.address);
    if (!todo.length) return;
    const ok = window.confirm(
      `AWS will email a verification link to ${todo.length} ${todo.length === 1 ? "person" : "people"}` +
        ` (${todo.slice(0, 5).join(", ")}${todo.length > 5 ? ", …" : ""}).\n\n` +
        "Each of them has to click it before they can receive this agent's email. Continue?",
    );
    if (!ok) return;
    let requested = 0;
    const failed: string[] = [];
    const done = await act("recipients", async () => {
      // The server sends up to 10 per call (SES allows ~1 per second); keep going.
      for (let i = 0; i < 100; i++) {
        setNotice(`Requesting verification emails… ${requested} of ${todo.length}`);
        const out = await api.verifyRecipients(agent.agent_id);
        requested += out.requested?.length ?? 0;
        failed.push(...(out.failed ?? []).map((f: { address: string }) => f.address));
        if (!out.remaining) break;
      }
      return true;
    });
    if (done) {
      setNotice(
        `AWS sent verification links to ${requested} ${requested === 1 ? "address" : "addresses"}.` +
          (failed.length ? ` Failed for: ${failed.join(", ")}.` : "") +
          " Statuses update as people click their links.",
      );
    }
    load();
  }

  async function sendTest() {
    const out = await act("test", () => api.sendTestEmail(agent.agent_id, testTo));
    if (out) setTest(out);
  }

  const verified = verification && verification !== "loading" ? verification.verified : null;
  const unverified = (recips?.recipients ?? []).filter((r) => r.status !== "verified");
  const requestable = unverified.filter((r) => needsRequest(r.status)).length;
  const shown = showAll ? unverified : unverified.slice(0, 8);

  return (
    <div className="email-delivery">
      <div className="delivery-row">
        <span className="field-label">Delivery</span>
        <button className="btn btn-ghost" type="button" disabled={!!busy} onClick={() => { onSenderChanged(); load(); }}>
          Refresh status
        </button>
      </div>

      {verified === false && (
        <div className="delivery-block">
          <p>
            <strong>{senderEmail}</strong> isn't verified in SES, so SES won't send as it.
          </p>
          <button className="btn btn-primary" type="button" disabled={!!busy} onClick={verifySender}>
            {busy === "sender" ? "Asking AWS…" : "Send verification email"}
          </button>
          <p className="muted mailing-list-hint">
            AWS emails {senderEmail} a link. Click it, then press Refresh status.
          </p>
        </div>
      )}

      {account?.sandbox === true && (
        <div className="sandbox-banner" role="status">
          <p>
            <strong>SES sandbox:</strong> only verified recipients receive mail
            {recips?.available
              ? ` — ${recips.verified} of ${recips.total} verified.`
              : recipients.length
                ? ". (Couldn't check which recipients are verified.)"
                : "."}{" "}
            Limit {account.max_24h ?? "?"} emails/day, {account.max_rate ?? "?"}/second
            {account.sent_last_24h != null ? `, ${account.sent_last_24h} sent in the last 24h` : ""}.
          </p>
          {recips?.available && unverified.length > 0 && (
            <>
              <ul className="recipient-status">
                {shown.map((r) => (
                  <li key={r.address}>
                    <code>{r.address}</code> <span className="muted">{STATUS_TEXT[r.status ?? ""] ?? "unknown"}</span>
                  </li>
                ))}
              </ul>
              {unverified.length > shown.length && (
                <button className="btn btn-ghost" type="button" onClick={() => setShowAll(true)}>
                  Show all {unverified.length}
                </button>
              )}
              {requestable > 0 && (
                <button className="btn btn-outline" type="button" disabled={!!busy} onClick={verifyRecipients}>
                  {busy === "recipients"
                    ? "Sending…"
                    : `Send verification emails to ${requestable} unverified recipient${requestable === 1 ? "" : "s"}`}
                </button>
              )}
            </>
          )}
          <p className="muted mailing-list-hint">
            To email anyone without verifying them, request production access in the{" "}
            <a href={account.console_url || "https://console.aws.amazon.com/ses/home?region=us-east-1#/account"} target="_blank" rel="noreferrer">
              SES console (Account dashboard → Request production access)
            </a>
            .
          </p>
        </div>
      )}
      {account && account.sandbox === null && account.reason && (
        <p className="muted mailing-list-hint">SES account: {account.reason}</p>
      )}

      <div className="delivery-row">
        <Permission label="Send" value={perms?.send} />
        <Permission label="Verify addresses" value={perms?.create_identity} />
      </div>
      {perms?.reason && <p className="muted mailing-list-hint">{perms.reason}</p>}

      <div className="delivery-block">
        <span className="field-label">Test send (the real proof)</span>
        <div className="upload-row">
          <select
            className="input test-select"
            value={testTo}
            onChange={(e) => setTestTo(e.target.value)}
            aria-label="Send the test email to"
          >
            {targets.map((t) => (
              <option key={t} value={t}>
                {t}
                {t === senderEmail ? " (sender)" : ""}
              </option>
            ))}
          </select>
          <button className="btn btn-outline" type="button" disabled={!!busy || !testTo} onClick={sendTest}>
            {busy === "test" ? "Sending…" : "Send test email"}
          </button>
        </div>
        <p className="muted mailing-list-hint">
          Sends one real email through SES right now, without the approval step (you clicked it).
        </p>
        {test && (
          <p className={test.ok ? "test-ok" : "alert alert-error"} role="status">
            {test.ok
              ? `Sent to ${test.to}. SES message id: ${test.message_id}`
              : `SES didn't send it: ${test.error}`}
          </p>
        )}
      </div>

      {error && (
        <p className="alert alert-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="muted mailing-list-hint">{notice}</p>}
    </div>
  );
}
