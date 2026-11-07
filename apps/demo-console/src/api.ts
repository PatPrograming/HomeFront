const BASE = import.meta.env.VITE_API_BASE || '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  })
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`) as Error & {
      status: number
      data: unknown
    }
    err.status = res.status
    err.data = data
    throw err
  }
  return data as T
}

export type Agent = {
  agent_id: string
  name: string
  agency: string
  tool_grants: string[]
  token_version: number
  active?: boolean
}

export type Tool = {
  name: string
  description: string
  risk_level: string
  atlas_techniques: string[]
  atlas_mitigations: string[]
  requires_hitl: boolean
  holds_secrets: boolean
  untrusted_input: boolean
}

export type AuditEvent = {
  id: string
  ts: number
  type: string
  decision: string
  agent_id: string
  tool: string | null
  atlas?: {
    technique_ids?: string[]
    mitigation_ids?: string[]
    narrative?: string
  }
}

export type Matrix = {
  atlas_version: string
  techniques: { id: string; name?: string; description?: string }[]
  mitigations: { id: string; name?: string; description?: string }[]
}

export const api = {
  agents: () => request<Agent[]>('/agents'),
  tools: () => request<Tool[]>('/tools'),
  audit: () => request<AuditEvent[]>('/audit?limit=80'),
  matrix: () => request<Matrix>('/demo/matrix'),
  auth: (agent_id: string) =>
    request<{
      access_token: string
      tool_grants: string[]
      token_version: number
      agency: string
      atlas: { narrative: string }
    }>('/auth/token', {
      method: 'POST',
      body: JSON.stringify({ agent_id }),
    }),
  revoke: (agent_id: string, tool: string) =>
    request<{
      tool_grants: string[]
      token_version: number
      atlas: { technique_ids: string[]; mitigation_ids: string[]; narrative: string }
      message: string
    }>(`/agents/${agent_id}/grants/${encodeURIComponent(tool)}/revoke`, {
      method: 'POST',
    }),
  grant: (agent_id: string, tool: string) =>
    request<{ tool_grants: string[] }>(
      `/agents/${agent_id}/grants/${encodeURIComponent(tool)}`,
      { method: 'POST' },
    ),
  callTool: (token: string, tool: string, arguments_: Record<string, unknown>) =>
    request<{
      decision: string
      result: unknown
      atlas: {
        technique_ids: string[]
        mitigation_ids: string[]
        narrative: string
      }
    }>('/mcp/call', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: JSON.stringify({ tool, arguments: arguments_ }),
    }),
}
