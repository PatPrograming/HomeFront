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
  replaceGrants: (agentId, tools) =>
    request(`/agents/${encodeURIComponent(agentId)}/grants`, { method: "PUT", body: { tools } }),
  // filters: { agent_id, trace_id, decision, type } (any subset)
  audit: (limit = 80, filters = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    for (const [k, v] of Object.entries(filters)) if (v) params.set(k, v);
    return request(`/audit?${params}`);
  },
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
