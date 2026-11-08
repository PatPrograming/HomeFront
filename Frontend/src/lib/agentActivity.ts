// Plain words for what the assistant may do and what it did, for the inline "Working… / Done" disclosure in the
// chat. Staff see only these lines; the raw decision (policy ids, ATLAS ids, session id) stays in the admin feed.
import { TOOL_NAMES, type DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import type { AutonomyCategory, SessionCard } from "./api";

const LABELS: Record<string, string> = {
  inbox_read: "Read your inbox",
  case_read: "Look up case files for your office",
  datagov_search: "Search public government data",
  web_scrape: "Read public web pages",
  email_send: "Send email replies (waits for your review)",
  social_post: "Post on the official account (waits for your review)",
  records_bulk_export: "Export all constituent records",
};

export function toolLabel(tool: string): string {
  return LABELS[tool] ?? tool.replace(/_/g, " ");
}

// What the agent may do this session: its allowed tools, plus every kind of message it may send alone, either
// always (autonomy set to "sends alone") or for this session only ("Approve and allow for this session").
export function permissionLines(
  card: SessionCard,
  categories: AutonomyCategory[],
  grants: string[] = [],
): { allowed: string[]; never: string[] } {
  const open = categories.filter((c) => !c.locked);
  const always = open.filter((c) => c.level === "auto");
  const granted = open.filter((c) => c.level !== "auto" && grants.includes(c.category));
  const alone = [...always, ...granted].map((c) => c.category);
  const label = (tool: string) => {
    if (tool === "email_send" && alone.some((c) => c !== "social_post")) return "Send email replies (the kinds listed below go out without asking)";
    if (tool === "social_post" && alone.includes("social_post")) return "Post on the official account";
    return toolLabel(tool);
  };
  return {
    allowed: [
      ...card.allowedTools.map(label),
      ...always.map((c) => `Sends ${c.label.toLowerCase()} without asking`),
      ...granted.map((c) => `Sends ${c.label.toLowerCase()} without asking (this session only)`),
    ],
    never: TOOL_NAMES.filter((t) => !card.allowedTools.includes(t)).map(toolLabel),
  };
}

const lower = (s: string) => s[0].toLowerCase() + s.slice(1);

// Fail-closed denials where the check itself couldn't run: a server problem, not something the assistant may not do.
const SERVER_TROUBLE: Record<string, string> = {
  "AWS credentials expired on the server": "the server's AWS sign-in has expired (ask your admin)",
  "server lacks AWS permission": "the server isn't allowed to make this check (ask your admin)",
  "permission service unavailable": "the permission check is unavailable right now, try again shortly",
  "server error (failed closed)": "a server problem stopped it, so nothing was done",
};

// One action, as a staffer would say it.
export function describeEvent(e: DecisionEvent): { text: string; blocked: boolean } {
  const what = e.resource ?? "";
  if (e.reason === "session revoked / delegator inactive") return { text: "Blocked: your access was removed", blocked: true };
  if (SERVER_TROUBLE[e.reason]) return { text: `Not done: ${SERVER_TROUBLE[e.reason]}`, blocked: true };
  if (e.decision === "DENY") {
    const never = e.policyIds.some((p) => p.startsWith("forbid-")) ? " (never allowed)" : what ? ` (${what} is outside what it may do)` : "";
    return { text: `Blocked: ${lower(toolLabel(e.tool))}${never}`, blocked: true };
  }
  const kind = e.tool === "social_post" ? "a post" : `a reply to ${what}`;
  if (e.reason === "held for staff review") return { text: `Drafted ${kind}, waiting for your review`, blocked: false };
  if (e.reason === "approved by staff") return { text: `Sent ${kind} after you approved it`, blocked: false };
  const done: Record<string, string> = {
    inbox_read: `Read email ${what}`,
    case_read: `Looked up case ${what}`,
    datagov_search: `Searched public data for "${what}"`,
    web_scrape: `Read the web page ${what}`,
    email_send: `Sent a reply to ${what}`,
    social_post: "Posted on the official account",
  };
  const text = done[e.tool] ?? `${toolLabel(e.tool)}${what ? `: ${what}` : ""}`;
  const trick = e.guardrail?.flagged ? " (it looked like a trick; its instructions were ignored)" : "";
  return { text: text + trick, blocked: false };
}

// The calls made after `fromTs` and up to `toTs` (or until now), oldest first.
export function turnEvents(events: DecisionEvent[], fromTs: string, toTs: string | null): DecisionEvent[] {
  const from = Date.parse(fromTs);
  const to = toTs ? Date.parse(toTs) : Infinity;
  return events
    .filter((e) => {
      const t = Date.parse(e.ts);
      return t > from && t <= to;
    })
    .sort((a, b) => (a.ts < b.ts ? -1 : 1));
}
