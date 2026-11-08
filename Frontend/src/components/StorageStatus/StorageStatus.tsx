import { useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
import { agentsApi as api } from "../../lib/agentsApi";
import { isDurable, type StorageStatus as Status } from "../../lib/agents";
import "./StorageStatus.css";

const storeName = (s: string) => (s === "dynamodb" ? "DynamoDB" : "memory only");

/**
 * Shows where agents and the activity log are being saved (GET /status), so a
 * silent fallback to memory is visible. Polls so expired credentials show up.
 */
export default function StorageStatus({ compact = false }: { compact?: boolean }) {
  const [status, setStatus] = useState<Status | null>(null);
  // "down": no response at all; "outdated": API answers but has no /status.
  const [problem, setProblem] = useState<"down" | "outdated" | null>(null);

  useEffect(() => {
    const load = () =>
      api
        .status()
        .then((s: Status) => {
          setStatus(s);
          setProblem(null);
        })
        .catch((e: unknown) => setProblem(e instanceof ApiError && e.status === 404 ? "outdated" : "down"));
    load();
    const id = window.setInterval(load, 10000);
    return () => window.clearInterval(id);
  }, []);

  if (problem) {
    return (
      <div className={`storage storage-bad${compact ? " is-compact" : ""}`} role="status">
        <strong>{problem === "down" ? "API unreachable" : "API needs a restart"}</strong>
        {!compact && (
          <span>
            {problem === "down"
              ? "Nothing is being saved. Start it with ./scripts/run-gateway.sh."
              : "The running API is older than this page and can't report where data is saved. Restart ./scripts/run-gateway.sh."}
          </span>
        )}
      </div>
    );
  }
  if (!status) return null;

  const ok = isDurable(status);
  const issues = [
    ...status.warnings,
    ...(status.audit_write_errors
      ? [
          `${status.audit_write_errors} activity event(s) failed to save` +
            (status.last_audit_error ? ` (${status.last_audit_error}). AWS credentials may have expired.` : "."),
        ]
      : []),
  ];

  return (
    <div className={`storage ${ok ? "storage-ok" : "storage-bad"}${compact ? " is-compact" : ""}`} role="status">
      <strong>{ok ? "Saving to DynamoDB" : "Not fully saved"}</strong>
      {!compact && (
        <span>
          Agents: {storeName(status.agent_store)} · Activity log: {storeName(status.audit_store)}
          {status.approval_store && <> · Approvals: {storeName(status.approval_store)}</>}
        </span>
      )}
      {!compact && issues.length > 0 && (
        <ul>
          {issues.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
