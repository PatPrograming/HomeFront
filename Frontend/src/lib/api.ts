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
  /** The server's rank (Backend/app/routers/draft_rank.py): from the category, the original email and the age. */
  urgency: "high" | "medium" | "low";
  score: number;
  reason: string;
  // Editing in the chat (Lane H's routes). Older servers send none of these.
  version?: number; // "Draft N": +1 on each saved edit or revision
  revising?: boolean; // a change was requested; the agent is writing the next version
  editable?: string[]; // which fields the staffer may change; the server decides
  recipient?: string | null; // an email's recipient (args.to)
  platform?: string | null; // a post's one platform: one draft per platform, so approve can't half-post
  platformOptions?: { id: string; label: string }[]; // the platforms the server can post to
}

/** PATCH /drafts/{id}: the staffer's edits (only fields in the draft's `editable`), for the version they saw. */
export type DraftPatch = { subject?: string; body?: string; text?: string; to?: string; platform?: string };

/** GET /sessions: one of the caller's conversations (Lane E's route). Carries the card fields, so switching to
 * it can show the identity line without another call. */
export interface SessionSummary extends SessionCard {
  title: string; // named by the server from what the staffer asked first; the staffer can rename it
  status: "active" | "revoked" | "expired";
  waiting: number;
  createdAt: string;
  pinned: boolean;
  group: string | null;
  archived: boolean;
  earlier?: string[]; // the conversation's older sessions, newest first (Lane E); their chats load per sid
}

/** PATCH /sessions/{sid}: what the sidebar can change. Never the session's identity or permissions. */
export type SessionPatch = { title?: string; pinned?: boolean; group?: string | null; archived?: boolean };

/** POST /drafts/{id}/approve */
export type ApproveResult =
  | { id: string; status: "sent"; edited?: boolean }
  | { id: string; status: "needs_connection"; provider: string; label: string; connectUrl: string; note: string };

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
  // A new conversation, or (Lane E) the next session of one whose hour is up: `continues` is the old sid.
  startSession: (continues?: string) =>
    request<SessionCard & { continuesFrom?: string; grantsReset?: boolean }>("/sessions", {
      method: "POST",
      body: continues ? { continues } : {},
    }),
  // The caller's conversations, pinned first; `q` searches titles; `archived` lists only archived ones.
  listSessions: (q = "", archived = false) => {
    const params = new URLSearchParams({ ...(q.trim() ? { q: q.trim() } : {}), ...(archived ? { archived: "1" } : {}) }).toString();
    // Lane E's rows call the waiting count `waitingDrafts`; older servers and mock mode say `waiting`.
    return request<{ sessions: (SessionSummary & { waitingDrafts?: number })[] }>(`/sessions${params ? `?${params}` : ""}`).then((page) => ({
      sessions: page.sessions.map((s) => ({ ...s, waiting: s.waiting ?? s.waitingDrafts ?? 0 })),
    }));
  },
  updateSession: (sid: string, patch: SessionPatch) =>
    request<SessionSummary>(`/sessions/${encodeURIComponent(sid)}`, { method: "PATCH", body: patch }),
  // Soft delete: hidden from the staffer's lists; the office's audit record (events, drafts) is kept.
  deleteSession: (sid: string) => request<{ sid: string; deleted: true }>(`/sessions/${encodeURIComponent(sid)}`, { method: "DELETE" }),
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
  // Edit a held draft in place (Save edits), or ask the agent for a new version ("make it shorter").
  updateDraft: (id: string, patch: DraftPatch & { expectedVersion?: number }) =>
    request<Draft>(`/drafts/${encodeURIComponent(id)}`, { method: "PATCH", body: patch }),
  reviseDraft: (id: string, instruction: string) =>
    request<{ id: string; revising: true }>(`/drafts/${encodeURIComponent(id)}/revise`, { method: "POST", body: { instruction } }),
  // Let the agent send this category alone for one session (staffer's own login; chat text never grants)
  grant: (sid: string, category: string) =>
    request<{ sid: string; category: string; label: string; scope: string }>(`/sessions/${encodeURIComponent(sid)}/grants`, {
      method: "POST",
      body: { category },
    }),
  // `expectedVersion`: approve only the version on screen (409 if it changed). A platform that isn't connected yet
  // answers needs_connection (Lane I) and nothing is sent.
  approveDraft: (id: string, edits: DraftEdits, expectedVersion?: number) =>
    request<ApproveResult>(`/drafts/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      body: expectedVersion === undefined ? edits : { ...edits, expectedVersion },
    }),
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
