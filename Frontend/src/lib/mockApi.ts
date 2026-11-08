// Fake backend for mock mode (VITE_MOCK_API=1, dev server only). It answers the same routes as
// lane A/B's API so the whole demo can be clicked through without a server:
//   GET  /me                   → { username, sub, email, groups } of the demo user
//   POST /sessions             → a fake identity card, then a scripted agent run (one event every 2 s)
//   GET  /sessions/{sid}       → { sid, status, actingFor, office, expiresAt }; own sessions, or any for an admin
//   GET  /events?since=<key>   → contract sample events + the generated ones, same cursor rules
//   POST /staff/{id}/offboard  → admin only; the agent's next call becomes a DENY and the run stops
//   chat, review queue and autonomy routes → mockAssistant.ts
// State lives in localStorage so a Maya tab and a Chief of Staff tab see the same fake world.
import { ROLE_LABELS, ROLE_TOOLS, TOOL_NAMES, type DecisionEvent, type Role } from "../../../packages/mandate-contracts/ts/index";
import sampleEvents from "../../../packages/mandate-contracts/samples/events.json";
import { eventKey, nextCursor } from "./events";
import { assistantRoute, emptyAssistantState, mergeAssistantState, MockError, type AssistantState } from "./mockAssistant";
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
  assistant: AssistantState;
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
  // Held, not sent: Maya has not let the agent send replies alone yet (earned autonomy).
  { tool: "email_send", resource: "resident.lee@example.com", ...allow("email-send-known-recipient"), reason: "held for staff review" },
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
    if (saved) return { ...saved, assistant: saved.assistant ?? emptyAssistantState() }; // saved before the chat existed
  } catch {
    // fall through to a fresh world
  }
  return { events: [...samples], sessions: {}, offboarded: {}, assistant: emptyAssistantState() };
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
  const assistant = mergeAssistantState(latest.assistant, state.assistant);
  localStorage.setItem(STORE_KEY, JSON.stringify({ events: [...events.values()], sessions, offboarded, assistant }));
}

export function resetMock(): void {
  localStorage.removeItem(STORE_KEY);
}

// The demo accounts' office roles, as Backend/infra/test-users.sh writes them (docs/ROLES.md).
const MOCK_ROLES: Record<string, Role> = {
  admin: "chief_of_staff",
  chiefofstaff: "chief_of_staff",
  bronco: "caseworker",
  maya: "caseworker",
  scuacm: "communications_director",
  a10: "legislative_aide",
  aws: "intern",
};

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

export async function mockRequest(method: string, path: string, body: unknown, token: string | null): Promise<unknown> {
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
    const role = MOCK_ROLES[user.username] ?? (user.groups.includes("admin") ? "chief_of_staff" : "caseworker");
    const card = {
      sid,
      agent: "agent-1",
      actingFor: user.username,
      office: OFFICE,
      mode: "official",
      // Like the real broker: the role comes from the account (test-users.sh), never from the client.
      role,
      roleLabel: ROLE_LABELS[role],
      allowedTools: TOOL_NAMES.filter((t) => ROLE_TOOLS[role].includes(t)),
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
    const revoked = (s: { sid: string; actingFor: string }) => Boolean(state.offboarded[s.actingFor] || state.sessions[s.sid]?.stopped);
    result = assistantRoute(state.assistant, state.events, state.sessions, revoked, {
      method, pathname: url.pathname, search: url.searchParams, body, user: user.username, now,
    });
    if (result === undefined) throw new MockError(404, `mock API has no ${method} ${url.pathname}`);
  }

  save(state);
  return result;
}
