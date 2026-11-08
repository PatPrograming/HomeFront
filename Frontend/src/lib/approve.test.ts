import { describe, expect, it } from "vitest";
import { approveOutcome } from "./approve";

describe("approveOutcome", () => {
  it("a sent draft is done", () => {
    expect(approveOutcome({ id: "d", status: "sent" })).toEqual({ done: true });
  });

  it("a platform that isn't connected yet asks to connect it, and nothing is sent", () => {
    const out = approveOutcome({ id: "d", status: "needs_connection", provider: "x", label: "X", connectUrl: "/connections/x", note: "Connect X first." });
    expect(out).toEqual({ done: false, connect: { label: "X", url: "/connections/x", note: "Connect X first." } });
  });
});
