// Reports: every report your agents saved with report.save, newest first,
// with search and a document view. ?report=<id> opens one.
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import { ReportDocument, formatWhen } from "../components/ResearchOutput/ResearchOutput";
import { agentsApi as api } from "../lib/agentsApi";
import { describeError, type Agent, type Report } from "../lib/agents";
import "./Reports.css";

type Row = Report & { agentName: string };

const snippet = (body: string) =>
  body
    .replace(/[#>*_`|[\]()-]+/g, " ")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 140);

export default function Reports() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("report");

  useEffect(() => {
    let cancelled = false;
    api
      .agents()
      .then(async (list: Agent[]) => {
        const lists = await Promise.all(
          list.map((a) =>
            api
              .reports(a.agent_id)
              .then((rs: Report[]) => rs.map((r) => ({ ...r, agentName: a.name })))
              .catch(() => [] as Row[]),
          ),
        );
        if (cancelled) return;
        setAgents(list);
        setRows(lists.flat().sort((a, b) => b.created_at - a.created_at));
      })
      .catch((e) => !cancelled && setError(describeError(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (rows ?? []).filter(
      (r) =>
        (!agentFilter || r.agent_id === agentFilter) &&
        (!q || r.title.toLowerCase().includes(q) || r.body.toLowerCase().includes(q)),
    );
  }, [rows, query, agentFilter]);

  const selected = (rows ?? []).find((r) => r.report_id === selectedId) ?? filtered[0] ?? null;
  const agentsWithReports = agents.filter((a) => (rows ?? []).some((r) => r.agent_id === a.agent_id));

  const select = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("report", id);
    setParams(next, { replace: true });
  };

  return (
    <Layout>
      <div className="reports-page">
        <div className="container">
          <div className="page-head">
            <div>
              <p className="eyebrow">Research</p>
              <h1>Reports</h1>
              <p className="lede muted">
                Reports your research agents saved with <code>report.save</code>. Each one is tied to the run and
                agent identity that wrote it.
              </p>
            </div>
          </div>

          {error && <p className="alert alert-error">Couldn't load reports: {error}</p>}
          {!rows && !error && <p className="muted">Loading…</p>}

          {rows && rows.length === 0 && (
            <div className="empty">
              <p>No reports yet. Give a research agent the "save reports" tool and run a task, and its report shows up here.</p>
              <Link to="/agents" className="btn btn-outline">
                Go to my agents
              </Link>
            </div>
          )}

          {rows && rows.length > 0 && (
            <div className="reports-layout">
              <aside className="reports-list" aria-label="Saved reports">
                <div className="reports-filters">
                  <input
                    className="input"
                    type="search"
                    placeholder="Search reports"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    aria-label="Search reports"
                  />
                  {agentsWithReports.length > 1 && (
                    <select value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)} aria-label="Agent">
                      <option value="">All agents</option>
                      {agentsWithReports.map((a) => (
                        <option key={a.agent_id} value={a.agent_id}>
                          {a.name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                {filtered.length === 0 && <p className="muted">No reports match.</p>}
                <ul>
                  {filtered.map((r) => (
                    <li key={r.report_id}>
                      <button
                        type="button"
                        className={`report-item${selected?.report_id === r.report_id ? " is-current" : ""}`}
                        onClick={() => select(r.report_id)}
                      >
                        <span className="report-item-title">{r.title}</span>
                        <span className="report-item-meta">
                          {r.agentName} · {formatWhen(r.created_at)}
                        </span>
                        <span className="report-item-snippet">{snippet(r.body)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </aside>

              <div className="reports-view">
                {selected ? (
                  <ReportDocument key={selected.report_id} report={selected} agentName={selected.agentName} />
                ) : (
                  <p className="muted">Pick a report.</p>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}
