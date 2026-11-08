import { describe, expect, it } from "vitest";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import sampleEvents from "../../../packages/mandate-contracts/samples/events.json";
import { eventKey, eventsPath, mergeEvents, nextCursor } from "./events";

const samples = sampleEvents as DecisionEvent[];
const ev = (id: string, ts: string): DecisionEvent => ({ ...samples[0], id, ts });

describe("nextCursor", () => {
  it("uses the server's next cursor", () => {
    expect(nextCursor(null, { events: [ev("a", "2026-11-07T20:00:00Z")], next: "srv" })).toBe("srv");
  });
  it("falls back to the newest event key when next is missing", () => {
    const page = { events: [ev("b", "2026-11-07T20:00:02Z"), ev("a", "2026-11-07T20:00:01Z")] };
    expect(nextCursor(null, page)).toBe("2026-11-07T20:00:02Z#b");
  });
  it("keeps the old cursor on an empty page", () => {
    expect(nextCursor("old", { events: [], next: null })).toBe("old");
    expect(nextCursor("old", { events: [] })).toBe("old");
  });
});

describe("mergeEvents", () => {
  it("puts the newest event first", () => {
    const merged = mergeEvents([ev("a", "2026-11-07T20:00:01Z")], [ev("b", "2026-11-07T20:00:03Z")]);
    expect(merged.map((e) => e.id)).toEqual(["b", "a"]);
  });
  it("does not duplicate an event sent twice", () => {
    const a = ev("a", "2026-11-07T20:00:01Z");
    expect(mergeEvents([a], [a, ev("b", "2026-11-07T20:00:02Z")])).toHaveLength(2);
  });
  it("caps the list at the limit, dropping the oldest", () => {
    const many = Array.from({ length: 5 }, (_, i) => ev(`e${i}`, `2026-11-07T20:00:0${i}Z`));
    expect(mergeEvents([], many, 3).map((e) => e.id)).toEqual(["e4", "e3", "e2"]);
  });
  it("orders the contract's sample events newest first", () => {
    const merged = mergeEvents([], samples);
    expect(merged[0].id).toBe(samples[samples.length - 1].id);
    expect(eventKey(merged[0]) > eventKey(merged[1])).toBe(true);
  });
});

describe("eventsPath", () => {
  it("omits since on the first poll and encodes it afterwards", () => {
    expect(eventsPath(null)).toBe("/events");
    expect(eventsPath("2026-11-07T20:00:00Z#a b")).toBe("/events?since=2026-11-07T20%3A00%3A00Z%23a%20b");
  });
});
