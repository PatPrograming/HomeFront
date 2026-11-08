// "Run a task": give an agent a task, then watch its tool calls live.
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../lib/api";
import { agentsApi as api } from "../lib/agentsApi";
import { describeError, TERMINAL_RUN_STATUSES, type Agent, type Run, type RunStep } from "../lib/agents";
import "./RunTask.css";

const POLL_MS = 1500;
const HISTORY = 10;

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  max_steps: "Stopped: step limit",
  revoked: "Stopped: token revoked",
  refused: "Refused by the model",
  failed: "Failed",
};

const isActive = (run: Run | null) => !!run && !TERMINAL_RUN_STATUSES.includes(run.status);

const preview = (value: unknown, max = 160) => {
  if (value === undefined || value === null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max)}…` : text;
};

const when = (ts: number | null | undefined) => (ts ? new Date(ts * 1000).toLocaleTimeString() : "");

function startError(e: unknown): string {
  if (e instanceof ApiError && (e as ApiError & { status?: number }).status === 503) {
    return (
      "The agent runner isn't configured on the server: it needs BEDROCK_REGION (or AWS_REGION) " +
      "and access to Claude on Amazon Bedrock."
    );
  }
  return describeError(e);
}

function StepTime({ ts }: { ts?: number }) {
  if (ts === undefined) return null;
  const when = new Date(ts * 1000);
  return (
    <time className="muted" dateTime={when.toISOString()}>
      {when.toLocaleString()}
    </time>
  );
}

function Step({ step, index }: { step: RunStep; index: number }) {
  if (step.type === "model_fallback") {
    return (
      <li className="run-step">
        <span className="step-num">{index + 1}</span>
        <span className="muted">
          Switched model{step.from ? ` from ${step.from}` : ""}
          {step.to ? ` to ${step.to}` : ""}
        </span>{" "}
        <StepTime ts={step.ts} />
      </li>
    );
  }
  if (step.type !== "tool_call") {
    return (
      <li className="run-step">
        <span className="step-num">{index + 1}</span>
        <span className="muted">{step.type}</span> <StepTime ts={step.ts} />
      </li>
    );
  }
  const decision = step.decision || "unknown";
  return (
    <li className="run-step">
      <span className="step-num">{index + 1}</span>
      <div className="step-body">
        <div className="step-head">
          <code>{step.tool || "?"}</code>
          <span className={`decision-chip decision-${decision}`}>
            {decision === "pending_approval" ? "waiting for approval" : decision}
          </span>
          <StepTime ts={step.ts} />
        </div>
        {step.input && Object.keys(step.input).length > 0 && (
          <p className="step-detail">
            <span className="muted">Input </span>
            <code>{preview(step.input)}</code>
          </p>
        )}
        {decision === "deny" && (
          <p className="step-detail step-deny">
            {step.message || step.error || "Blocked by the gateway"}
          </p>
        )}
        {decision === "pending_approval" && (
          <p className="step-detail">
            Nothing was sent yet. <Link to="/approvals">Review it on the Approvals page</Link>
          </p>
        )}
        {decision === "allow" && step.result_preview && (
          <p className="step-detail">
            <span className="muted">Result </span>
            <code>{preview(step.result_preview, 220)}</code>
          </p>
        )}
      </div>
    </li>
  );
}

export default function RunTask({ agent, collapsible = false }: { agent: Agent; collapsible?: boolean }) {
  const [open, setOpen] = useState(!collapsible);
  const [task, setTask] = useState("");
  const [current, setCurrent] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadHistory = useCallback(
    () =>
      api
        .runs(agent.agent_id)
        .then((list: Run[]) => {
          const runs = Array.isArray(list) ? list.slice(0, HISTORY) : [];
          setHistory(runs);
          return runs;
        })
        .catch(() => [] as Run[]),
    [agent.agent_id],
  );

  // Pick up a run that's still going (e.g. after a page reload).
  useEffect(() => {
    if (!open) return;
    setCurrent(null);
    loadHistory().then((runs) => {
      const active = runs.find((r) => isActive(r));
      if (active) setCurrent(active);
    });
  }, [open, loadHistory]);

  const activeId = isActive(current) ? current!.run_id : null;
  useEffect(() => {
    if (!activeId) return;
    const id = window.setInterval(() => {
      api
        .run(agent.agent_id, activeId)
        .then((run: Run) => {
          setCurrent(run);
          if (!isActive(run)) loadHistory();
        })
        .catch((e) => setError(describeError(e)));
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [activeId, agent.agent_id, loadHistory]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const run: Run = await api.startRun(agent.agent_id, task.trim());
      setCurrent(run);
      loadHistory();
    } catch (e) {
      setError(startError(e));
    } finally {
      setStarting(false);
    }
  }

  if (!open) {
    return (
      <div className="run-task run-task-collapsed">
        <button className="btn btn-outline" type="button" onClick={() => setOpen(true)}>
          Run a task
        </button>
      </div>
    );
  }

  const busy = starting || !!activeId;
  const steps = current?.steps ?? [];
  const usage = current?.usage ?? { input_tokens: 0, output_tokens: 0 };

  return (
    <div className="run-task">
      <div className="run-task-head">
        <span className="field-label">Run a task</span>
        {collapsible && (
          <button className="btn btn-ghost" type="button" onClick={() => setOpen(false)}>
            Hide
          </button>
        )}
      </div>
      <textarea
        className="textarea"
        rows={3}
        maxLength={8000}
        value={task}
        onChange={(e) => setTask(e.target.value)}
        placeholder="e.g. Email everyone on the mailing list a reminder about Tuesday's 6pm town hall."
        aria-label={`Task for ${agent.name}`}
      />
      <div className="form-actions">
        <button
          className="btn btn-primary"
          type="button"
          disabled={busy || !task.trim() || !agent.active}
          onClick={start}
        >
          {starting ? "Starting…" : activeId ? "Running…" : "Run"}
        </button>
        <span className="muted mailing-list-hint">
          Each run calls Claude on Amazon Bedrock (billed to your AWS account). Emails still wait for approval.
        </span>
      </div>
      {!agent.active && <p className="muted mailing-list-hint">This agent is inactive, so it can't run.</p>}
      {error && (
        <p className="alert alert-error" role="alert">
          {error}
        </p>
      )}

      {current && (
        <div className="run-view" aria-live="polite">
          <div className="run-meta">
            <span className={`run-status run-${current.status}`}>{STATUS_LABELS[current.status] || current.status}</span>
            <span className="muted">
              {current.model_turns ?? 0} model turn{current.model_turns === 1 ? "" : "s"} ·{" "}
              {(usage.input_tokens ?? 0).toLocaleString()} in / {(usage.output_tokens ?? 0).toLocaleString()} out
              tokens{current.model ? ` · ${current.model}` : ""}
            </span>
          </div>
          <p className="run-task-text">
            <span className="muted">Task: </span>
            {current.task}
          </p>
          {steps.length > 0 ? (
            <ol className="run-steps">
              {steps.map((s, i) => (
                <Step key={i} step={s} index={i} />
              ))}
            </ol>
          ) : (
            isActive(current) && <p className="muted mailing-list-hint">Thinking…</p>
          )}
          {current.output && (
            <div className="run-output">
              <span className="field-label">Agent's summary</span>
              <p>{current.output}</p>
            </div>
          )}
          {current.error && <p className="alert alert-error">{current.error}</p>}
        </div>
      )}

      {history.length > 0 && (
        <div className="run-history">
          <span className="field-label">Recent runs</span>
          <ul>
            {history.map((r) => (
              <li key={r.run_id}>
                <button
                  type="button"
                  className={`run-history-item${current?.run_id === r.run_id ? " is-current" : ""}`}
                  onClick={() => setCurrent(r)}
                >
                  <span className={`run-status run-${r.status}`}>{STATUS_LABELS[r.status] || r.status}</span>
                  <span className="run-history-task">{preview(r.task, 80)}</span>
                  <span className="muted">{when(r.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
