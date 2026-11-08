// Fake backend for mock mode (VITE_MOCK_API=1, dev server only). It answers the same routes as
// lane A/B's API so the whole demo can be clicked through without a server:
//   GET  /me                   → { username, sub, email, groups } of the demo user
//   POST /sessions             → a fake identity card, then a scripted agent run (one event every 2 s)
//   GET  /sessions/{sid}       → { sid, status, actingFor, office, expiresAt }; own sessions, or any for an admin
//   GET  /events?since=<key>   → contract sample events + the generated ones, same cursor rules
//   POST /staff/{id}/offboard  → admin only; the agent's next call becomes a DENY and the run stops
//   /admin/people, /admin/roles, /admin/audit → People & roles (admin only)
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
  admin?: MockAdmin;
}

// People & roles edits: office role and group per user, a role's tool list, and the change log.
interface MockAdmin {
  roles: Record<string, Role>;
  groups: Record<string, "staffer" | "admin">;
  roleTools: Partial<Record<Role, string[]>>;
  log: { id: string; ts: string; actor: string; type: string; target: string; summary: string }[];
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
  const admin = state.admin ?? latest.admin;
  localStorage.setItem(STORE_KEY, JSON.stringify({ events: [...events.values()], sessions, offboarded, assistant, admin }));
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

const MOCK_ADMINS = new Set(["admin", "chiefofstaff"]);
const ROLES = Object.keys(ROLE_LABELS) as Role[];
const ACTIONS: Record<string, string> = {
  inbox_read: "read the office inbox",
  case_read: "open constituent case files",
  datagov_search: "search public government data",
  web_scrape: "read public web pages",
  email_send: "send email for the office",
  social_post: "post on the office's official account",
  email_send_list: "email the whole office mailing list",
  social_reply: "reply to comments on the office's official account",
  usaspending_search: "search federal contract awards",
  sam_entity_lookup: "look up federal vendors in SAM.gov",
  report_save: "save reports for the office",
  census_query: "look up Census data",
  openfda_search: "search FDA drug, device and food data",
  eia_query: "look up energy data",
  nasa_search: "search NASA images",
  noaa_climate: "look up NOAA climate data",
  nps_parks: "look up National Park Service parks",
  bls_series: "look up labor statistics",
  fec_search: "search campaign finance records",
  epa_search: "search EPA facility records",
  fcc_search: "search FCC licenses",
  records_bulk_export: "export constituent records in bulk",
};

function adminState(state: MockState): MockAdmin {
  state.admin ??= { roles: { ...MOCK_ROLES }, groups: {}, roleTools: {}, log: [] };
  return state.admin;
}

const roleOf = (state: MockState, username: string): Role | undefined => state.admin?.roles[username] ?? MOCK_ROLES[username];
const toolsOf = (state: MockState, role: Role): string[] => state.admin?.roleTools[role] ?? [...ROLE_TOOLS[role]];

function describe(role: Role, before: string[], after: string[]): string[] {
  const label = ROLE_LABELS[role];
  return [
    ...after.filter((t) => !before.includes(t)).map((t) => `Every ${label}'s assistant will be able to ${ACTIONS[t] ?? t}.`),
    ...before.filter((t) => !after.includes(t)).map((t) => `Every ${label}'s assistant will no longer be able to ${ACTIONS[t] ?? t}.`),
  ];
}

// Like people.py: the admin group only, a real role, only governed tools, never the last admin.
function adminRoute(state: MockState, method: string, pathname: string, body: unknown, user: { username: string; groups: string[] }): unknown {
  if (!user.groups.includes("admin")) throw new MockError(403, "Admin only");
  const a = adminState(state);
  const groupOf = (u: string) => a.groups[u] ?? (MOCK_ADMINS.has(u) ? "admin" : "staffer");
  const usernames = Object.keys(a.roles).sort();
  const people = () =>
    usernames.map((u) => ({
      username: u, email: `${u}@rep-x.example.gov`, inDirectory: true, enabled: true, group: groupOf(u),
      officeRole: a.roles[u], roleLabel: ROLE_LABELS[a.roles[u]], office: OFFICE, active: !state.offboarded[u],
    }));
  const log = (type: string, target: string, summary: string) =>
    a.log.unshift({ id: crypto.randomUUID(), ts: new Date().toISOString(), actor: user.username, type, target, summary });
  const roleTools = (role: string, tools: unknown) => {
    if (!ROLES.includes(role as Role)) throw new MockError(404, `There is no office role called '${role}'.`);
    if (!Array.isArray(tools) || new Set(tools).size !== tools.length) throw new MockError(400, "A tool is listed twice.");
    for (const t of tools) {
      if (t === "records_bulk_export") throw new MockError(400, `${t} is blocked for everyone by policy and cannot be given to a role.`);
      if (!TOOL_NAMES.includes(t)) throw new MockError(400, `There is no tool called '${t}'.`);
      if (t === "email_send_list" && role !== "chief_of_staff") throw new MockError(400, `${t} is locked by policy to the Chief of Staff only.`);
    }
    const r = role as Role;
    const before = toolsOf(state, r);
    const after = TOOL_NAMES.filter((t) => tools.includes(t));
    return { role: r, tools: after, before, changes: describe(r, before, after), affects: usernames.filter((u) => a.roles[u] === r) };
  };

  if (method === "GET" && pathname === "/admin/people") return { people: people() };
  if (method === "GET" && pathname === "/admin/roles") {
    return {
      roles: ROLES.map((r) => ({
        id: r, label: ROLE_LABELS[r], tools: toolsOf(state, r), defaultTools: [...ROLE_TOOLS[r]],
        assignable: TOOL_NAMES.filter((t) => t !== "records_bulk_export" && (t !== "email_send_list" || r === "chief_of_staff")), customized: Boolean(a.roleTools[r]),
        updatedBy: a.log.find((e) => e.type === "role_tools_changed" && e.target === r)?.actor ?? null, updatedAt: null, people: usernames.filter((u) => a.roles[u] === r),
      })),
      tools: TOOL_NAMES.map((t) => ({ name: t, action: ACTIONS[t] ?? t, alwaysDenied: t === "records_bulk_export", lockedTo: t === "email_send_list" ? ["Chief of Staff"] : [] })),
    };
  }
  if (method === "GET" && pathname === "/admin/audit") return { events: a.log };
  const preview = pathname.match(/^\/admin\/roles\/([^/]+)\/preview$/);
  if (method === "POST" && preview) return roleTools(decodeURIComponent(preview[1]), (body as { tools?: unknown })?.tools);
  const role = pathname.match(/^\/admin\/roles\/([^/]+)$/);
  if (method === "PUT" && role) {
    const change = roleTools(decodeURIComponent(role[1]), (body as { tools?: unknown })?.tools);
    a.roleTools[change.role] = change.tools;
    log("role_tools_changed", change.role, change.changes.join(" ") || `${ROLE_LABELS[change.role]} saved with no change.`);
    return change;
  }
  const person = pathname.match(/^\/admin\/people\/([^/]+)$/);
  if (method === "PUT" && person) {
    const u = decodeURIComponent(person[1]);
    if (!a.roles[u]) throw new MockError(404, "Unknown person");
    const { officeRole, group } = (body ?? {}) as { officeRole?: string; group?: "staffer" | "admin" };
    if (officeRole !== undefined && !ROLES.includes(officeRole as Role)) throw new MockError(400, `There is no office role called '${officeRole}'.`);
    if (group && group !== groupOf(u)) {
      if (groupOf(u) === "admin" && !usernames.some((o) => o !== u && groupOf(o) === "admin")) {
        throw new MockError(409, "This is the last admin. Make someone else an admin first.");
      }
      log("group_changed", u, `${u} moved from ${groupOf(u)} to ${group}.`);
      a.groups[u] = group;
    }
    if (officeRole) {
      log("role_assigned", u, `${u} is now ${ROLE_LABELS[officeRole as Role]} (was ${ROLE_LABELS[a.roles[u]]}). Their assistant's next tool call uses the new role.`);
      a.roles[u] = officeRole as Role;
    }
    return people().find((p) => p.username === u);
  }
  return undefined;
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
    const role = roleOf(state, user.username) ?? (user.groups.includes("admin") ? "chief_of_staff" : "caseworker");
    const card = {
      sid,
      agent: "agent-1",
      actingFor: user.username,
      office: OFFICE,
      mode: "official",
      // Like the real broker: the role comes from the account (test-users.sh), never from the client.
      role,
      roleLabel: ROLE_LABELS[role],
      allowedTools: TOOL_NAMES.filter((t) => toolsOf(state, role).includes(t)),
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
  } else if (url.pathname.startsWith("/admin/")) {
    result = adminRoute(state, method, url.pathname, body, user);
    if (result === undefined) throw new MockError(404, `mock API has no ${method} ${url.pathname}`);
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
