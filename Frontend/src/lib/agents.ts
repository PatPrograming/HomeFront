// Shared shapes and helpers for agents, agent types and tools (see GET /agent-types).
import { ApiError } from "./api";

export type AgentKind = "email" | "social" | "research" | "market_research" | "custom";

export interface Agent {
  agent_id: string;
  kind: AgentKind;
  name: string;
  agency: string;
  instructions: string;
  model: string | null;
  max_steps: number;
  owner_id: string | null;
  tool_grants: string[];
  // Mailing list: the only addresses email tools can reach.
  recipients: string[];
  // Missing on APIs older than email settings; read via emailSettingsOf().
  email_settings?: EmailSettings;
  // web.scrape domain allowlist ("*.gov", "sam.gov"); empty = any public host.
  scrape_domains?: string[];
  // Social media agents: the platforms they're for (x, bluesky, ...), shown on the card.
  platforms?: string[];
  token_version: number;
  active: boolean;
}

export interface EmailSettings {
  from_email: string;
  from_name: string;
  reply_to: string;
  signature: string;
}

export const EMPTY_EMAIL_SETTINGS: EmailSettings = { from_email: "", from_name: "", reply_to: "", signature: "" };

export const emailSettingsOf = (agent: Agent): EmailSettings => ({
  ...EMPTY_EMAIL_SETTINGS,
  ...(agent.email_settings ?? {}),
});

export interface EmailVerification {
  from_email: string | null;
  source: "agent" | "env" | null;
  verified: boolean | null;
  reason: string;
}

export interface AgentType {
  kind: AgentKind;
  label: string;
  description: string;
  default_instructions: string;
  default_tools: string[];
  // null = no ceiling (any registered tool).
  allowed_tools: string[] | null;
  default_max_steps: number;
  default_scrape_domains?: string[];
}

export interface Tool {
  name: string;
  description: string;
  risk_level: string;
  requires_hitl?: boolean;
  untrusted_input?: boolean;
  atlas_techniques?: string[];
}

// Plain-language phrase for what each tool lets an agent do.
const TOOL_ACTIONS: Record<string, string> = {
  "email.send": "send email",
  "email.send_list": "email the mailing list",
  "social.post": "post to social media",
  "social.reply": "reply to comments",
  "web.scrape": "read web pages",
  "datagov.search": "search data.gov",
  "usaspending.search": "search past federal awards",
  "sam.entity_lookup": "look up SAM.gov vendors",
  "report.save": "save reports",
  "census.query": "query Census data",
  "openfda.search": "search openFDA",
  "eia.query": "query EIA energy data",
  "nasa.search": "search NASA imagery",
  "noaa.climate": "query NOAA climate data",
  "nps.parks": "search National Parks",
  "bls.series": "fetch BLS series",
  "fec.search": "search FEC candidates",
  "epa.search": "search EPA facilities",
  "fcc.search": "search FCC licenses",
};

export const toolAction = (tool: string) => TOOL_ACTIONS[tool] || `use ${tool}`;

export const SOCIAL_TOOLS = ["social.post", "social.reply"];
export const isSocialTool = (tool: string) => SOCIAL_TOOLS.includes(tool);

const RISK_RANK: Record<string, number> = { low: 0, medium: 1, high: 2 };

/** Prefer a low-risk, non-HITL grant for the Prompt 1 focus tool. */
export function pickFocusTool(agent: Agent | null | undefined, catalog: Tool[] = []): string | null {
  const grants = agent?.tool_grants ?? [];
  if (!grants.length) return null;
  const byName = new Map(catalog.map((t) => [t.name, t]));
  const ranked = [...grants].sort((a, b) => {
    const ta = byName.get(a);
    const tb = byName.get(b);
    const hitl = Number(!!ta?.requires_hitl) - Number(!!tb?.requires_hitl);
    if (hitl) return hitl;
    return (RISK_RANK[ta?.risk_level || "medium"] ?? 1) - (RISK_RANK[tb?.risk_level || "medium"] ?? 1);
  });
  return ranked[0] ?? null;
}

/** Safe placeholder arguments for a Monitoring Prompt 1 tool call. */
export function demoArgsForTool(tool: string, agent?: Agent | null): Record<string, unknown> {
  switch (tool) {
    case "email.send": {
      const to = agent?.recipients?.[0] || "constituent@example.gov";
      return { to, subject: "Town hall reminder", body: "Join us Tuesday at City Hall." };
    }
    case "email.send_list":
      return { subject: "Office update", body: "A short update from the office." };
    case "social.post":
      return {
        platform: agent?.platforms?.[0] || "bluesky",
        text: "Office hours this week — stop by with questions.",
      };
    case "social.reply":
      return {
        platform: agent?.platforms?.[0] || "bluesky",
        text: "Thanks for writing in — we will follow up.",
        reply_to_url: "https://bsky.app/profile/example.bsky.social/post/demo",
      };
    case "web.scrape":
      return { url: "https://www.usa.gov" };
    case "datagov.search":
      return { query: "public health" };
    case "usaspending.search":
      return { keywords: "broadband", fiscal_year: 0, awarding_agency: "", limit: 5 };
    case "sam.entity_lookup":
      return { uei: "EXAMPLEUEI123" };
    case "report.save":
      return { title: "Demo findings", body: "Short summary for human review.", sources: [] };
    case "census.query":
      return { query: "population California" };
    case "openfda.search":
      return { query: "ibuprofen" };
    case "eia.query":
      return { series: "PET.RWTC.D" };
    case "nasa.search":
      return { query: "earth" };
    case "noaa.climate":
      return { location: "USW00094728" };
    case "nps.parks":
      return { query: "yosemite" };
    case "bls.series":
      return { series_id: "CUUR0000SA0" };
    case "fec.search":
      return { query: "senate" };
    case "epa.search":
      return { query: "water" };
    case "fcc.search":
      return { query: "license" };
    default:
      return {};
  }
}

// What a handed-off social draft looks like after approval (mcp_tools/share_links.py).
// HomeFront never publishes: the person opens share_url and presses Post themselves.
export interface ShareResult {
  status: "handed_off";
  published: false;
  platform: string;
  platform_name: string;
  // prefilled: text is in the link. link_only: only a URL is. copy_and_open: paste it yourself.
  mode: "prefilled" | "link_only" | "copy_and_open";
  share_url: string;
  copy_text: string;
  char_count: number;
  char_limit: number | null;
  over_limit: boolean;
  warnings: string[];
  note: string;
}

export const isShareResult = (result: unknown): result is ShareResult =>
  !!result && typeof result === "object" && (result as { status?: unknown }).status === "handed_off";

// Domains a share link may open, mirrored from the backend. The UI re-checks them so a
// bad row can never turn the button into an open redirect.
const SHARE_DOMAINS = ["x.com", "twitter.com", "bsky.app", "reddit.com", "facebook.com", "linkedin.com"];

export function safeShareUrl(url: string): string | null {
  try {
    const u = new URL(url);
    const host = u.hostname.toLowerCase();
    const ok = SHARE_DOMAINS.some((d) => host === d || host.endsWith(`.${d}`));
    return u.protocol === "https:" && ok ? u.toString() : null;
  } catch {
    return null;
  }
}

export const EMAIL_TOOLS = ["email.send", "email.send_list"];
export const sendsEmail = (agent: Agent) => (agent.tool_grants ?? []).some((t) => EMAIL_TOOLS.includes(t));

export function typeAllows(type: AgentType | undefined, tool: string): boolean {
  if (!type) return false;
  return type.allowed_tools === null || type.allowed_tools.includes(tool);
}

export function kindLabel(types: AgentType[], kind: string): string {
  return types.find((t) => t.kind === kind)?.label || kind;
}

const listPhrase = (items: string[]) =>
  items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;

/** A readable message for an API error, with a specific one for type-ceiling denials. */
export function describeError(e: unknown, types: AgentType[] = []): string {
  if (e instanceof ApiError) {
    const detail = (e as ApiError & { data?: { detail?: unknown } }).data?.detail;
    if (detail && typeof detail === "object") {
      const d = detail as { error?: string; kind?: string; tools?: string[]; message?: string };
      if (d.error === "tool_not_allowed_for_kind" && d.kind && d.tools?.length) {
        return `${kindLabel(types, d.kind)} agents can't ${listPhrase(d.tools.map(toolAction))}.`;
      }
      if (d.message) return d.message;
    }
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export interface AuditEvent {
  id: string;
  ts: number;
  type: string;
  decision: "allow" | "deny" | "revoke" | "info" | string;
  agent_id: string | null;
  trace_id?: string | null;
  run_id?: string | null;
  actor?: string;
  tool?: string | null;
  tools?: string[];
  kind?: string;
  status?: string;
  detail?: string;
  fields?: string[];
  tool_grants?: string[];
  args_hash?: string;
  auth_protocol?: string | null;
  atlas?: { narrative?: string; technique_ids?: string[]; mitigation_ids?: string[] };
}

export interface StorageStatus {
  agent_store: "memory" | "dynamodb" | string;
  audit_store: "memory" | "dynamodb" | string;
  approval_store?: "memory" | "dynamodb" | string;
  audit_write_errors: number;
  last_audit_error: string | null;
  warnings: string[];
  runner_configured: boolean;
}

/** True only when agents and the activity log both persist to DynamoDB without errors. */
export const isDurable = (s: StorageStatus | null) =>
  !!s &&
  s.agent_store === "dynamodb" &&
  s.audit_store === "dynamodb" &&
  (s.approval_store ?? "dynamodb") === "dynamodb" &&
  s.audit_write_errors === 0;

export const EVENT_LABELS: Record<string, string> = {
  agent_created: "Agent created",
  agent_updated: "Agent updated",
  authenticate: "Token issued",
  tool_call: "Tool call",
  grant_added: "Tool granted",
  grant_revoked: "Tool revoked",
  grants_replaced: "Tools replaced",
  grant_denied: "Grant blocked",
  recipients_updated: "Mailing list changed",
  email_settings_updated: "Email setup changed",
  email_test: "Test email",
  sender_verification_requested: "Sender verification sent",
  recipients_verification_requested: "Recipient verification sent",
  run_started: "Run started",
  run_finished: "Run finished",
  approval_requested: "Approval requested",
  approval_approved: "Approved",
  approval_rejected: "Rejected",
  approval_blocked: "Approval blocked",
  approval_expired: "Approval expired",
};

export const eventLabel = (type: string) => EVENT_LABELS[type] || type;

export type ApprovalStatus = "pending" | "approved" | "executed" | "failed" | "rejected" | "expired";

export interface Approval {
  approval_id: string;
  agent_id: string;
  tool: string;
  arguments: Record<string, string>;
  original_arguments: Record<string, string> | null;
  edited: boolean;
  status: ApprovalStatus;
  trace_id: string | null;
  run_id: string | null;
  requested_at: number;
  expires_at: number;
  decided_by: string | null;
  decided_at: number | null;
  note: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  agent: { agent_id: string; name: string; agency: string; kind: AgentKind } | null;
  tool_description: string | null;
  input_schema: { properties?: Record<string, { description?: string }> } | null;
  // email.send_list only: who it goes to if approved now.
  recipients: string[] | null;
  // Email tools only: the sender and signature the gateway adds when sending.
  sender?: {
    from: string | null;
    from_source: "agent" | "env" | null;
    reply_to: string | null;
    signature: string | null;
    simulated: boolean;
  } | null;
  // Pending email approvals: in the SES sandbox, recipients SES would reject.
  delivery?: {
    sandbox: boolean | null;
    // null = couldn't tell
    unverified: string[] | null;
    recipient_count?: number;
    reason: string | null;
  } | null;
}

export interface EmailAccount {
  sandbox: boolean | null;
  sending_enabled: boolean | null;
  max_24h: number | null;
  max_rate: number | null;
  sent_last_24h: number | null;
  region?: string;
  console_url?: string;
  reason: string | null;
}

export interface EmailPermissions {
  send: boolean | null;
  create_identity: boolean | null;
  principal?: string | null;
  reason: string | null;
}

export type RecipientStatus = "verified" | "pending" | "failed" | "not_requested" | null;

export interface RecipientVerification {
  available: boolean;
  reason: string | null;
  total: number;
  verified: number;
  recipients: { address: string; status: RecipientStatus }[];
}

export interface TestEmailResult {
  ok: boolean;
  to: string;
  from: string;
  message_id: string | null;
  error: string | null;
  error_code: string | null;
}

export type RunStatus = "queued" | "running" | "completed" | "max_steps" | "revoked" | "refused" | "failed";

export const TERMINAL_RUN_STATUSES: RunStatus[] = ["completed", "max_steps", "revoked", "refused", "failed"];

export interface RunStep {
  type: "tool_call" | "model_fallback" | "pipeline" | string;
  tool?: string;
  input?: Record<string, unknown>;
  decision?: "allow" | "deny" | "pending_approval" | string;
  error?: string;
  message?: string;
  result_preview?: string;
  approval_id?: string;
  from?: string;
  to?: string;
  stage?: string;
  agent_id?: string;
  pipeline_agent?: string;
  pipeline_stage?: string;
  research_run_id?: string;
  research_status?: string;
}

// A report saved with report.save (GET /agents/{id}/reports). body is Markdown.
export interface Report {
  report_id: string;
  agent_id: string;
  title: string;
  body: string;
  // Run id for agent runs, or the workload-token session for manual calls.
  trace_id: string | null;
  run_id: string | null;
  created_at: number;
}

export interface Run {
  run_id: string;
  agent_id: string;
  task: string;
  model: string;
  status: RunStatus;
  output: string | null;
  error: string | null;
  steps: RunStep[];
  model_turns: number;
  usage: { input_tokens: number; output_tokens: number };
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  pipeline?: string | null;
  research_run_id?: string | null;
  research_agent_id?: string | null;
  research_output?: string | null;
}
