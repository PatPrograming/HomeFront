import { supabase } from "./supabase.js";

// All backend routes live under /api/v1. In dev, Vite proxies /api to FastAPI (:8080).
// In production set VITE_API_BASE to the deployed API origin + /api/v1.
const BASE = import.meta.env.VITE_API_BASE || "/api/v1";

export class ApiError extends Error {
  constructor(status, data) {
    const detail = typeof data?.detail === "string" ? data.detail : `HTTP ${status}`;
    super(detail);
    this.status = status;
    this.data = data;
  }
}

async function request(path, { auth = true, method = "GET", body, headers } = {}) {
  const finalHeaders = { ...(body ? { "Content-Type": "application/json" } : {}), ...headers };

  if (auth) {
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
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
  // public
  home: () => request("/home", { auth: false }),
  health: () => request("/health", { auth: false }),
  tools: () => request("/tools", { auth: false }),

  // signed-in user
  me: () => request("/me"),
  updateMe: (changes) => request("/me", { method: "PATCH", body: changes }),

  // agent management (needs login when AGENT_ADMIN_REQUIRES_LOGIN=true)
  agents: () => request("/agents"),
  audit: (limit = 80) => request(`/audit?limit=${limit}`),
  revokeTool: (agentId, tool) =>
    request(`/agents/${agentId}/grants/${encodeURIComponent(tool)}/revoke`, { method: "POST" }),
  grantTool: (agentId, tool) =>
    request(`/agents/${agentId}/grants/${encodeURIComponent(tool)}`, { method: "POST" }),
};
