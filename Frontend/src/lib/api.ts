import { getAccessToken } from "./auth";
import { eventsPath, type EventPage } from "./events";
import { MOCK } from "./mode";
import type { ToolName } from "../../../packages/mandate-contracts/ts/index";

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

/** POST /staff/{id}/offboard */
export interface OffboardResult {
  staffer: string;
  active: boolean;
  revokedSessions: number;
}

export interface HomeContent {
  mission: string[];
  buttons: string[];
}

type Method = "GET" | "POST";

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

async function request<T>(path: string, { auth = true, method = "GET", body, headers }: RequestOptions = {}): Promise<T> {
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
  events: (cursor?: string | null) => request<EventPage>(eventsPath(cursor)),
  offboard: (stafferId: string) =>
    request<OffboardResult>(`/staff/${encodeURIComponent(stafferId)}/offboard`, { method: "POST", body: {} }),

  // Public home-page copy (no token)
  home: () => request<HomeContent>("/home", { auth: false }),
};
