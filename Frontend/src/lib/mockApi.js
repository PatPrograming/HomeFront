// Fake backend for mock mode (VITE_MOCK_API=1, dev server only). It answers the same three routes as
// lane B's API so the whole demo can be clicked through without a server:
//   POST /sessions             → a fake identity card, then a scripted agent run (one event every 2 s)
//   GET  /events?since=<key>   → contract sample events + the generated ones, same cursor rules
//   POST /staff/{id}/offboard  → admin only; the agent's next call becomes a DENY and the run stops
// State lives in localStorage so a Maya tab and a Chief of Staff tab see the same fake world.
import { TOOL_NAMES } from "../../../packages/mandate-contracts/ts/index.ts";
import samples from "../../../packages/mandate-contracts/samples/events.json";
import { eventKey, nextCursor } from "./events.js";
import { decodeJwt } from "./pkce.js";
import { groupsFromAccessToken } from "./roles.js";

console.info("MANDATE_MOCK_API_MODULE loaded: fake API, no backend is called.");

const STORE_KEY = "mandate.mock.v1";
const STEP_MS = 2000;
const OFFICE = "rep-x";

const allow = (policyId) => ({ decision: "ALLOW", reason: "allowed", policyIds: [policyId], atlas: [] });
const deny = (policyIds, atlas) => ({ decision: "DENY", reason: "denied by policy", policyIds, atlas });
const clean = { flagged: false, categories: [] };

// What the agent "does" after Start assistant: routine work, a poisoned email, the attack, then routine work.
const SCRIPT = [
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

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY));
    if (saved) return saved;
  } catch {
    // fall through to a fresh world
  }
  return { events: [...samples], sessions: {}, offboarded: {} };
}

function save(state) {
  localStorage.setItem(STORE_KEY, JSON.stringify(state));
}

export function resetMock() {
  localStorage.removeItem(STORE_KEY);
}

class MockError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
    this.data = { detail };
  }
}

function caller(token) {
  if (!token) throw new MockError(401, "not signed in");
  return { username: decodeJwt(token).username, groups: groupsFromAccessToken(token) };
}

// Appends every scripted event whose time has come. After an offboard, the first call scheduled after it
// is denied ("session revoked / delegator inactive") and the agent stops, like the real agent does.
function advance(state, now) {
  for (const s of Object.values(state.sessions)) {
    while (!s.stopped && s.startedAt + (s.step + 1) * STEP_MS <= now) {
      const at = s.startedAt + (s.step + 1) * STEP_MS;
      const offboardedAt = state.offboarded[s.actingFor];
      const base = { sid: s.sid, agent: s.agent, actingFor: s.actingFor, office: s.office, guardrail: clean };
      if (offboardedAt && offboardedAt <= at) {
        state.events.push({
          ...base,
          id: crypto.randomUUID(),
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
      state.events.push({ ...base, ...SCRIPT[s.step % SCRIPT.length], id: crypto.randomUUID(), ts: new Date(at).toISOString() });
      s.step += 1;
    }
  }
}

export async function mockRequest(method, path, _body, token) {
  await new Promise((r) => setTimeout(r, 120)); // feel like a network call
  const url = new URL(path, "http://mock");
  const state = load();
  const now = Date.now();
  const user = caller(token);
  let result;

  if (method === "POST" && url.pathname === "/sessions") {
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
      expiresAt: new Date(now + 15 * 60 * 1000).toISOString(),
    };
    state.sessions[sid] = { ...card, startedAt: now, step: 0, stopped: false };
    result = card;
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
