import { getAccessToken } from "./auth";
import { eventsPath, type EventPage } from "./events";
import { MOCK } from "./mode";
import type { ToolName } from "../../../packages/mandate-contracts/ts/index";
import type { EmailPayload, SocialPayload } from "./agentPayload";

// Every backend call goes through this file. All routes live under /api/v1. In dev, Vite proxies /api to
// FastAPI (:8080). In production set VITE_API_BASE to the deployed API origin + /api/v1.
// The browser never calls AWS: it sends the staffer's Cognito access token to our API, which does the rest.
const BASE = import.meta.env.VITE_API_BASE || "/api/v1";
const TIMEOUT_MS = 5000;

/** GET /me: who the API thinks you are, from your Cognito token (lane A). */
export interface Me {
  username: string;
  sub: string;
  email: string | null;
  groups: string[];
}

/** POST /sessions: the agent's delegated identity card (lane B). */
export interface SessionCard {
  sid: string;
  agent: string;
  actingFor: string;
  office: string;
  mode: string;
  allowedTools: ToolName[];
  /** ISO string, or epoch seconds / milliseconds. */
  expiresAt: string | number;
}

/** GET /sessions/{sid}: the session's live status, so the card can show a revocation. */
export interface SessionStatus {
  sid: string;
  status: "active" | "revoked";
  actingFor: string;
  office: string;
  expiresAt: string | number;
}

/** POST /staff/{id}/offboard */
export interface OffboardResult {
  staffer: string;
  active: boolean;
  revokedSessions: number;
}

/** One message in the console chat. */
export interface ChatMessage {
  id: string;
  ts: string;
  role: "user" | "assistant";
  text: string;
}

/** GET /sessions/{sid}/messages?after=<cursor> */
export interface ChatPage {
  messages: ChatMessage[];
  next: string | null;
}

/** An email or post the gateway held for the staffer to review (GET /drafts). */
export interface Draft {
  id: string;
  status: "pending" | "sent" | "rejected";
  tool: "email_send" | "social_post";
  category: string;
  categoryLabel: string;
  args: Record<string, string>;
  createdAt: string;
  sid?: string; // the session that drafted it; "Allow for this session" grants the category there
}

/** What the staffer may change before approving: the text, never the recipient or account. */
export type DraftEdits = { subject?: string; body?: string; text?: string };

/** One kind of outbound message and whether the agent may send it alone (GET /autonomy). */
export interface AutonomyCategory {
  category: string;
  label: string;
  level: "review" | "auto";
  locked: boolean;
  approvedUnchanged: number;
  edited: number;
  rejected: number;
  suggestAuto: boolean;
}

export interface HomeContent {
  mission: string[];
  buttons: string[];
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

interface RequestOptions {
  auth?: boolean;
  method?: Method;
  body?: unknown;
  headers?: Record<string, string>;
}

export class ApiError extends Error {
  status: number;
  data: unknown;

  constructor(status: number, data: unknown) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail && typeof detail.message === "string"
          ? detail.message
          : `HTTP ${status}`;
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function request<T>(path: string, { auth = true, method = "GET", body, headers }: RequestOptions = {}): Promise<T> {
  if (MOCK) {
    // Dev-only fake backend; this branch and mockApi.ts are removed from production builds.
    const { mockRequest } = await import("./mockApi");
    return (await mockRequest(method, path, body, getAccessToken())) as T;
  }

  const finalHeaders: Record<string, string> = { ...(body ? { "Content-Type": "application/json" } : {}), ...headers };
  if (auth) {
    const token = getAccessToken();
    if (token) finalHeaders.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: finalHeaders,
    body: body ? JSON.stringify(body) : undefined,
    // A hung request must fail, or the 1 s feed would wait on it forever while still saying "Live".
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });

  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) throw new ApiError(res.status, data);
  return data as T;
}

export const api = {
  // Who the API thinks you are: { username, sub, email, groups } from your Cognito token (lane A)
  me: () => request<Me>("/me"),

  // HomeFront console (lane B contract, docs/LANES.md)
  startSession: () => request<SessionCard>("/sessions", { method: "POST", body: {} }),
  sessionStatus: (sid: string) => request<SessionStatus>(`/sessions/${encodeURIComponent(sid)}`),
  events: (cursor?: string | null) => request<EventPage>(eventsPath(cursor)),
  offboard: (stafferId: string) =>
    request<OffboardResult>(`/staff/${encodeURIComponent(stafferId)}/offboard`, { method: "POST", body: {} }),

  // Chat console: talk to the agent, review what it held, decide what it may send alone
  messages: (sid: string, after?: string | null) =>
    request<ChatPage>(`/sessions/${encodeURIComponent(sid)}/messages${after ? `?after=${encodeURIComponent(after)}` : ""}`),
  sendMessage: (sid: string, text: string) =>
    request<ChatMessage>(`/sessions/${encodeURIComponent(sid)}/messages`, { method: "POST", body: { text } }),
  drafts: () => request<{ drafts: Draft[] }>("/drafts"),
  // Let the agent send this category alone for one session (staffer's own login; chat text never grants)
  grant: (sid: string, category: string) =>
    request<{ sid: string; category: string; label: string; scope: string }>(`/sessions/${encodeURIComponent(sid)}/grants`, {
      method: "POST",
      body: { category },
    }),
  approveDraft: (id: string, edits: DraftEdits) =>
    request<{ id: string; status: "sent"; edited: boolean }>(`/drafts/${encodeURIComponent(id)}/approve`, { method: "POST", body: edits }),
  rejectDraft: (id: string) =>
    request<{ id: string; status: "rejected" }>(`/drafts/${encodeURIComponent(id)}/reject`, { method: "POST", body: {} }),
  autonomy: () => request<{ categories: AutonomyCategory[] }>("/autonomy"),
  setAutonomy: (category: string, level: AutonomyCategory["level"]) =>
    request<AutonomyCategory>(`/autonomy/${encodeURIComponent(category)}`, { method: "PUT", body: { level } }),
  snoozeAutonomy: (category: string) =>
    request<AutonomyCategory>(`/autonomy/${encodeURIComponent(category)}/snooze`, { method: "POST", body: {} }),

  // Email / Social Platform forms: settings for the AI agent (lib/agentPayload.ts). Sent with the Cognito
  // token when signed in. The backend route is not built yet, so this answers 404 for now.
  submitAgentRequest: (body: SocialPayload | EmailPayload) =>
    request<unknown>("/agent-requests", { method: "POST", body }),

  // Public home-page copy (no token)
  home: () => request<HomeContent>("/home", { auth: false }),
};
