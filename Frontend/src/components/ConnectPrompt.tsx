import { useState } from "react";
import { Link } from "react-router-dom";
import { beginConnect } from "../lib/connections";
import "../pages/Account.css";

export interface NeedsConnection {
  provider: string;
  label: string;
  note: string;
}

// The only accounts HomeFront can connect. Labels come from here, never from the message, because a
// tool result can be steered by text the agent read (it must not make "Connect Gmail" start an X sign-in).
export const CONNECTABLE: Record<string, string> = { gmail: "Gmail", x: "X" };

function fromObject(x: unknown): NeedsConnection | null {
  if (typeof x !== "object" || x === null) return null;
  const r = x as Record<string, unknown>;
  if (r.status !== "needs_connection" || typeof r.provider !== "string" || !(r.provider in CONNECTABLE)) return null;
  return { provider: r.provider, label: CONNECTABLE[r.provider], note: typeof r.note === "string" ? r.note : "" };
}

/** Reads a tool's `needs_connection` result from the result object, a JSON string, or text that contains the JSON. */
export function parseNeedsConnection(x: unknown): NeedsConnection | null {
  if (typeof x !== "string") return fromObject(x);
  const start = x.indexOf("{");
  const end = x.lastIndexOf("}");
  if (start < 0 || end < start) return null;
  try {
    return fromObject(JSON.parse(x.slice(start, end + 1)));
  } catch {
    return null; // braces that are not our JSON: ordinary chat text
  }
}

/** Shown in the chat when the assistant could not send or post because the staffer has not connected that account. */
export default function ConnectPrompt({ provider, note }: { provider: string; label?: string; note?: string }) {
  const [error, setError] = useState<string | null>(null);
  const name = CONNECTABLE[provider] ?? provider;

  const connect = () => {
    setError(null);
    beginConnect(provider).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  return (
    <div className="connect-prompt">
      <p>Nothing was sent. To let your assistant send this, connect your {name}.</p>
      {note && <p className="muted">{note}</p>}
      {error && (
        <p className="alert alert-error" role="alert">
          Could not start the sign-in: {error}
        </p>
      )}
      <div className="connect-prompt-actions">
        <button type="button" className="btn btn-outline" onClick={connect}>
          Connect {name}
        </button>
        <Link to="/account">Manage connected accounts</Link>
      </div>
    </div>
  );
}
