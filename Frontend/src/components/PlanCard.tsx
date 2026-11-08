import { useState } from "react";
import { api, type ChatPlan } from "../lib/api";
import { connectionMessage } from "../lib/errors";
import "../pages/Account.css";

/** The assistant's proposed plan: which helper agents it will use and exactly what each may do. Nothing runs until
 * the staffer approves here; the server wrote the card from the staffer's role, not from the model's words. */
export default function PlanCard({ sid, plan }: { sid: string; plan: ChatPlan }) {
  const [status, setStatus] = useState<ChatPlan["status"]>(plan.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "approve" | "cancel") {
    setBusy(true);
    setError(null);
    try {
      setStatus((await api.decidePlan(sid, plan.id, decision)).status);
    } catch (e) {
      setError(connectionMessage(e, false));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="connect-prompt plan-card" role="group" aria-label="Plan to approve">
      <p className="plan-title">To do this I'll use:</p>
      <ul className="plan-steps" style={{ margin: 0, paddingLeft: 18 }}>
        {plan.steps.map((step, i) => (
          <li key={i}>
            <strong>{step.label}</strong>
            {step.readOnly ? " — read-only" : ""}: {step.purpose}
            <ul>
              {step.tools.map((t) => (
                <li key={t.name}>
                  {t.label} <code>{t.name}</code>
                </li>
              ))}
              {step.dropped.map((name) => (
                <li key={name} className="muted">
                  <s>{name}</s> not allowed for your role
                </li>
              ))}
            </ul>
            {step.readOnly ? (
              <p className="muted">Can't send anything.</p>
            ) : (
              <p className="muted">It waits for your review; nothing goes out without you.</p>
            )}
          </li>
        ))}
      </ul>
      {status === "pending" ? (
        <div className="connect-prompt-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => decide("approve")}>
            Approve plan
          </button>
          <button type="button" className="btn btn-outline" disabled={busy} onClick={() => decide("cancel")}>
            Cancel
          </button>
        </div>
      ) : (
        <p className="plan-status" role="status">
          {status === "approved" ? "Approved" : "Cancelled"}
        </p>
      )}
      {error && <p className="alert alert-error" role="alert">{error}</p>}
    </div>
  );
}
