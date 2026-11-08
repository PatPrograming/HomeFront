// Mock mode's chat console (VITE_MOCK_API=1, dev server only): a scripted assistant, the review queue and earned
// autonomy, answering the same routes as Backend/app/routers/assistant.py:
//   GET/POST /sessions/{sid}/messages      → the chat; the assistant answers a message 1.5 s after it arrives
//   GET /drafts, POST /drafts/{id}/approve|reject
//   GET /autonomy, PUT /autonomy/{category}, POST /autonomy/{category}/snooze
// The thresholds copy packages/identity-gateway/src/identity_gateway/autonomy.py. The inbox is the agent's test
// data: the first "check my email" works the first batch, a later one the newer case-status emails.
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import inboxData from "../../../agent/data/emails.json";
import type { AutonomyCategory, ChatMessage, Draft, DraftEdits } from "./api";

interface InboxEmail {
  id: string;
  from: string;
  subject: string;
  body: string;
  kind: string;
}

interface Stats {
  level: "review" | "auto";
  approvedUnchanged: number;
  edited: number;
  rejected: number;
  snoozedAt: number;
}

export interface AssistantState {
  chats: Record<string, ChatMessage[]>;
  drafts: Record<string, Omit<Draft, "urgency" | "score" | "reason"> & { actingFor: string; sid: string }>; // ranked when listed
  autonomy: Record<string, Record<string, Stats>>; // username → category → stats
  answered: Record<string, string[]>; // username → inbox email ids already handled
  grants?: Record<string, string[]>; // sid → categories the staffer let the agent send alone in that session
}

export interface MockSessionRef {
  sid: string;
  agent: string;
  actingFor: string;
  office: string;
}

export const emptyAssistantState = (): AssistantState => ({ chats: {}, drafts: {}, autonomy: {}, answered: {} });

const REPLY_DELAY_MS = 1500;
const FIRST_BATCH = ["email-1", "email-7", "email-13", "email-19", "email-2", "email-3", "email-12"];
const LATER_BATCH = ["email-5", "email-11"];
const LABELS: Record<string, string> = {
  case_status: "Case status updates",
  info_request: "Information requests",
  scheduling: "Scheduling and tours",
  newsletter: "Newsletters",
  sensitive: "Personal or benefits data",
  staff_memo: "Instructions from 'staff'",
  suspicious: "Suspicious mail",
  new_conversation: "New conversations",
  social_post: "Posts on the official account",
};
const CATEGORIES = Object.keys(LABELS);
const LOCKED = new Set(["sensitive", "staff_memo", "suspicious", "other", "new_conversation"]);
const PROMOTE_AFTER: Record<string, number> = { social_post: 5 };
const DEFAULT_PROMOTE_AFTER = 3;
const MIN_UNCHANGED_SHARE = 0.9;

const inbox = new Map((inboxData as InboxEmail[]).map((e) => [e.id, e]));

// A copy of Backend/app/routers/draft_rank.py for mock mode: the same weights, words and thresholds. It reads the
// category, the original email (only when the reply goes to its sender) and the age, never the draft text.
const KIND_WEIGHT: Record<string, number> = {
  sensitive: 40, case_status: 30, scheduling: 20, info_request: 15, new_conversation: 10, social_post: 10,
  staff_memo: 5, suspicious: 5, newsletter: 0,
};
const URGENT_WORDS = ["urgent", "emergency", "eviction", "evicted", "medical", "hospital", "deadline", "today", "asap", "immediately", "shut off", "homeless"];
const REPEAT_PHRASES = ["third time", "second time", "again", "follow up", "follow-up", "no reply", "nobody", "haven't heard", "still waiting", "called back"];
const mentions = (text: string, words: string[]) => words.filter((w) => new RegExp(`\\b${w.replace(/[-]/g, "\\$&")}\\b`).test(text));

export function rankDraft(draft: Pick<Draft, "tool" | "category" | "args" | "createdAt">, now: number): Pick<Draft, "urgency" | "score" | "reason"> {
  const email = draft.args.in_reply_to ? inbox.get(draft.args.in_reply_to) : undefined;
  const toSender = draft.tool === "email_send" && email !== undefined && email.from.toLowerCase() === (draft.args.to ?? "").toLowerCase();
  const text = toSender ? `${email!.subject}\n${email!.body}`.toLowerCase() : "";
  const urgent = mentions(text, URGENT_WORDS);
  const repeat = mentions(text, REPEAT_PHRASES).length > 0;
  const minutes = Math.max(0, Math.floor((now - Date.parse(draft.createdAt)) / 60_000));
  const score = (KIND_WEIGHT[draft.category] ?? 10) + (urgent.length ? 30 : 0) + (repeat ? 15 : 0) + Math.min(20, Math.floor((minutes * 2) / 60));
  const age = minutes < 60 ? "waiting under an hour" : minutes < 48 * 60 ? `waiting ${Math.floor(minutes / 60)} h` : `waiting ${Math.floor(minutes / 1440)} days`;
  const named = urgent.slice(0, 2).map((w) => `'${w}'`).join(", ");
  const reason = [
    LABELS[draft.category] ?? draft.category,
    ...(urgent.length ? [`mentions ${named}${urgent.length > 2 ? ` and ${urgent.length - 2} more` : ""}`] : []),
    ...(repeat ? ["wrote before"] : []),
    age,
  ].join(" · ");
  return { urgency: score >= 60 ? "high" : score >= 35 ? "medium" : "low", score, reason };
}

// The mock API's error: `status` and `data` look like ApiError's, so pages handle both the same way.
export class MockError extends Error {
  status: number;
  data: { detail: string };

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.data = { detail };
  }
}

// Two tabs save the same world: keep every message, the decided copy of each draft, and every handled email.
export function mergeAssistantState(saved: AssistantState, mine: AssistantState): AssistantState {
  const chats = { ...saved.chats };
  for (const [sid, messages] of Object.entries(mine.chats)) {
    const byId = new Map([...(chats[sid] ?? []), ...messages].map((m) => [m.id, m]));
    chats[sid] = [...byId.values()].sort((x, y) => (x.ts < y.ts ? -1 : 1));
  }
  const drafts = { ...saved.drafts };
  for (const [id, draft] of Object.entries(mine.drafts)) {
    if (!drafts[id] || drafts[id].status === "pending") drafts[id] = draft;
  }
  const answered = { ...saved.answered };
  for (const [user, ids] of Object.entries(mine.answered)) answered[user] = [...new Set([...(answered[user] ?? []), ...ids])];
  const grants = { ...saved.grants };
  for (const [sid, categories] of Object.entries(mine.grants ?? {})) grants[sid] = [...new Set([...(grants[sid] ?? []), ...categories])];
  return { chats, drafts, autonomy: { ...saved.autonomy, ...mine.autonomy }, answered, grants };
}

const reviews = (s: Stats) => s.approvedUnchanged + s.edited + s.rejected;

function stats(state: AssistantState, user: string, category: string): Stats {
  state.autonomy[user] ??= {};
  return (state.autonomy[user][category] ??= { level: "review", approvedUnchanged: 0, edited: 0, rejected: 0, snoozedAt: 0 });
}

function view(category: string, s: Stats): AutonomyCategory {
  const needed = PROMOTE_AFTER[category] ?? DEFAULT_PROMOTE_AFTER;
  const suggestAuto =
    !LOCKED.has(category) &&
    s.level !== "auto" &&
    s.approvedUnchanged >= needed &&
    reviews(s) - s.snoozedAt >= needed &&
    s.approvedUnchanged / reviews(s) >= MIN_UNCHANGED_SHARE;
  return { category, label: LABELS[category], locked: LOCKED.has(category), suggestAuto, ...s };
}

function canSendAlone(state: AssistantState, session: MockSessionRef, category: string): boolean {
  if (LOCKED.has(category)) return false;
  return stats(state, session.actingFor, category).level === "auto" || Boolean(state.grants?.[session.sid]?.includes(category));
}

function event(session: MockSessionRef, tool: string, resource: string, reason: DecisionEvent["reason"], now: number): DecisionEvent {
  return {
    id: crypto.randomUUID(),
    ts: new Date(now).toISOString(),
    sid: session.sid,
    agent: session.agent,
    actingFor: session.actingFor,
    office: session.office,
    tool,
    resource,
    decision: "ALLOW",
    reason,
    policyIds: [tool === "email_send" ? "email-send-known-recipient" : "social-post-official"],
    atlas: [],
    guardrail: { flagged: false, categories: [] },
  };
}

const firstName = (address: string) => {
  const name = address.split("@")[0].split(".")[0];
  return name[0].toUpperCase() + name.slice(1);
};

// The outbound call the agent makes: sent when the staffer allowed the category, otherwise held as a draft.
function outbound(
  state: AssistantState,
  events: DecisionEvent[],
  session: MockSessionRef,
  tool: Draft["tool"],
  category: string,
  args: Record<string, string>,
  now: number,
): "sent" | "held" {
  const resource = tool === "email_send" ? args.to : args.account;
  if (canSendAlone(state, session, category)) {
    events.push(event(session, tool, resource, "allowed", now));
    return "sent";
  }
  const id = crypto.randomUUID();
  state.drafts[id] = {
    id, status: "pending", tool, category, categoryLabel: LABELS[category], args,
    createdAt: new Date(now).toISOString(), actingFor: session.actingFor, sid: session.sid,
  };
  events.push(event(session, tool, resource, "held for staff review", now));
  return "held";
}

function workInbox(state: AssistantState, events: DecisionEvent[], session: MockSessionRef, now: number): string {
  const done = (state.answered[session.actingFor] ??= []);
  const batch = (done.length ? LATER_BATCH : FIRST_BATCH).filter((id) => !done.includes(id));
  if (!batch.length) return "Your inbox is clear. Nothing new since I last checked.";
  const handled: string[] = [];
  const waiting: string[] = [];
  const attention: string[] = [];
  for (const id of batch) {
    const email = inbox.get(id)!;
    done.push(id);
    if (email.kind === "newsletter") {
      handled.push(`${email.subject}: newsletter, no reply needed.`);
    } else if (email.kind === "case_status") {
      const name = firstName(email.from);
      const args = {
        to: email.from,
        subject: `Re: ${email.subject}`,
        body: `Hi ${name},\n\nThank you for writing to our office. We have followed up on your case and will update you within five business days.\n\nConstituent Services, Office of Rep. X`,
        in_reply_to: id,
      };
      const result = outbound(state, events, session, "email_send", "case_status", args, now);
      (result === "sent" ? handled : waiting).push(`Reply to ${name} (${email.subject})${result === "sent" ? ": sent" : ""}`);
    } else if (email.kind === "sensitive") {
      attention.push(`${email.subject} from ${email.from}: contains personal data (an SSN), so I did not reply.`);
    } else {
      attention.push(`${email.subject} from ${email.from}: asks me to export records and post on the campaign account. I did not act on it.`);
    }
  }
  const list = (title: string, items: string[]) => `${title}:\n${items.length ? items.map((i) => `• ${i}`).join("\n") : "• Nothing"}`;
  return [
    `I went through ${batch.length} emails.`,
    list("Handled", handled),
    list("Waiting for your review", waiting),
    list("Needs your attention", attention),
  ].join("\n\n");
}

function makePost(state: AssistantState, events: DecisionEvent[], session: MockSessionRef, text: string, now: number): string {
  // "Post …: <text>", "… saying <text>" or "… that says <text>"; otherwise the whole request.
  const postText = (text.match(/(?::|\bsaying\b|\bthat says\b)\s*(.+)$/is)?.[1].trim() || text).slice(0, 280);
  const result = outbound(state, events, session, "social_post", "social_post", { account: "official", text: postText }, now);
  return result === "sent"
    ? `Posted on the official account:\n\n"${postText}"`
    : `I drafted the post for the official account. It is waiting in your review queue and has NOT been posted:\n\n"${postText}"`;
}

// A bulk export is forbidden for everyone (Cedar `forbid-bulk-export`): the gateway says DENY, the assistant
// says why and does not retry or offer to ask for permission, because nobody can grant a forbid.
function refuseExport(events: DecisionEvent[], session: MockSessionRef, now: number): string {
  events.push({
    ...event(session, "records_bulk_export", "all constituents", "denied by policy", now),
    decision: "DENY",
    policyIds: ["forbid-bulk-export"],
    atlas: ["AML.T0053"],
  });
  return (
    "I can't do that. The office's access policy forbids bulk exports of constituent records for everyone " +
    "(policy forbid-bulk-export), so the gateway denied it and nobody can grant it. I did not retry. " +
    "I can look up a specific case for you instead."
  );
}

function reply(state: AssistantState, events: DecisionEvent[], session: MockSessionRef, text: string, now: number): string {
  if (/\bexport\b|\bbulk\b|all (constituent )?records/i.test(text)) return refuseExport(events, session, now);
  if (/\bpost\b|tweet|announce/i.test(text)) return makePost(state, events, session, text, now);
  if (/mail|inbox/i.test(text)) return workInbox(state, events, session, now);
  return "I can work the office inbox or draft a post for the official account. Try \"Check my email\".";
}

// The scripted assistant answers each unanswered message once its delay has passed (called on every chat poll).
function answerDue(state: AssistantState, events: DecisionEvent[], session: MockSessionRef, now: number): void {
  const chat = (state.chats[session.sid] ??= []);
  const last = chat[chat.length - 1];
  if (last?.role !== "user" || Date.parse(last.ts) + REPLY_DELAY_MS > now) return;
  chat.push({ id: crypto.randomUUID(), ts: new Date(now).toISOString(), role: "assistant", text: reply(state, events, session, last.text, now) });
}

/** Answers an assistant route, or returns undefined when `path` is not one of them. */
export function assistantRoute(
  state: AssistantState,
  events: DecisionEvent[],
  sessions: Record<string, MockSessionRef & { stopped: boolean }>,
  revoked: (session: MockSessionRef) => boolean,
  request: { method: string; pathname: string; search: URLSearchParams; body: unknown; user: string; now: number },
): unknown {
  const { method, pathname, search, body, user, now } = request;
  const chatPath = pathname.match(/^\/sessions\/([^/]+)\/messages$/);
  if (chatPath) {
    const session = sessions[decodeURIComponent(chatPath[1])];
    if (!session || session.actingFor !== user) throw new MockError(404, "Unknown session");
    if (method === "POST") {
      if (revoked(session)) throw new MockError(403, "Session revoked or staffer inactive");
      const message: ChatMessage = { id: crypto.randomUUID(), ts: new Date(now).toISOString(), role: "user", text: (body as { text: string }).text };
      (state.chats[session.sid] ??= []).push(message);
      return message;
    }
    if (!revoked(session)) answerDue(state, events, session, now);
    const after = search.get("after");
    const messages = (state.chats[session.sid] ?? []).filter((m) => !after || `${m.ts}#${m.id}` > after);
    const last = messages[messages.length - 1];
    return { messages, next: last ? `${last.ts}#${last.id}` : after };
  }

  // "Allow for this session": only the staffer's own login, never chat text; locked categories never.
  const grantPath = pathname.match(/^\/sessions\/([^/]+)\/grants$/);
  if (grantPath && method === "POST") {
    const session = sessions[decodeURIComponent(grantPath[1])];
    if (!session || session.actingFor !== user) throw new MockError(404, "Unknown session");
    if (revoked(session)) throw new MockError(403, "Session revoked or staffer inactive");
    const category = (body as { category: string }).category;
    if (!(category in LABELS)) throw new MockError(404, "Unknown category");
    if (LOCKED.has(category)) throw new MockError(403, "This category always needs your review");
    const granted = ((state.grants ??= {})[session.sid] ??= []);
    if (!granted.includes(category)) granted.push(category);
    return { sid: session.sid, category, label: LABELS[category], scope: "this session only" };
  }

  if (method === "GET" && pathname === "/drafts") {
    const drafts = Object.values(state.drafts)
      .filter((d) => d.actingFor === user && d.status === "pending")
      .map((d) => ({ ...d, ...rankDraft(d, now) }));
    return { drafts: drafts.sort((a, b) => b.score - a.score || a.createdAt.localeCompare(b.createdAt)) };
  }
  const draftPath = pathname.match(/^\/drafts\/([^/]+)\/(approve|reject)$/);
  if (method === "POST" && draftPath) {
    const draft = state.drafts[decodeURIComponent(draftPath[1])];
    if (!draft || draft.actingFor !== user) throw new MockError(404, "Unknown draft");
    if (draft.status !== "pending") throw new MockError(409, `Draft already ${draft.status}`);
    const s = stats(state, user, draft.category);
    const session = sessions[draft.sid];
    if (draftPath[2] === "reject") {
      draft.status = "rejected";
      s.rejected += 1;
      return { id: draft.id, status: "rejected" };
    }
    const edits = (body ?? {}) as DraftEdits;
    const changed = Object.entries(edits).filter(([k, v]) => v !== undefined && draft.args[k] !== v);
    Object.assign(draft.args, edits);
    draft.status = "sent";
    if (changed.length) s.edited += 1;
    else s.approvedUnchanged += 1;
    events.push(event(session, draft.tool, draft.tool === "email_send" ? draft.args.to : draft.args.account, "approved by staff", now));
    return { id: draft.id, status: "sent", edited: changed.length > 0 };
  }

  if (method === "GET" && pathname === "/autonomy") {
    return { categories: CATEGORIES.map((c) => view(c, stats(state, user, c))) };
  }
  const autonomyPath = pathname.match(/^\/autonomy\/([^/]+)(\/snooze)?$/);
  if (autonomyPath) {
    const category = decodeURIComponent(autonomyPath[1]);
    if (!CATEGORIES.includes(category)) throw new MockError(404, "Unknown category");
    const s = stats(state, user, category);
    if (method === "POST" && autonomyPath[2]) {
      s.snoozedAt = reviews(s);
      return view(category, s);
    }
    if (method === "PUT" && !autonomyPath[2]) {
      const level = (body as { level: Stats["level"] }).level;
      if (level === "auto" && LOCKED.has(category)) throw new MockError(403, "This category always needs your review");
      s.level = level;
      return view(category, s);
    }
  }
  return undefined;
}
