import { getIdToken } from "./AuthProvider";

// All backend routes live under /api/v1. In dev, Vite proxies /api to FastAPI (:8080).
// In production set VITE_API_BASE to the deployed API origin + /api/v1.
const BASE = import.meta.env.VITE_API_BASE || "/api/v1";

const API_DOWN = "Can't reach the API server. Start it with ./scripts/run-gateway.sh (port 8080).";

export class ApiError extends Error {
  constructor(status, data) {
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && detail.message
          ? detail.message
          : `HTTP ${status}`;
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function request(path, { auth = true, method = "GET", body, headers } = {}) {
  const finalHeaders = { ...(body ? { "Content-Type": "application/json" } : {}), ...headers };

  if (auth) {
    const token = await getIdToken();
    if (token) finalHeaders.Authorization = `Bearer ${token}`;
  }

  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: finalHeaders,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, { detail: API_DOWN });
  }

  const text = await res.text();
  // The Vite dev proxy answers 5xx with an empty body when the API isn't running.
  if (!res.ok && res.status >= 500 && !text) throw new ApiError(res.status, { detail: API_DOWN });
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data;
}

export const api = {
  // public
  home: () => request("/home", { auth: false }),
  health: () => request("/health", { auth: false }),
  tools: () => request("/tools", { auth: false }),
  agentTypes: () => request("/agent-types", { auth: false }),
  matrix: () => request("/demo/matrix", { auth: false }),

  // signed-in user
  me: () => request("/me"),
  updateMe: (changes) => request("/me", { method: "PATCH", body: changes }),

  // agent management (needs login when AGENT_ADMIN_REQUIRES_LOGIN=true)
  agents: () => request("/agents"),
  agent: (agentId) => request(`/agents/${encodeURIComponent(agentId)}`),
  // Fields left out (instructions, tool_grants, max_steps) take the kind's template defaults.
  createAgent: (body) => request("/agents", { method: "POST", body }),
  // Replaces the agent's whole mailing list (the only addresses it may email).
  setRecipients: (agentId, emails) =>
    request(`/agents/${encodeURIComponent(agentId)}/recipients`, { method: "PUT", body: { emails } }),
  // body: { from_email, from_name, reply_to, signature } ("" clears a field)
  setEmailSettings: (agentId, body) =>
    request(`/agents/${encodeURIComponent(agentId)}/email-settings`, { method: "PUT", body }),
  // { from_email, source, verified: true | false | null, reason }
  emailVerification: (agentId) => request(`/agents/${encodeURIComponent(agentId)}/email-settings/verification`),
  // SES: CreateEmailIdentity for the sender (AWS emails it a verification link).
  verifySender: (agentId) =>
    request(`/agents/${encodeURIComponent(agentId)}/email-settings/verify-sender`, { method: "POST" }),
  // A real SES send to one listed address (or the sender itself), no approval step.
  sendTestEmail: (agentId, to) =>
    request(`/agents/${encodeURIComponent(agentId)}/email-settings/test`, { method: "POST", body: { to } }),
  recipientVerification: (agentId) => request(`/agents/${encodeURIComponent(agentId)}/recipients/verification`),
  // Up to 10 verification emails per call; call again while `remaining` > 0.
  verifyRecipients: (agentId) =>
    request(`/agents/${encodeURIComponent(agentId)}/recipients/verify`, { method: "POST" }),
  emailAccount: () => request("/email/account"),
  emailPermissions: () => request("/email/permissions"),

  // Form data (social post / email setup) for the AI agent. The backend route is not built yet;
  // change this path when it is. body: see lib/agentPayload.ts
  submitAgentRequest: (body) => request("/agent-requests", { method: "POST", body }),

  // Agent runs (Claude on Bedrock). Poll run() until a terminal status.
  startRun: (agentId, task) =>
    request(`/agents/${encodeURIComponent(agentId)}/runs`, { method: "POST", body: { task } }),
  runs: (agentId) => request(`/agents/${encodeURIComponent(agentId)}/runs`),
  run: (agentId, runId) =>
    request(`/agents/${encodeURIComponent(agentId)}/runs/${encodeURIComponent(runId)}`),
  replaceGrants: (agentId, tools) =>
    request(`/agents/${encodeURIComponent(agentId)}/grants`, { method: "PUT", body: { tools } }),
  // filters: { agent_id, trace_id, decision, type } (any subset)
  audit: (limit = 80, filters = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v);
    return request(`/audit?${params}`);
  },
  // Human approvals for high-risk tool calls (email.send, social.*).
  approvals: (status) => request(`/approvals${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  approval: (id) => request(`/approvals/${encodeURIComponent(id)}`),
  // body: { arguments?: edited args, note?: string }
  approve: (id, body = {}) =>
    request(`/approvals/${encodeURIComponent(id)}/approve`, { method: "POST", body }),
  reject: (id, note) =>
    request(`/approvals/${encodeURIComponent(id)}/reject`, { method: "POST", body: note ? { note } : {} }),
  // Where agents and the activity log are actually stored right now.
  status: () => request("/status", { auth: false }),
  revokeTool: (agentId, tool) =>
    request(`/agents/${agentId}/grants/${encodeURIComponent(tool)}/revoke`, { method: "POST" }),
  grantTool: (agentId, tool) =>
    request(`/agents/${agentId}/grants/${encodeURIComponent(tool)}`, { method: "POST" }),

  // mint agent workload token (user JWT when admin login required)
  mintAgentToken: (agentId, ttlSeconds = 3600) =>
    request("/auth/token", {
      method: "POST",
      body: { agent_id: agentId, ttl_seconds: ttlSeconds },
    }),

  // MCP tool call uses the agent token only — never the Supabase user JWT
  callTool: (agentToken, tool, arguments_ = {}) =>
    request("/mcp/call", {
      auth: false,
      method: "POST",
      headers: { Authorization: `Bearer ${agentToken}` },
      body: { tool, arguments: arguments_ },
    }),
};
