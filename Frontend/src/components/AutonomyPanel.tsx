import { useState } from "react";
import { api, type AutonomyCategory } from "../lib/api";

// What the agent may send without asking, per kind of message. The agent earns this: after enough drafts
// approved unchanged, the panel offers to let it handle that kind alone. The staffer always decides, and the
// gateway enforces the answer on every send.
export default function AutonomyPanel({ categories, onChange }: { categories: AutonomyCategory[]; onChange: () => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const offers = categories.filter((c) => c.suggestAuto);

  async function act(category: string, action: () => Promise<unknown>) {
    setBusy(category);
    setError(null);
    try {
      await action();
      await onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel" aria-labelledby="autonomy-title">
      {offers.map((c) => (
        <div key={c.category} className="offer" role="status">
          <p>
            You sent my last <strong>{c.approvedUnchanged}</strong> drafts for <strong>{c.label.toLowerCase()}</strong>{" "}
            without changes. Should I send these myself from now on?
          </p>
          <div className="draft-actions">
            <button type="button" className="btn-primary" disabled={busy === c.category} onClick={() => act(c.category, () => api.setAutonomy(c.category, "auto"))}>
              Let it send these
            </button>
            <button type="button" className="btn-plain" disabled={busy === c.category} onClick={() => act(c.category, () => api.snoozeAutonomy(c.category))}>
              Not now
            </button>
          </div>
        </div>
      ))}

      <div className="panel-head">
        <h2 id="autonomy-title">What it can send without asking</h2>
      </div>
      <ul className="autonomy">
        {categories.map((c) => {
          const reviewed = c.approvedUnchanged + c.edited + c.rejected;
          return (
            <li key={c.category} className="autonomy-row">
              <div>
                <p className="autonomy-label">{c.label}</p>
                <p className="muted autonomy-stats">
                  {c.locked
                    ? "Always needs your review"
                    : reviewed
                      ? `${c.approvedUnchanged} sent as drafted · ${c.edited} edited · ${c.rejected} rejected`
                      : "No drafts reviewed yet"}
                </p>
              </div>
              {c.locked ? (
                <span className="chip chip-locked">Always asks</span>
              ) : (
                <button
                  type="button"
                  role="switch"
                  aria-checked={c.level === "auto"}
                  aria-label={`${c.label}: ${c.level === "auto" ? "sends without asking" : "needs your review"}`}
                  className={`level-switch ${c.level === "auto" ? "is-auto" : ""}`}
                  disabled={busy === c.category}
                  onClick={() => act(c.category, () => api.setAutonomy(c.category, c.level === "auto" ? "review" : "auto"))}
                >
                  {c.level === "auto" ? "Sends alone" : "Asks first"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
