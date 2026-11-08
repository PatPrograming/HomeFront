import { getAccessToken } from "./auth.js";
import { eventsPath } from "./events.js";
import { MOCK } from "./mode.js";

// Every backend call goes through this file. All routes live under /api/v1. In dev, Vite proxies /api to
// FastAPI (:8080). In production set VITE_API_BASE to the deployed API origin + /api/v1.
// The browser never calls AWS: it sends the staffer's Cognito access token to our API, which does the rest.
const BASE = import.meta.env.VITE_API_BASE || "/api/v1";

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
  if (MOCK) {
    // Dev-only fake backend; this branch and mockApi.js are removed from production builds.
    const { mockRequest } = await import("./mockApi.js");
    return mockRequest(method, path, body, getAccessToken());
  }

  const finalHeaders = { ...(body ? { "Content-Type": "application/json" } : {}), ...headers };
  if (auth) {
    const token = getAccessToken();
    if (token) finalHeaders.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: finalHeaders,
    body: body ? JSON.stringify(body) : undefined,
  });

  const text = await res.text();
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
  // Mandate console (lane B contract, docs/LANES.md)
  startSession: () => request("/sessions", { method: "POST", body: {} }),
  events: (cursor) => request(eventsPath(cursor)),
  offboard: (stafferId) =>
    request(`/staff/${encodeURIComponent(stafferId)}/offboard`, { method: "POST", body: {} }),

  // Legacy in-memory gateway, used only by the old /monitoring page
  tools: () => request("/tools", { auth: false }),
  matrix: () => request("/demo/matrix", { auth: false }),
  agents: () => request("/agents"),
  audit: (limit = 80) => request(`/audit?limit=${limit}`),
  revokeTool: (agentId, tool) =>
    request(`/agents/${agentId}/grants/${encodeURIComponent(tool)}/revoke`, { method: "POST" }),
  grantTool: (agentId, tool) =>
    request(`/agents/${agentId}/grants/${encodeURIComponent(tool)}`, { method: "POST" }),
  mintAgentToken: (agentId, ttlSeconds = 3600) =>
    request("/auth/token", {
      method: "POST",
      body: { agent_id: agentId, ttl_seconds: ttlSeconds },
    }),
  callTool: (agentToken, tool, arguments_ = {}) =>
    request("/mcp/call", {
      auth: false,
      method: "POST",
      headers: { Authorization: `Bearer ${agentToken}` },
      body: { tool, arguments: arguments_ },
    }),
};
