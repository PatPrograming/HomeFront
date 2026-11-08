// Cursor logic for the live feed. The API returns `{ events, next }` for GET /events?since=<cursor>;
// the cursor is the last event's sort key, which on the server is the DynamoDB sk "<ts>#<id>".

import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";

/** One page of GET /events: new events and the cursor for the next poll. */
export interface EventPage {
  events: DecisionEvent[];
  next?: string | null;
}

export function eventKey(event: Pick<DecisionEvent, "ts" | "id">): string {
  return `${event.ts}#${event.id}`;
}

// The cursor for the next poll: the server's `next`, else the newest key we got, else the old cursor
// (an empty page must never reset the cursor, or we would download the whole history again).
export function nextCursor(cursor: string | null, page: Partial<EventPage> | null | undefined): string | null {
  if (page?.next) return page.next;
  const keys = (page?.events ?? []).map(eventKey).sort();
  return keys.length ? keys[keys.length - 1] : cursor;
}

// Adds a page of events to what is on screen: no duplicates (a retried poll can resend events),
// newest first, and at most `limit` rows.
export function mergeEvents(
  current: DecisionEvent[],
  incoming: DecisionEvent[],
  limit = Infinity,
): DecisionEvent[] {
  const byId = new Map(current.map((e) => [e.id, e]));
  for (const e of incoming) byId.set(e.id, e);
  return [...byId.values()]
    .sort((a, b) => (eventKey(a) < eventKey(b) ? 1 : -1))
    .slice(0, limit);
}

export function eventsPath(cursor?: string | null): string {
  return cursor ? `/events?since=${encodeURIComponent(cursor)}` : "/events";
}
