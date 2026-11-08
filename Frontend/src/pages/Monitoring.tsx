import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AtlasChips, collectAtlasIds } from "../components/AtlasChips";
import Layout from "../components/Layout";
import StorageStatus from "../components/StorageStatus/StorageStatus";
import { ApiError } from "../lib/api";
import { agentsApi as api } from "../lib/agentsApi";
import {
  demoArgsForTool,
  describeError,
  kindLabel,
  pickFocusTool,
  sendsEmail,
  typeAllows,
  toolAction,
  type Agent,
  type AgentType,
  type AuditEvent,
  type Tool,
} from "../lib/agents";
import { emailSetupPath } from "./EmailSetup";
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

type StepId = "authenticate" | "allow" | "revoke" | "block";
type StepState = "locked" | "active" | "done";

const STEPS: { id: StepId; label: string; blurb: string }[] = [
  { id: "authenticate", label: "Authenticate", blurb: "Issue a workload token" },
  { id: "allow", label: "Allow tool", blurb: "Call a granted MCP tool" },
  { id: "revoke", label: "Revoke", blurb: "Pull the grant mid-task" },
  { id: "block", label: "Block", blurb: "Next call is denied" },
];

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function atlasFromError(e: unknown): { technique_ids?: string[]; mitigation_ids?: string[]; narrative?: string } {
  if (!(e instanceof ApiError)) return {};
  const detail = (e as ApiError & { data?: { detail?: unknown } }).data?.detail;
  if (!detail || typeof detail !== "object") return {};
  const d = detail as {
    atlas?: { technique_ids?: string[]; mitigation_ids?: string[]; narrative?: string };
    technique_ids?: string[];
    mitigation_ids?: string[];
    message?: string;
  };
  return {
    technique_ids: d.atlas?.technique_ids || d.technique_ids,
    mitigation_ids: d.atlas?.mitigation_ids || d.mitigation_ids,
    narrative: d.atlas?.narrative || d.message,
  };
}

export default function Monitoring() {
  const [params, setParams] = useSearchParams();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [types, setTypes] = useState<AgentType[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [authProtocol, setAuthProtocol] = useState<string | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState<unknown>(null);
  const [atlasVersion, setAtlasVersion] = useState("");
  const [focusTool, setFocusTool] = useState<string | null>(null);
  const [allowedOnce, setAllowedOnce] = useState(false);
  const [revokedFocus, setRevokedFocus] = useState(false);
  const [blockedOnce, setBlockedOnce] = useState(false);
  const [runOpen, setRunOpen] = useState(false);

  const selectedId = params.get("agent") || DEMO_AGENT;
  const agent = useMemo(
    () =>
      agents.find((a) => a.agent_id === selectedId) ||
      agents.find((a) => a.agent_id === DEMO_AGENT) ||
      agents[0],
    [agents, selectedId],
  );
  const agentType = useMemo(() => types.find((t) => t.kind === agent?.kind), [types, agent?.kind]);

  const usableTools = useMemo(() => {
    if (!agent) return [];
    return tools.filter((t) => typeAllows(agentType, t.name));
  }, [agent, agentType, tools]);

  // Reset Prompt 1 session only when the selected agent changes — not when
  // refresh() replaces the tools catalog (that was bouncing step 1 after auth).
  useEffect(() => {
    setToken(null);
    setSessionId(null);
    setAuthProtocol(null);
    setLastResult(null);
    setAllowedOnce(false);
    setRevokedFocus(false);
    setBlockedOnce(false);
    setFlash(null);
    setFocusTool(null);
  }, [agent?.agent_id]);

  // Auto-pick only when unset (new agent). Never replace mid-run: after revoke the
  // focus tool leaves grants and a poll/refresh was re-picking (e.g. nasa.search).
  const grantKey = (agent?.tool_grants || []).join(",");
  useEffect(() => {
    if (!agent || focusTool) return;
    setFocusTool(pickFocusTool(agent, tools));
  }, [agent, grantKey, focusTool, tools]);

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

  const stepStates = useMemo((): Record<StepId, StepState> => {
    const authDone = !!token;
    const allowDone = allowedOnce;
    const revokeDone = revokedFocus;
    const blockDone = blockedOnce;
    return {
      authenticate: authDone ? "done" : "active",
      allow: !authDone ? "locked" : allowDone ? "done" : "active",
      revoke: !allowDone ? "locked" : revokeDone ? "done" : "active",
      block: !revokeDone ? "locked" : blockDone ? "done" : "active",
    };
  }, [token, allowedOnce, revokedFocus, blockedOnce]);

  const allDone = Object.values(stepStates).every((s) => s === "done");
  const activeStep =
    (Object.entries(stepStates).find(([, s]) => s === "active")?.[0] as StepId | undefined) ||
    (allDone ? "block" : "authenticate");

  const sessionAudit = useMemo(() => {
    if (!agent) return [];
    return audit.filter((ev) => {
      if (ev.agent_id !== agent.agent_id) return false;
      // Prefer the live token session for auth/tool rows; keep grant revoke/restore for context.
      if (sessionId && (ev.type === "tool_call" || ev.type === "authenticate")) {
        return !ev.trace_id || ev.trace_id === sessionId;
      }
      return true;
    });
  }, [audit, agent, sessionId]);

  const contextualAtlas = useMemo(() => {
    const fromFlash = flash
      ? [{ atlas: { technique_ids: flash.technique_ids, mitigation_ids: flash.mitigation_ids } }]
      : [];
    return collectAtlasIds([...fromFlash, ...sessionAudit.slice(0, 12)]);
  }, [flash, sessionAudit]);

  async function authenticate() {
    if (!agent) return;
    setBusy(true);
    try {
      const res = await api.mintAgentToken(agent.agent_id);
      setToken(res.access_token);
      setSessionId(res.session_id || null);
      setAuthProtocol(res.auth_protocol || null);
      setAllowedOnce(false);
      setRevokedFocus(false);
      setBlockedOnce(false);
      setFlash({
        kind: "ok",
        title: res.auth_protocol === "aauth" ? "Agent authenticated (AAuth)" : "Agent authenticated",
        body: res.atlas?.narrative || "Workload token issued.",
        technique_ids: res.atlas?.technique_ids,
        mitigation_ids: res.atlas?.mitigation_ids || ["AML.M0028", "AML.M0024"],
      });
      await refresh();
    } catch (e) {
      setFlash({ kind: "deny", title: "Auth failed", body: describeError(e, types) });
    } finally {
      setBusy(false);
    }
  }

  async function runFocusTool() {
    if (!agent || !token || !focusTool) {
      setFlash({
        kind: "info",
        title: "Pick a focus tool",
        body: token ? "This agent has no tool grants yet — restore one first." : "Authenticate first.",
      });
      return;
    }
    setBusy(true);
    try {
      const res = await api.callTool(token, focusTool, demoArgsForTool(focusTool, agent));
      setLastResult(res.result);
      setAllowedOnce(true);
      if (res.decision === "pending_approval") {
        setFlash({
          kind: "info",
          title: `${focusTool} waiting for approval`,
          body: "Granted path held for a person (AML.M0029). Approve it, then continue to revoke.",
          link: { to: "/approvals", label: "Open Approvals" },
          mitigation_ids: res.atlas?.mitigation_ids || ["AML.M0029", "AML.M0028"],
          technique_ids: res.atlas?.technique_ids,
        });
      } else {
        setFlash({
          kind: "ok",
          title: `${focusTool} allowed`,
          body: res.atlas?.narrative || `Tool permitted under current grants.`,
          technique_ids: res.atlas?.technique_ids,
          mitigation_ids: res.atlas?.mitigation_ids || ["AML.M0028", "AML.M0024"],
        });
      }
      await refresh();
    } catch (e) {
      const ax = atlasFromError(e);
      setFlash({
        kind: "deny",
        title: `${focusTool} blocked`,
        body: describeError(e, types),
        technique_ids: ax.technique_ids,
        mitigation_ids: ax.mitigation_ids,
      });
    } finally {
      setBusy(false);
    }
  }

  async function revokeFocus() {
    if (!agent || !focusTool) return;
    setBusy(true);
    try {
      const res = await api.revokeTool(agent.agent_id, focusTool);
      setRevokedFocus(true);
      setFlash({
        kind: "ok",
        title: `Revoked ${focusTool}`,
        body: res.message || res.atlas?.narrative || "Grant revoked mid-task.",
        technique_ids: res.atlas?.technique_ids || ["AML.T0053"],
        mitigation_ids: res.atlas?.mitigation_ids || ["AML.M0028", "AML.M0024"],
      });
      await refresh();
    } catch (e) {
      setFlash({ kind: "deny", title: "Revoke failed", body: describeError(e, types) });
    } finally {
      setBusy(false);
    }
  }

  async function retryBlocked() {
    if (!agent || !token || !focusTool) return;
    setBusy(true);
    try {
      const res = await api.callTool(token, focusTool, demoArgsForTool(focusTool, agent));
      // Unexpected allow after revoke — still surface it; do not mark Block done.
      setLastResult(res.result);
      setFlash({
        kind: "info",
        title: "Call was allowed",
        body: "Grant may have been restored. Revoke again to demo the block.",
        technique_ids: res.atlas?.technique_ids,
        mitigation_ids: res.atlas?.mitigation_ids,
      });
    } catch (e) {
      const ax = atlasFromError(e);
      // Any deny/401 after revoke counts as the Prompt 1 "block subsequent" beat.
      setBlockedOnce(true);
      setLastResult(null);
      setFlash({
        kind: "deny",
        title: "Subsequent request blocked",
        body: describeError(e, types) || ax.narrative || "Tool call denied after revoke.",
        technique_ids: ax.technique_ids || ["AML.T0053"],
        mitigation_ids: ax.mitigation_ids || ["AML.M0028", "AML.M0024"],
      });
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function restore(tool: string) {
    if (!agent) return;
    setBusy(true);
    try {
      await api.grantTool(agent.agent_id, tool);
      if (tool === focusTool) setRevokedFocus(false);
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

  async function revoke(tool: string) {
    if (!agent) return;
    setBusy(true);
    try {
      const res = await api.revokeTool(agent.agent_id, tool);
      if (tool === focusTool) setRevokedFocus(true);
      setFlash({
        kind: "ok",
        title: `Revoked ${tool}`,
        body: res.message || "Grant revoked.",
        technique_ids: res.atlas?.technique_ids || ["AML.T0053"],
        mitigation_ids: res.atlas?.mitigation_ids || ["AML.M0028", "AML.M0024"],
      });
      await refresh();
    } catch (e) {
      setFlash({ kind: "deny", title: "Revoke failed", body: describeError(e, types) });
    } finally {
      setBusy(false);
    }
  }

  const focusGranted = !!(agent && focusTool && agent.tool_grants.includes(focusTool));

  return (
    <Layout>
      <div className="monitoring-console">
        <div className="shell">
          <header className="top">
            <div className="brand-block">
              <p className="eyebrow">Prompt 1 · Agentic identity</p>
              <h1>Monitoring</h1>
              <p className="tagline">
                Authenticate an agent, allow selected MCP tools, revoke mid-task and block what comes next — MITRE
                ATLAS on every step.
              </p>
            </div>
            <div className="meta">
              <span className="pill">ATLAS {atlasVersion || "…"}</span>
              <span className={`pill ${token ? "on" : ""}`}>{token ? "Token live" : "Unauthenticated"}</span>
              {authProtocol && (
                <span className={`pill ${authProtocol === "aauth" ? "on" : ""}`}>
                  {authProtocol === "aauth" ? "AAuth PoP" : authProtocol}
                </span>
              )}
              <StorageStatus compact />
            </div>
          </header>

          <div className="agent-bar panel">
            <div className="agent-picker">
              <label htmlFor="agent-select">Agent</label>
              <select
                id="agent-select"
                value={agent?.agent_id || ""}
                onChange={(e) => setParams({ agent: e.target.value })}
                disabled={!agents.length}
              >
                {agents.map((a) => (
                  <option key={a.agent_id} value={a.agent_id}>
                    {a.name} · {kindLabel(types, a.kind)}
                  </option>
                ))}
              </select>
              <Link to="/agents/new">New agent</Link>
              {agent && sendsEmail(agent) && (
                <Link to={emailSetupPath(agent.agent_id)}>Email setup</Link>
              )}
            </div>
            {agent && (
              <dl className="agent-meta">
                <div>
                  <dt>Type</dt>
                  <dd>
                    <span className={`kind-badge kind-${agent.kind}`}>{kindLabel(types, agent.kind)}</span>
                  </dd>
                </div>
                <div>
                  <dt>Agency</dt>
                  <dd>{agent.agency}</dd>
                </div>
                <div>
                  <dt>Token v</dt>
                  <dd>{agent.token_version}</dd>
                </div>
              </dl>
            )}
          </div>

          <ol className="prompt1-stepper" aria-label="Prompt 1 demo steps">
            {STEPS.map((s, i) => {
              const state = stepStates[s.id];
              return (
                <li
                  key={s.id}
                  className={`step step-${state}${s.id === activeStep && state !== "done" ? " is-current" : ""}`}
                >
                  <span className="step-num">{i + 1}</span>
                  <div>
                    <strong>{s.label}</strong>
                    <p>{s.blurb}</p>
                  </div>
                </li>
              );
            })}
          </ol>

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
              <AtlasChips
                techniqueIds={flash.technique_ids}
                mitigationIds={flash.mitigation_ids}
                techniques={matrix?.techniques}
                mitigations={matrix?.mitigations}
              />
            </div>
          )}

          {(contextualAtlas.techniqueIds.length > 0 || contextualAtlas.mitigationIds.length > 0) && (
            <div className="atlas-context panel">
              <h2>ATLAS in this demo</h2>
              <p className="hint">Techniques and mitigations touched by the current agent session.</p>
              <AtlasChips
                techniqueIds={contextualAtlas.techniqueIds}
                mitigationIds={contextualAtlas.mitigationIds}
                techniques={matrix?.techniques}
                mitigations={matrix?.mitigations}
              />
            </div>
          )}

          <div className="grid">
            <section className="panel step-panel">
              <h2>
                Step {STEPS.findIndex((s) => s.id === activeStep) + 1}: {STEPS.find((s) => s.id === activeStep)?.label}
              </h2>

              {activeStep === "authenticate" && (
                <>
                  <p className="hint">Mint a workload identity for {agent?.name || "this agent"}.</p>
                  <button className="primary" type="button" disabled={busy || !agent} onClick={authenticate}>
                    Authenticate agent
                  </button>
                  <AtlasChips
                    mitigationIds={["AML.M0028", "AML.M0024"]}
                    mitigations={matrix?.mitigations}
                    compact
                  />
                </>
              )}

              {activeStep === "allow" && (
                <>
                  <p className="hint">
                    Call a tool this agent is granted. Focus tool drives the rest of the Prompt 1 path.
                  </p>
                  <div className="focus-row">
                    <label htmlFor="focus-tool">Focus tool</label>
                    <select
                      id="focus-tool"
                      value={focusTool || ""}
                      onChange={(e) => {
                        setFocusTool(e.target.value || null);
                        setAllowedOnce(false);
                        setRevokedFocus(false);
                        setBlockedOnce(false);
                      }}
                    >
                      {!focusTool && <option value="">No grants</option>}
                      {Array.from(
                        new Set([...(agent?.tool_grants || []), ...(focusTool ? [focusTool] : [])]),
                      ).map((name) => (
                        <option key={name} value={name}>
                          {name} — {toolAction(name)}
                          {agent && !agent.tool_grants.includes(name) ? " (revoked)" : ""}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    className="primary"
                    type="button"
                    disabled={busy || !token || !focusTool || !focusGranted}
                    onClick={runFocusTool}
                  >
                    Allow {focusTool || "tool"}
                  </button>
                  <AtlasChips
                    mitigationIds={["AML.M0028", "AML.M0024"]}
                    mitigations={matrix?.mitigations}
                    compact
                  />
                </>
              )}

              {activeStep === "revoke" && (
                <>
                  <p className="hint">
                    Revoke <code>{focusTool}</code> while the token is still live — mid-task revoke.
                  </p>
                  <button className="danger" type="button" disabled={busy || !focusTool || !focusGranted} onClick={revokeFocus}>
                    Revoke {focusTool}
                  </button>
                  <AtlasChips
                    techniqueIds={["AML.T0053"]}
                    mitigationIds={["AML.M0028", "AML.M0024"]}
                    techniques={matrix?.techniques}
                    mitigations={matrix?.mitigations}
                    compact
                  />
                </>
              )}

              {activeStep === "block" && (
                <>
                  <p className="hint">
                    Retry <code>{focusTool}</code> with the same token. The gateway should deny it (AML.T0053).
                  </p>
                  <button className="primary" type="button" disabled={busy || !token || !focusTool} onClick={retryBlocked}>
                    Retry {focusTool}
                  </button>
                  <AtlasChips
                    techniqueIds={["AML.T0053"]}
                    mitigationIds={["AML.M0028", "AML.M0024"]}
                    techniques={matrix?.techniques}
                    mitigations={matrix?.mitigations}
                    compact
                  />
                </>
              )}

              {allDone && (
                <p className="hint done-note">
                  Prompt 1 complete for this agent. Restore the grant or switch agents to run again.
                </p>
              )}

              <h3>Tool grants</h3>
              <ul className="grants compact-grants">
                {usableTools.map((t) => {
                  const granted = !!agent?.tool_grants.includes(t.name);
                  return (
                    <li key={t.name} className={t.name === focusTool ? "is-focus" : ""}>
                      <div>
                        <code>{t.name}</code>
                        <span className={`risk risk-${t.risk_level}`}>{t.risk_level}</span>
                        <p>{t.description}</p>
                      </div>
                      <div className="grant-actions">
                        <span className={granted ? "yes" : "no"}>{granted ? "granted" : "revoked"}</span>
                        {granted ? (
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
                {usableTools.length === 0 && <li>No tools available for this agent type.</li>}
              </ul>

              {lastResult != null && (
                <>
                  <h3>Last tool result</h3>
                  <pre className="result">{JSON.stringify(lastResult, null, 2)}</pre>
                </>
              )}
            </section>

            <section className="panel audit">
              <div className="audit-title">
                <h2>Session audit · AML.M0024</h2>
                <div className="audit-links">
                  {sessionId && (
                    <Link to={`/activity?trace=${encodeURIComponent(sessionId)}`}>Trace session</Link>
                  )}
                  {agent && (
                    <Link to={`/activity?agent=${encodeURIComponent(agent.agent_id)}`}>Full log</Link>
                  )}
                </div>
              </div>
              <ul className="audit-list">
                {sessionAudit.map((ev) => (
                  <li key={ev.id} className={`dec-${ev.decision}`}>
                    <div className="audit-head">
                      <span className="decision">{ev.decision}</span>
                      <span>{ev.type}</span>
                      <code>{ev.tool || "—"}</code>
                      {ev.auth_protocol && <code className="tech">{ev.auth_protocol}</code>}
                      <time>{new Date(ev.ts * 1000).toLocaleTimeString()}</time>
                    </div>
                    <p>{ev.atlas?.narrative}</p>
                    <AtlasChips
                      techniqueIds={ev.atlas?.technique_ids}
                      mitigationIds={ev.atlas?.mitigation_ids}
                      techniques={matrix?.techniques}
                      mitigations={matrix?.mitigations}
                      compact
                    />
                  </li>
                ))}
                {sessionAudit.length === 0 && <li>No events yet — authenticate to begin.</li>}
              </ul>
            </section>
          </div>

          <section className="panel run-panel">
            <button type="button" className="run-toggle" onClick={() => setRunOpen((o) => !o)} aria-expanded={runOpen}>
              {runOpen ? "Hide" : "Show"} Bedrock task runner
            </button>
            {runOpen && (
              <>
                <p className="hint">
                  Optional: give the agent a free-form task. Outbound sends still wait on{" "}
                  <Link to="/approvals">Approvals</Link>.
                </p>
                {agent ? <RunTask key={agent.agent_id} agent={agent} /> : <p>Loading agent…</p>}
              </>
            )}
          </section>
        </div>
      </div>
    </Layout>
  );
}
