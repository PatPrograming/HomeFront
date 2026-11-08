// Cursor logic for the live feed. The API returns `{ events, next }` for GET /events?since=<cursor>;
// the cursor is the last event's sort key, which on the server is the DynamoDB sk "<ts>#<id>".

/** @typedef {import("../../../packages/mandate-contracts/ts/index.ts").DecisionEvent} DecisionEvent */

/** @param {DecisionEvent} event */
export function eventKey(event) {
  return `${event.ts}#${event.id}`;
}

// The cursor for the next poll: the server's `next`, else the newest key we got, else the old cursor
// (an empty page must never reset the cursor, or we would download the whole history again).
export function nextCursor(cursor, page) {
  if (page?.next) return page.next;
  const keys = (page?.events ?? []).map(eventKey).sort();
  return keys.length ? keys[keys.length - 1] : cursor;
}

// Adds a page of events to what is on screen: no duplicates (a retried poll can resend events),
// newest first, and at most `limit` rows.
export function mergeEvents(current, incoming, limit = Infinity) {
  const byId = new Map(current.map((e) => [e.id, e]));
  for (const e of incoming) byId.set(e.id, e);
  return [...byId.values()]
    .sort((a, b) => (eventKey(a) < eventKey(b) ? 1 : -1))
    .slice(0, limit);
}

export function eventsPath(cursor) {
  return cursor ? `/events?since=${encodeURIComponent(cursor)}` : "/events";
}
