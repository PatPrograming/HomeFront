import { useEffect, useState, type CSSProperties } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import { agentsApi as api } from "../lib/agentsApi";
import { describeError, kindLabel, sendsEmail, type Agent, type AgentType } from "../lib/agents";
import EmailSetup, { NoEmailNote } from "./EmailSetup";
import RunTask from "./RunTask";
import PlatformIcon, { PLATFORMS } from "../components/PlatformIcons/PlatformIcons";
import "./Agents.css";

// The social platforms a social media agent is for, in the card's top right corner.
function PlatformBadges({ platforms }: { platforms?: string[] }) {
  const shown = PLATFORMS.filter((p) => platforms?.includes(p.id));
  if (shown.length === 0) return null;
  return (
    <ul className="platform-badges" aria-label={`Platforms: ${shown.map((p) => p.name).join(", ")}`}>
      {shown.map((p) => (
        <li key={p.id} title={p.name} style={{ background: p.color, "--glyph-cut": p.color } as CSSProperties}>
          <PlatformIcon name={p.id} size={14} />
        </li>
      ))}
    </ul>
  );
}

// Two clicks: Delete, then Confirm. Shared demo agents have no owner and can't be deleted.
function DeleteAgent({ agent, onDeleted }: { agent: Agent; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (agent.owner_id === null) return null;

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api.deleteAgent(agent.agent_id);
      onDeleted();
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  }

  if (!confirming) {
    return (
      <button type="button" className="btn btn-ghost danger" onClick={() => setConfirming(true)}>
        Delete agent
      </button>
    );
  }
  return (
    <div className="delete-confirm" role="alertdialog" aria-label={`Delete ${agent.name}`}>
      <p>
        Delete <strong>{agent.name}</strong>? Its access stops at once and any drafts waiting for approval are
        rejected. This can't be undone.
      </p>
      {error && (
        <p className="alert alert-error" role="alert">
          {error}
        </p>
      )}
      <div className="form-actions">
        <button type="button" className="btn btn-primary danger-solid" disabled={busy} onClick={remove}>
          {busy ? "Deleting…" : "Yes, delete"}
        </button>
        <button type="button" className="btn btn-outline" disabled={busy} onClick={() => setConfirming(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export default function Agents() {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [types, setTypes] = useState<AgentType[]>([]);
  const [error, setError] = useState<string | null>(null);
  // ?edit=<agent id>&section=email (or /agents/<id>/email) opens that agent's email setup.
  const [params] = useSearchParams();
  const editId = params.get("section") === "email" ? params.get("edit") : null;

  useEffect(() => {
    if (!editId || !agents) return;
    document.getElementById(`email-${editId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [editId, agents]);

  useEffect(() => {
    Promise.all([api.agents(), api.agentTypes()])
      .then(([a, t]) => {
        setAgents(a);
        setTypes(t);
      })
      .catch((e) => setError(describeError(e)));
  }, []);

  return (
    <Layout>
      <div className="agents-page">
        <div className="container">
          <div className="page-head">
            <div>
              <p className="eyebrow">Agents</p>
              <h1>My agents</h1>
            </div>
            <Link to="/agents/new" className="btn btn-primary">
              New agent
            </Link>
          </div>

          {error && <p className="alert alert-error">Couldn't load agents: {error}</p>}
          {!agents && !error && <p className="muted">Loading…</p>}

          {agents && agents.length === 0 && (
            <div className="empty">
              <p>No agents yet.</p>
              <Link to="/agents/new" className="btn btn-outline">
                Create your first agent
              </Link>
            </div>
          )}

          {agents && agents.length > 0 && (
            <ul className="agent-list">
              {agents.map((a) => (
                <li key={a.agent_id} className="agent-card">
                  <div className="agent-card-head">
                    <span className={`kind-badge kind-${a.kind}`}>{kindLabel(types, a.kind)}</span>
                    {a.owner_id === null && <span className="demo-badge">Shared demo</span>}
                    {!a.active && <span className="demo-badge">Inactive</span>}
                  </div>
                  <PlatformBadges platforms={a.platforms} />
                  <h2>{a.name}</h2>
                  <p className="muted">{a.agency}</p>
                  <div className="type-tools">
                    {(a.tool_grants ?? []).length ? (
                      a.tool_grants.map((t) => <code key={t}>{t}</code>)
                    ) : (
                      <span className="muted">No tools granted</span>
                    )}
                  </div>
                  {sendsEmail(a) ? (
                    <EmailSetup
                      agent={a}
                      defaultOpen={a.agent_id === editId}
                      onSaved={(updated) =>
                        setAgents((list) => list && list.map((x) => (x.agent_id === updated.agent_id ? updated : x)))
                      }
                    />
                  ) : (
                    <NoEmailNote agent={a} types={types} />
                  )}
                  <RunTask agent={a} collapsible />
                  <Link
                    to={`/monitoring?agent=${encodeURIComponent(a.agent_id)}`}
                    className="btn btn-outline"
                  >
                    Open in Monitoring
                  </Link>
                  <DeleteAgent
                    agent={a}
                    onDeleted={() => setAgents((list) => list && list.filter((x) => x.agent_id !== a.agent_id))}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Layout>
  );
}
