import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import { agentsApi as api } from "../lib/agentsApi";
import { describeError, type Agent, type AgentKind, type AgentType } from "../lib/agents";
import {
  diffFromRecommended,
  initialSelection,
  isAvailable,
  type PermissionChange,
  type PermissionOption,
} from "../lib/permissions";
import "./Agents.css";

// The agent types users pick from. `custom` and the generic `research` type
// (no report.save, so its runs have no report to show) stay API-only; existing
// agents of those types keep working.
const KINDS: AgentKind[] = ["email", "social", "market_research"];

const KIND_ICON: Record<string, string> = { email: "✉", social: "◎", research: "⌕", market_research: "▤" };

export default function NewAgent() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [types, setTypes] = useState<AgentType[]>([]);
  const [options, setOptions] = useState<PermissionOption[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [agency, setAgency] = useState("");
  const [instructions, setInstructions] = useState("");
  const [grants, setGrants] = useState<string[]>([]);
  const [maxSteps, setMaxSteps] = useState(10);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const kind = params.get("kind") as AgentKind | null;
  const type = useMemo(() => types.find((t) => t.kind === kind), [types, kind]);

  useEffect(() => {
    api
      .agentTypes()
      .then(setTypes)
      .catch((e) => setLoadError(describeError(e)));
  }, []);

  // Load the template whenever the chosen type changes.
  useEffect(() => {
    if (!type) return;
    setInstructions(type.default_instructions);
    setMaxSteps(type.default_max_steps);
    setError(null);
  }, [type]);

  // The server's recommendation, not the type's default tools, is the starting selection.
  useEffect(() => {
    if (!kind) return;
    let stale = false;
    setOptions([]);
    setGrants([]);
    api
      .agentPermissions(kind)
      .then((permissions) => {
        if (stale) return;
        setOptions(permissions.tools);
        setGrants(initialSelection(permissions.tools));
      })
      .catch((e) => !stale && setLoadError(describeError(e)));
    return () => {
      stale = true;
    };
  }, [kind]);

  const choose = (k: AgentKind) => setParams({ kind: k }, { replace: true });

  const changes = useMemo(() => diffFromRecommended(options, grants), [options, grants]);

  // The grants never hold an unavailable tool, even if a change event reaches a disabled box.
  const toggleGrant = (option: PermissionOption) => {
    if (!isAvailable(option)) return;
    setGrants((cur) => (cur.includes(option.name) ? cur.filter((t) => t !== option.name) : [...cur, option.name]));
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!type) return;
    setSubmitting(true);
    setError(null);
    try {
      const agent: Agent = await api.createAgent({
        kind: type.kind,
        name: name.trim(),
        agency: agency.trim(),
        instructions,
        tool_grants: grants,
        max_steps: maxSteps,
      });
      navigate(`/monitoring?agent=${encodeURIComponent(agent.agent_id)}`);
    } catch (err) {
      setError(describeError(err, types));
      setSubmitting(false);
    }
  }

  return (
    <Layout>
      <div className="agents-page">
        <div className="container">
          <Link to="/agents" className="back-link">
            ← My agents
          </Link>
          <p className="eyebrow">New agent</p>
          <h1>What should this agent do?</h1>
          <p className="lede">
            Each type starts from a template and can only hold the tools that fit its job. A
            research agent reads untrusted web pages, so it can never send email or post.
          </p>

          {loadError && <p className="alert alert-error">Couldn't load agent types: {loadError}</p>}

          <div className="type-grid" role="radiogroup" aria-label="Agent type">
            {KINDS.map((k) => {
              const t = types.find((x) => x.kind === k);
              if (!t) return null;
              const selected = k === kind;
              return (
                <button
                  key={k}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  className={`type-card${selected ? " is-selected" : ""}`}
                  onClick={() => choose(k)}
                >
                  <span className={`type-icon kind-${k}`} aria-hidden="true">
                    {KIND_ICON[k]}
                  </span>
                  <strong>{t.label}</strong>
                  <span className="type-desc">{t.description}</span>
                  <span className="type-tools">
                    {(t.allowed_tools || []).map((tool) => (
                      <code key={tool}>{tool}</code>
                    ))}
                  </span>
                </button>
              );
            })}
          </div>

          {type && (
            <div className="agent-layout">
            <form className="agent-form" onSubmit={submit}>
              <div className="form-row">
                <label className="field">
                  <span className="field-label">Name</span>
                  <input
                    className="input"
                    required
                    maxLength={80}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder={`e.g. ${type.label} agent`}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Agency</span>
                  <input
                    className="input"
                    required
                    maxLength={120}
                    value={agency}
                    onChange={(e) => setAgency(e.target.value)}
                    placeholder="e.g. Department of Housing"
                  />
                </label>
              </div>

              <label className="field">
                <span className="field-label">Instructions</span>
                <span className="field-hint">
                  The agent's purpose and rules. Pre-filled from the {type.label.toLowerCase()} template.
                </span>
                <textarea
                  className="textarea"
                  rows={7}
                  maxLength={8000}
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                />
                {instructions !== type.default_instructions && (
                  <button
                    type="button"
                    className="btn btn-ghost reset"
                    onClick={() => setInstructions(type.default_instructions)}
                  >
                    Reset to template
                  </button>
                )}
              </label>

              <fieldset className="field tool-picker">
                <legend className="field-label">Tools</legend>
                <span className="field-hint">
                  The recommended tools are checked. Tools your role or this agent type can't hold are
                  greyed out. You can revoke any of them later, even mid-task.
                </span>
                {options.map((o) => (
                  <label key={o.name} className={`tool-option${isAvailable(o) ? "" : " is-disabled"}`}>
                    <input
                      type="checkbox"
                      checked={grants.includes(o.name)}
                      disabled={!isAvailable(o)}
                      onChange={() => toggleGrant(o)}
                    />
                    <span>
                      <strong>{o.label}</strong> <code>{o.name}</code>
                      <span className={`risk risk-${o.risk}`}>{o.risk} risk</span>
                      <span className="tool-desc">{o.effect}</span>
                      {o.reason_unavailable && <span className="tool-reason">{o.reason_unavailable}</span>}
                    </span>
                  </label>
                ))}
              </fieldset>

              <label className="field steps-field">
                <span className="field-label">Max steps per run</span>
                <span className="field-hint">Caps how many model turns one task can take (1–50).</span>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={50}
                  required
                  value={maxSteps}
                  onChange={(e) => setMaxSteps(Number(e.target.value))}
                />
              </label>

              {error && (
                <p className="alert alert-error" role="alert">
                  {error}
                </p>
              )}

              <div className="form-actions">
                <button className="btn btn-primary" type="submit" disabled={submitting}>
                  {submitting ? "Creating…" : `Create ${type.label.toLowerCase()} agent`}
                </button>
                <Link to="/agents" className="btn btn-outline">
                  Cancel
                </Link>
              </div>
            </form>
            <aside className="permission-panel" aria-live="polite" aria-label="How your choices change this agent">
              <h2>Your changes</h2>
              {changes.added.length + changes.removed.length === 0 ? (
                <p className="muted">This matches the recommended permissions.</p>
              ) : (
                <ul>
                  {changes.removed.map((c) => (
                    <ChangeItem key={c.name} change={c} kind="Removed" />
                  ))}
                  {changes.added.map((c) => (
                    <ChangeItem key={c.name} change={c} kind="Added" />
                  ))}
                </ul>
              )}
            </aside>
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}

function ChangeItem({ change, kind }: { change: PermissionChange; kind: "Added" | "Removed" }) {
  return (
    <li className="change">
      <strong>
        <span className="change-kind">{kind}</span> {change.label}
      </strong>
      <span className="change-effect">{change.effect}</span>
      <span className={`risk risk-${change.risk}`}>{change.risk} risk</span>
      {change.beyondRecommendation && <span className="change-note">Beyond the recommendation</span>}
      {change.review && <span className="change-note">{change.review}</span>}
    </li>
  );
}
