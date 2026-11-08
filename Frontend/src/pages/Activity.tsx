import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import StorageStatus from "../components/StorageStatus/StorageStatus";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/AuthProvider";
import {
  EVENT_LABELS,
  describeError,
  eventLabel,
  type Agent,
  type AuditEvent,
} from "../lib/agents";
import "./Agents.css";
import "./Activity.css";

const DECISIONS = ["allow", "deny", "revoke", "info"];
// URL search param -> API filter name.
const FILTERS = { agent: "agent_id", trace: "trace_id", decision: "decision", type: "type" } as const;
type FilterParam = keyof typeof FILTERS;

export default function Activity() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(true);

  const filters = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [param, apiName] of Object.entries(FILTERS)) {
      const v = params.get(param);
      if (v) out[apiName] = v;
    }
    return out;
  }, [params]);

  const setFilter = (param: FilterParam, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(param, value);
    else next.delete(param);
    setParams(next);
  };

  const load = useCallback(
    () =>
      api
        .audit(200, filters)
        .then((e: AuditEvent[]) => {
          setEvents(e);
          setError(null);
        })
        .catch((e: unknown) => setError(describeError(e))),
    [filters],
  );

  useEffect(() => {
    api.agents().then(setAgents).catch(() => {});
  }, []);

  useEffect(() => {
    load();
    if (!live) return;
    const id = window.setInterval(load, 4000);
    return () => window.clearInterval(id);
  }, [load, live]);

  const agentName = (id: string | null) =>
    (id && agents.find((a) => a.agent_id === id)?.name) || id || "—";

  const actorLabel = (actor?: string) => {
    if (!actor) return "—";
    if (user && actor === `user:${user.id}`) return "You";
    if (actor.startsWith("agent:")) return `Agent · ${agentName(actor.slice(6))}`;
    if (actor.startsWith("user:")) return `User ${actor.slice(5, 13)}…`;
    return actor === "anonymous" ? "Anonymous" : actor;
  };

  function download() {
    const blob = new Blob([JSON.stringify(events ?? [], null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `activity-${filters.trace_id || filters.agent_id || "all"}-${new Date().toISOString().slice(0, 19)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const traceFilter = params.get("trace");

  return (
    <Layout>
      <div className="agents-page activity-page">
        <div className="container">
          <div className="page-head">
            <div>
              <p className="eyebrow">Activity log</p>
              <h1>{traceFilter ? "Trace" : "Activity"}</h1>
              <p className="lede">
                Every agent change, token and tool call, with who did it and the policy decision. Click a
                trace to follow one run or session from start to finish.
              </p>
            </div>
            <StorageStatus />
          </div>

          <div className="activity-filters">
            <label>
              <span>Agent</span>
              <select value={params.get("agent") || ""} onChange={(e) => setFilter("agent", e.target.value)}>
                <option value="">All agents</option>
                {agents.map((a) => (
                  <option key={a.agent_id} value={a.agent_id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Decision</span>
              <select value={params.get("decision") || ""} onChange={(e) => setFilter("decision", e.target.value)}>
                <option value="">Any</option>
                {DECISIONS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Event</span>
              <select value={params.get("type") || ""} onChange={(e) => setFilter("type", e.target.value)}>
                <option value="">Any</option>
                {Object.entries(EVENT_LABELS).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            {traceFilter && (
              <button type="button" className="trace-chip is-active" onClick={() => setFilter("trace", null)}>
                Trace {traceFilter} ✕
              </button>
            )}
            <div className="activity-actions">
              <label className="live-toggle">
                <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
                Live
              </label>
              <button type="button" className="btn btn-outline" onClick={download} disabled={!events?.length}>
                Download JSON
              </button>
            </div>
          </div>

          {error && <p className="alert alert-error">Couldn't load activity: {error}</p>}
          {!events && !error && <p className="muted">Loading…</p>}
          {events && events.length === 0 && (
            <div className="empty">
              <p>No activity matches. Create an agent or authenticate one in Monitoring to see events here.</p>
            </div>
          )}

          {events && events.length > 0 && (
            <ol className="activity-list">
              {events.map((ev) => (
                <li key={ev.id} className={`activity-item dec-${ev.decision}`}>
                  <div className="activity-main">
                    <time dateTime={new Date(ev.ts * 1000).toISOString()}>
                      {new Date(ev.ts * 1000).toLocaleString()}
                    </time>
                    <span className={`decision decision-${ev.decision}`}>{ev.decision}</span>
                    <strong>{eventLabel(ev.type)}</strong>
                    {ev.tool && <code>{ev.tool}</code>}
                    <span className="activity-agent">{agentName(ev.agent_id)}</span>
                    <span className="activity-actor">by {actorLabel(ev.actor)}</span>
                  </div>
                  {(ev.detail || ev.atlas?.narrative) && (
                    <p className="activity-detail">{ev.detail || ev.atlas?.narrative}</p>
                  )}
                  <div className="activity-meta">
                    {ev.trace_id && (
                      <button
                        type="button"
                        className={`trace-chip${traceFilter === ev.trace_id ? " is-active" : ""}`}
                        onClick={() => setFilter("trace", ev.trace_id ?? null)}
                        title="Show only this run or session"
                      >
                        {ev.trace_id.startsWith("run_") ? "Run" : "Session"} {ev.trace_id}
                      </button>
                    )}
                    {(ev.atlas?.technique_ids || []).map((id) => (
                      <code key={id} className="tech">
                        {id}
                      </code>
                    ))}
                    {(ev.atlas?.mitigation_ids || []).map((id) => (
                      <code key={id} className="mit">
                        {id}
                      </code>
                    ))}
                    <details>
                      <summary>Raw</summary>
                      <pre>{JSON.stringify(ev, null, 2)}</pre>
                    </details>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </Layout>
  );
}
