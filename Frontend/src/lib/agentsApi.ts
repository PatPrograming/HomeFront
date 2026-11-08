// The teammates' agent tabs (Agents, Monitoring, Approvals, Activity) talk to the old-gateway routes in
// packages/identity-gateway/src/identity_gateway/agents_api.py. Every call goes through our `request()` in
// api.ts, so it carries the staffer's Cognito access token: reads need a staff login, changes need the admin
// group (the API checks both). In mock mode these routes don't exist, so the pages show the mock's 404.
import { request } from "./api";
import type {
  Agent,
  AgentType,
  Approval,
  AuditEvent,
  EmailAccount,
  EmailPermissions,
  EmailSettings,
  EmailVerification,
  RecipientVerification,
  Run,
  StorageStatus,
  TestEmailResult,
  Tool,
} from "./agents";

/** The ATLAS note the old gateway attaches to allow/deny/revoke answers. */
export interface AtlasNote {
  narrative?: string;
  technique_ids?: string[];
  mitigation_ids?: string[];
}

export interface AtlasMatrix {
  atlas_version: string;
  techniques?: { id: string; name?: string }[];
  mitigations?: { id: string; name?: string }[];
}

export interface WorkloadToken {
  access_token: string;
  agent_id: string;
  tool_grants: string[];
  token_version: number;
  session_id: string;
  atlas?: AtlasNote;
}

export interface ToolCallResult {
  decision: "allow" | "pending_approval";
  tool: string;
  result: unknown;
  approval_id?: string;
  atlas?: AtlasNote;
}

export interface GrantChange {
  agent_id: string;
  tool_grants: string[];
  token_version: number;
  message?: string;
  atlas?: AtlasNote;
}

export interface CreateAgentBody {
  kind: string;
  name: string;
  agency: string;
  instructions?: string;
  model?: string | null;
  max_steps?: number;
  tool_grants?: string[];
  agent_id?: string;
  recipients?: string[];
}

export interface VerificationRequest {
  requested: string[];
  failed: { address: string; error: string }[];
  remaining: number;
  already_verified: number;
  already_pending: number;
}

export type AuditFilters = Partial<Record<"agent_id" | "trace_id" | "decision" | "type", string>>;

const agentPath = (agentId: string) => `/agents/${encodeURIComponent(agentId)}`;

export const agentsApi = {
  // Catalogs (still need a login). `/tools` is the mandate flow's catalog, so the old one is `/agent-tools`.
  tools: () => request<Tool[]>("/agent-tools"),
  agentTypes: () => request<AgentType[]>("/agent-types"),
  matrix: () => request<AtlasMatrix>("/demo/matrix", { auth: false }),
  status: () => request<StorageStatus>("/status"),

  agents: () => request<Agent[]>("/agents"),
  agent: (agentId: string) => request<Agent>(agentPath(agentId)),
  // Fields left out (instructions, tool_grants, max_steps) take the kind's template defaults.
  createAgent: (body: CreateAgentBody) => request<Agent>("/agents", { method: "POST", body }),
  // Replaces the agent's whole mailing list (the only addresses it may email).
  setRecipients: (agentId: string, emails: string[]) =>
    request<Agent>(`${agentPath(agentId)}/recipients`, { method: "PUT", body: { emails } }),
  // "" clears a field
  setEmailSettings: (agentId: string, body: EmailSettings) =>
    request<Agent>(`${agentPath(agentId)}/email-settings`, { method: "PUT", body }),
  emailVerification: (agentId: string) => request<EmailVerification>(`${agentPath(agentId)}/email-settings/verification`),
  // SES CreateEmailIdentity for the sender (AWS emails it a verification link).
  verifySender: (agentId: string) =>
    request<{ address: string; message: string; status?: string }>(`${agentPath(agentId)}/email-settings/verify-sender`, {
      method: "POST",
      body: {},
    }),
  // A real SES send to one listed address (or the sender itself), no approval step.
  sendTestEmail: (agentId: string, to: string) =>
    request<TestEmailResult>(`${agentPath(agentId)}/email-settings/test`, { method: "POST", body: { to } }),
  recipientVerification: (agentId: string) =>
    request<RecipientVerification>(`${agentPath(agentId)}/recipients/verification`),
  // Up to 10 verification emails per call; call again while `remaining` > 0.
  verifyRecipients: (agentId: string) =>
    request<VerificationRequest>(`${agentPath(agentId)}/recipients/verify`, { method: "POST", body: {} }),
  emailAccount: () => request<EmailAccount>("/email/account"),
  emailPermissions: () => request<EmailPermissions>("/email/permissions"),

  // Agent runs (Claude on Bedrock). Poll run() until a terminal status.
  // opts.research_first: research agent runs first, then this agent uses the findings.
  startRun: (
    agentId: string,
    task: string,
    opts: { research_first?: boolean; research_agent_id?: string } = {},
  ) =>
    request<Run>(`${agentPath(agentId)}/runs`, {
      method: "POST",
      body: {
        task,
        ...(opts.research_first ? { research_first: true } : {}),
        ...(opts.research_agent_id ? { research_agent_id: opts.research_agent_id } : {}),
      },
    }),
  runs: (agentId: string) => request<Run[]>(`${agentPath(agentId)}/runs`),
  run: (agentId: string, runId: string) => request<Run>(`${agentPath(agentId)}/runs/${encodeURIComponent(runId)}`),

  replaceGrants: (agentId: string, tools: string[]) =>
    request<GrantChange>(`${agentPath(agentId)}/grants`, { method: "PUT", body: { tools } }),
  revokeTool: (agentId: string, tool: string) =>
    request<GrantChange>(`${agentPath(agentId)}/grants/${encodeURIComponent(tool)}/revoke`, { method: "POST", body: {} }),
  grantTool: (agentId: string, tool: string) =>
    request<GrantChange>(`${agentPath(agentId)}/grants/${encodeURIComponent(tool)}`, { method: "POST", body: {} }),

  audit: (limit = 80, filters: AuditFilters = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v);
    return request<AuditEvent[]>(`/audit?${params}`);
  },

  // Human approvals for high-risk tool calls (email.send, social.*).
  approvals: (status?: string) => request<Approval[]>(`/approvals${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  approval: (id: string) => request<Approval>(`/approvals/${encodeURIComponent(id)}`),
  approve: (id: string, body: { arguments?: Record<string, string>; note?: string } = {}) =>
    request<Approval>(`/approvals/${encodeURIComponent(id)}/approve`, { method: "POST", body }),
  reject: (id: string, note?: string) =>
    request<Approval>(`/approvals/${encodeURIComponent(id)}/reject`, { method: "POST", body: note ? { note } : {} }),

  // Admin only: mint the agent's workload token (old gateway, not a mandate token).
  mintAgentToken: (agentId: string, ttlSeconds = 3600) =>
    request<WorkloadToken>("/auth/token", { method: "POST", body: { agent_id: agentId, ttl_seconds: ttlSeconds } }),
  // Admin only: our Cognito login in Authorization, the agent's workload token in X-Agent-Token.
  callTool: (agentToken: string, tool: string, args: Record<string, unknown> = {}) =>
    request<ToolCallResult>("/agents/tool-call", {
      method: "POST",
      headers: { "X-Agent-Token": agentToken },
      body: { tool, arguments: args },
    }),
};
