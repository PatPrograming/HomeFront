// Fake backend for mock mode (VITE_MOCK_API=1, dev server only). It answers the same routes as
// lane A/B's API so the whole demo can be clicked through without a server:
//   GET  /me                   → { username, sub, email, groups } of the demo user
//   POST /sessions             → a fake identity card, then a scripted agent run (one event every 2 s)
//   GET  /sessions/{sid}       → { sid, status, actingFor, office, expiresAt }; own sessions, or any for an admin
//   GET  /events?since=<key>   → contract sample events + the generated ones, same cursor rules
//   POST /staff/{id}/offboard  → admin only; the agent's next call becomes a DENY and the run stops
// State lives in localStorage so a Maya tab and a Chief of Staff tab see the same fake world.
import { TOOL_NAMES, type DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import sampleEvents from "../../../packages/mandate-contracts/samples/events.json";
import { eventKey, nextCursor } from "./events";
import { decodeJwt } from "./pkce";
import { groupsFromAccessToken } from "./roles";

const samples = sampleEvents as DecisionEvent[];

// One scripted agent call: the event fields that do not depend on the session.
type Step = Pick<DecisionEvent, "tool" | "resource" | "decision" | "reason" | "policyIds" | "atlas"> &
  Partial<Pick<DecisionEvent, "guardrail">>;

interface MockSession {
  sid: string;
  agent: string;
  actingFor: string;
  office: string;
  mode: string;
  allowedTools: string[];
  expiresAt: string;
  startedAt: number;
  step: number;
  stopped: boolean;
}

interface MockState {
  events: DecisionEvent[];
  sessions: Record<string, MockSession>;
  offboarded: Record<string, number>;
}

console.info("MANDATE_MOCK_API_MODULE loaded: fake API, no backend is called.");

const STORE_KEY = "mandate.mock.v1";
const STEP_MS = 2000;
const OFFICE = "rep-x";

type Verdict = Pick<Step, "decision" | "reason" | "policyIds" | "atlas">;
const allow = (policyId: string): Verdict => ({ decision: "ALLOW", reason: "allowed", policyIds: [policyId], atlas: [] });
const deny = (policyIds: string[], atlas: string[]): Verdict => ({ decision: "DENY", reason: "denied by policy", policyIds, atlas });
const clean = { flagged: false, categories: [] as string[] };

// What the agent "does" after Start assistant: routine work, a poisoned email, the attack, then routine work.
const SCRIPT: Step[] = [
  { tool: "inbox_read", resource: "email-3", ...allow("read-tools") },
  { tool: "case_read", resource: "case-7", ...allow("case-read-same-office") },
  { tool: "datagov_search", resource: "pothole repair schedule", ...allow("read-tools") },
  { tool: "email_send", resource: "resident.lee@example.com", ...allow("email-send-known-recipient") },
  {
    tool: "inbox_read",
    resource: "email-13",
    ...allow("read-tools"),
    atlas: ["AML.T0051.001"],
    guardrail: { flagged: true, categories: ["PROMPT_ATTACK"] },
  },
  { tool: "records_bulk_export", resource: "all constituents", ...deny(["forbid-bulk-export"], ["AML.T0053"]) },
  { tool: "email_send", resource: "drop@outside-mail.example", ...deny([], ["AML.T0053"]) },
  { tool: "social_post", resource: "campaign", ...deny(["forbid-campaign-post"], ["AML.T0053"]) },
  { tool: "case_read", resource: "case-22", ...deny([], []) },
  { tool: "inbox_read", resource: "email-4", ...allow("read-tools") },
  { tool: "case_read", resource: "case-9", ...allow("case-read-same-office") },
];

function load(): MockState {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? "null") as MockState | null;
    if (saved) return saved;
  } catch {
    // fall through to a fresh world
  }
  return { events: [...samples], sessions: {}, offboarded: {} };
}

// Two tabs (Maya and the Chief of Staff) write here every second, so merge with what the other tab saved
// instead of overwriting it: events by id (ids are "<sid>-<step>", the same in both tabs), the furthest
// state of each session, and every offboard.
function save(state: MockState): void {
  const latest = load();
  const events = new Map([...latest.events, ...state.events].map((e) => [e.id, e]));
  const sessions = { ...latest.sessions };
  for (const [sid, s] of Object.entries(state.sessions)) {
    const other = sessions[sid];
    sessions[sid] = !other || s.step > other.step || (s.stopped && !other.stopped) ? s : other;
  }
  const offboarded = { ...latest.offboarded, ...state.offboarded };
  localStorage.setItem(STORE_KEY, JSON.stringify({ events: [...events.values()], sessions, offboarded }));
}

export function resetMock(): void {
  localStorage.removeItem(STORE_KEY);
}

class MockError extends Error {
  status: number;
  data: { detail: string };

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.data = { detail };
  }
}

function caller(token: string | null): { username: string; groups: string[] } {
  if (!token) throw new MockError(401, "not signed in");
  return { username: String(decodeJwt(token).username), groups: groupsFromAccessToken(token) };
}

// Appends every scripted event whose time has come. After an offboard, the first call scheduled after it
// is denied ("session revoked / delegator inactive") and the agent stops, like the real agent does.
function advance(state: MockState, now: number): void {
  for (const s of Object.values(state.sessions)) {
    while (!s.stopped && s.startedAt + (s.step + 1) * STEP_MS <= now) {
      const at = s.startedAt + (s.step + 1) * STEP_MS;
      const offboardedAt = state.offboarded[s.actingFor];
      const base = { sid: s.sid, agent: s.agent, actingFor: s.actingFor, office: s.office, guardrail: clean };
      if (offboardedAt && offboardedAt <= at) {
        state.events.push({
          ...base,
          id: `${s.sid}-${s.step}`,
          ts: new Date(at).toISOString(),
          tool: SCRIPT[s.step % SCRIPT.length].tool,
          resource: SCRIPT[s.step % SCRIPT.length].resource,
          decision: "DENY",
          reason: "session revoked / delegator inactive",
          policyIds: [],
          atlas: ["AML.T0053"],
        });
        s.stopped = true;
        break;
      }
      state.events.push({ ...base, ...SCRIPT[s.step % SCRIPT.length], id: `${s.sid}-${s.step}`, ts: new Date(at).toISOString() });
      s.step += 1;
    }
  }
}

export async function mockRequest(method: string, path: string, _body: unknown, token: string | null): Promise<unknown> {
  await new Promise((r) => setTimeout(r, 120)); // feel like a network call
  const url = new URL(path, "http://mock");
  const state = load();
  const now = Date.now();
  const user = caller(token);
  let result: unknown;

  if (method === "GET" && url.pathname === "/me") {
    result = { username: user.username, sub: `mock-sub-${user.username}`, email: `${user.username}@rep-x.example.gov`, groups: user.groups };
  } else if (method === "POST" && url.pathname === "/sessions") {
    if (state.offboarded[user.username]) throw new MockError(403, "staffer is offboarded");
    for (const s of Object.values(state.sessions)) if (s.actingFor === user.username) s.stopped = true;
    const sid = crypto.randomUUID();
    const card = {
      sid,
      agent: "agent-1",
      actingFor: user.username,
      office: OFFICE,
      mode: "official",
      allowedTools: TOOL_NAMES.filter((t) => t !== "records_bulk_export"),
      expiresAt: new Date(now + 60 * 60 * 1000).toISOString(), // a session lasts one hour, like the real one
    };
    state.sessions[sid] = { ...card, startedAt: now, step: 0, stopped: false };
    result = card;
  } else if (method === "GET" && /^\/sessions\/[^/]+$/.test(url.pathname)) {
    const s = state.sessions[decodeURIComponent(url.pathname.split("/")[2])];
    if (!s || (s.actingFor !== user.username && !user.groups.includes("admin"))) throw new MockError(404, "Unknown session");
    // Offboarding marks the staffer, so every session acting for them reads as revoked (as on the server).
    const status = state.offboarded[s.actingFor] ? "revoked" : "active";
    result = { sid: s.sid, status, actingFor: s.actingFor, office: s.office, expiresAt: s.expiresAt };
  } else if (method === "GET" && url.pathname === "/events") {
    advance(state, now);
    const since = url.searchParams.get("since");
    const events = state.events.filter((e) => !since || eventKey(e) > since).sort((a, b) => (eventKey(a) < eventKey(b) ? -1 : 1));
    result = { events, next: nextCursor(since, { events }) };
  } else if (method === "POST" && /^\/staff\/[^/]+\/offboard$/.test(url.pathname)) {
    if (!user.groups.includes("admin")) throw new MockError(403, "admin only");
    const id = decodeURIComponent(url.pathname.split("/")[2]);
    advance(state, now);
    state.offboarded[id] = now;
    const revoked = Object.values(state.sessions).filter((s) => s.actingFor === id && !s.stopped);
    result = { staffer: id, active: false, revokedSessions: revoked.length };
  } else {
    throw new MockError(404, `mock API has no ${method} ${url.pathname}`);
  }

  save(state);
  return result;
}
