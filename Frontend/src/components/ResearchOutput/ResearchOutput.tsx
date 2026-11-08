// How a run's research is shown: the saved report, the agent's summary, and
// the sources it consulted, as tabs under "Run a task". ReportDocument is
// also used by the Reports page.
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Markdown, { extractLinks, outline } from "../Markdown/Markdown";
import { api } from "../../lib/api.js";
import { TERMINAL_RUN_STATUSES, type Agent, type Report, type Run, type RunStep } from "../../lib/agents";
import "./ResearchOutput.css";

// ---------- helpers ----------

const hostOf = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

const isOfficial = (url: string) => /\.(gov|mil)$/i.test(hostOf(url));

const fileName = (title: string) =>
  `${
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 60) || "report"
  }.md`;

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// Print only this element: the print stylesheet hides everything else while
// <body> has .printing-report.
function printElement(el: HTMLElement | null) {
  if (!el) return;
  el.classList.add("print-target");
  document.body.classList.add("printing-report");
  const done = () => {
    el.classList.remove("print-target");
    document.body.classList.remove("printing-report");
  };
  window.addEventListener("afterprint", done, { once: true });
  window.print();
}

export const formatWhen = (ts: number) =>
  new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

// ---------- cited links ----------

export function CitedLinks({ source }: { source: string }) {
  const links = useMemo(() => extractLinks(source), [source]);
  if (links.length === 0) return null;
  const official = links.filter(isOfficial).length;
  return (
    <section className="cited-links" aria-label="Links cited">
      <h3 className="ro-subhead">
        Links cited <span className="muted">· {links.length} total, {official} official (.gov / .mil)</span>
      </h3>
      <ul>
        {links.map((url) => (
          <li key={url}>
            <span className={`src-badge ${isOfficial(url) ? "src-official" : "src-external"}`}>
              {isOfficial(url) ? "Official" : "External"}
            </span>
            <a href={url} target="_blank" rel="noopener noreferrer nofollow">
              {hostOf(url)}
            </a>
            <span className="cited-path">{url.replace(/^https?:\/\/[^/]+/, "") || "/"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ---------- report ----------

export function ReportDocument({
  report,
  agentName,
  showOpenLink = false,
}: {
  report: Report;
  agentName?: string;
  showOpenLink?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [el, setEl] = useState<HTMLElement | null>(null);
  // Agents often repeat the title as the body's first "# " heading; the header already shows it.
  const body = useMemo(
    () =>
      report.body.replace(/^\s*#\s+(.+?)\s*#*\s*(\n|$)/, (line, heading: string) =>
        heading.trim().toLowerCase() === report.title.trim().toLowerCase() ? "" : line,
      ),
    [report.body, report.title],
  );
  const toc = useMemo(() => outline(body).filter((h) => h.level <= 3), [body]);
  const idPrefix = report.report_id;
  const markdown = `# ${report.title}\n\n${report.body}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the Download button still works */
    }
  }

  return (
    <article className="report-doc" ref={setEl}>
      <header className="report-head">
        <div className="report-title-row">
          <h2 className="report-title">{report.title}</h2>
          <div className="report-actions">
            <button type="button" className="btn btn-ghost ro-btn" onClick={copy}>
              {copied ? "Copied" : "Copy markdown"}
            </button>
            <button type="button" className="btn btn-ghost ro-btn" onClick={() => download(fileName(report.title), markdown)}>
              Download .md
            </button>
            <button type="button" className="btn btn-ghost ro-btn" onClick={() => printElement(el)}>
              Print / PDF
            </button>
            {showOpenLink && (
              <Link className="btn btn-ghost ro-btn" to={`/reports?report=${encodeURIComponent(report.report_id)}`}>
                Open in Reports
              </Link>
            )}
          </div>
        </div>
        <p className="report-meta">
          {agentName ? `${agentName} · ` : ""}Saved {formatWhen(report.created_at)}
          {report.run_id ? ` · Run ${report.run_id}` : ""}
        </p>
        <p className="report-provenance">
          Written by an AI agent from public sources. Check the facts and links before you rely on them.
        </p>
      </header>

      {toc.length >= 3 && (
        <nav className="report-toc" aria-label="Contents">
          <span className="ro-subhead">Contents</span>
          <ol>
            {toc.map((h, i) => (
              <li key={i} className={`toc-l${h.level}`}>
                <a href={`#${idPrefix}-${h.slug}`}>{h.text}</a>
              </li>
            ))}
          </ol>
        </nav>
      )}

      <Markdown source={body} idPrefix={idPrefix} className="report-body" />
      <CitedLinks source={body} />
    </article>
  );
}

// ---------- sources consulted ----------

// Tools whose results come from outside the agency (ToolDefinition.untrusted_input).
const UNTRUSTED_TOOLS = new Set(["web.scrape", "usaspending.search", "sam.entity_lookup"]);

interface SourceRow {
  tool: string;
  label: string;
  detail: string;
  href: string | null;
  decision: string;
  message?: string;
}

const str = (v: unknown) => (typeof v === "string" ? v : v === undefined || v === null ? "" : String(v));

function sourceOf(step: RunStep): SourceRow | null {
  if (step.type !== "tool_call" || !step.tool) return null;
  const input = step.input ?? {};
  const base = { tool: step.tool, decision: step.decision || "unknown", message: step.message || step.error };
  switch (step.tool) {
    case "web.scrape": {
      const url = str(input.url);
      return { ...base, label: "Web page", detail: url, href: /^https?:\/\//i.test(url) ? url : null };
    }
    case "usaspending.search": {
      const fy = Number(input.fiscal_year) || 0;
      return {
        ...base,
        label: "USAspending awards",
        detail: `“${str(input.keywords)}” · ${fy ? `FY${fy}` : "last 5 fiscal years"}`,
        href: "https://www.usaspending.gov/search",
      };
    }
    case "sam.entity_lookup":
      return { ...base, label: "SAM.gov entities", detail: `“${str(input.name)}”`, href: "https://sam.gov/" };
    case "datagov.search": {
      const q = str(input.q);
      return {
        ...base,
        label: "data.gov datasets",
        detail: `“${q}”`,
        href: `https://catalog.data.gov/dataset?q=${encodeURIComponent(q)}`,
      };
    }
    default:
      return null;
  }
}

export function SourcesConsulted({ run }: { run: Run }) {
  const rows = useMemo(() => {
    const seen = new Set<string>();
    const out: SourceRow[] = [];
    for (const step of run.steps ?? []) {
      const row = sourceOf(step);
      if (!row) continue;
      const key = `${row.tool}|${row.detail}|${row.decision}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(row);
    }
    return out;
  }, [run.steps]);

  const blocked = (run.steps ?? []).filter((s) => s.type === "tool_call" && s.decision === "deny");

  return (
    <div className="sources">
      {rows.length === 0 ? (
        <p className="muted">This run didn't look anything up.</p>
      ) : (
        <ul className="source-list">
          {rows.map((r, i) => (
            <li key={i} className={r.decision === "deny" ? "is-blocked" : undefined}>
              <div className="source-main">
                <span className="source-label">{r.label}</span>
                {r.href && r.decision !== "deny" ? (
                  <a href={r.href} target="_blank" rel="noopener noreferrer nofollow" className="source-detail">
                    {r.detail}
                  </a>
                ) : (
                  <span className="source-detail">{r.detail}</span>
                )}
                {r.decision === "deny" && r.message && <span className="source-why">{r.message}</span>}
              </div>
              <div className="source-tags">
                {UNTRUSTED_TOOLS.has(r.tool) && (
                  <span className="src-badge src-external" title="Content from outside the agency is treated as data, never as instructions">
                    Untrusted input
                  </span>
                )}
                <span className={`decision-chip decision-${r.decision}`}>{r.decision === "deny" ? "blocked" : r.decision}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="muted sources-foot">
        {blocked.length > 0
          ? `The gateway blocked ${blocked.length} call${blocked.length === 1 ? "" : "s"} in this run. `
          : "Every lookup went through the gateway's grant, type and domain checks. "}
        <Link to={`/activity?trace=${encodeURIComponent(run.run_id)}`}>See every gateway decision for this run</Link>
      </p>
    </div>
  );
}

// ---------- tabs under a run ----------

type Tab = "report" | "summary" | "sources";

const reportForRun = (reports: Report[], runId: string) =>
  reports.filter((r) => r.run_id === runId || r.trace_id === runId);

export default function ResearchOutput({ agent, run }: { agent: Agent; run: Run }) {
  const [reports, setReports] = useState<Report[]>([]);
  const [tab, setTab] = useState<Tab | null>(null);
  const [which, setWhich] = useState(0);

  const savesReports = (agent.tool_grants ?? []).includes("report.save");
  const finished = TERMINAL_RUN_STATUSES.includes(run.status);
  // Refetch when the run saves another report or finishes.
  const saveCount = (run.steps ?? []).filter((s) => s.tool === "report.save" && s.decision === "allow").length;

  useEffect(() => {
    setTab(null);
    setWhich(0);
  }, [run.run_id]);

  useEffect(() => {
    if (!savesReports && saveCount === 0) {
      setReports([]);
      return;
    }
    let cancelled = false;
    api
      .reports(agent.agent_id)
      .then((list: Report[]) => {
        if (!cancelled) setReports(reportForRun(Array.isArray(list) ? list : [], run.run_id));
      })
      .catch(() => !cancelled && setReports([]));
    return () => {
      cancelled = true;
    };
  }, [agent.agent_id, run.run_id, saveCount, finished, savesReports]);

  const sourceCount = (run.steps ?? []).filter((s) => sourceOf(s)).length;
  const tabs: { id: Tab; label: string }[] = [
    ...(reports.length ? [{ id: "report" as Tab, label: reports.length > 1 ? `Reports (${reports.length})` : "Report" }] : []),
    ...(run.output ? [{ id: "summary" as Tab, label: "Summary" }] : []),
    ...(sourceCount ? [{ id: "sources" as Tab, label: `Sources (${sourceCount})` }] : []),
  ];
  if (tabs.length === 0) return null;
  const active = tab && tabs.some((t) => t.id === tab) ? tab : tabs[0].id;
  // Reports come newest first; show them oldest first like the run did.
  const ordered = [...reports].reverse();
  const shown = ordered[Math.min(which, ordered.length - 1)];

  return (
    <section className="research-output" aria-label="Research results">
      <div className="ro-tabs" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={active === t.id}
            className={`ro-tab${active === t.id ? " is-active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="ro-panel" role="tabpanel">
        {active === "report" && shown && (
          <>
            {ordered.length > 1 && (
              <div className="ro-report-picker">
                {ordered.map((r, i) => (
                  <button
                    key={r.report_id}
                    type="button"
                    className={`ro-chip${i === which ? " is-active" : ""}`}
                    onClick={() => setWhich(i)}
                  >
                    {r.title}
                  </button>
                ))}
              </div>
            )}
            <ReportDocument report={shown} showOpenLink />
          </>
        )}
        {active === "summary" && run.output && (
          <div className="ro-summary">
            <Markdown source={run.output} idPrefix={`sum-${run.run_id}`} />
            <CitedLinks source={run.output} />
          </div>
        )}
        {active === "sources" && <SourcesConsulted run={run} />}
      </div>
    </section>
  );
}
