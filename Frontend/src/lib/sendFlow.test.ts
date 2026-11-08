import { describe, expect, it } from "vitest";
import { attemptSend, retryThenResend } from "./sendFlow";

// The shape ApiError has: the server's JSON body under `data`.
const apiError = (status: number, detail: unknown) => Object.assign(new Error(typeof detail === "string" ? detail : "x"), { status, data: { detail } });
const cap = apiError(503, { message: "stopped 3 times", code: "agent_start_cap", actions: ["retry", "new_conversation"] });

describe("sending, and Try again", () => {
  it("a refused send keeps the reason and the ways back the server offered", async () => {
    const failed = await attemptSend(async () => Promise.reject(cap), "hello");
    expect(failed).toEqual({ error: "x", actions: ["retry", "new_conversation"] });
    expect(await attemptSend(async () => {}, "hello")).toBeNull();
  });

  it("Try again resets first, then resends the same message", async () => {
    const calls: string[] = [];
    const failed = await retryThenResend(
      async () => void calls.push("retry"),
      async (text) => void calls.push(`send:${text}`),
      "check my email",
    );
    expect(failed).toBeNull();
    expect(calls).toEqual(["retry", "send:check my email"]);
  });

  it("a refused Try again does not resend, and says what is still possible", async () => {
    const calls: string[] = [];
    const refused = apiError(429, { message: "Please wait 40 s", code: "agent_retry_refused", actions: ["retry", "new_conversation"] });
    const failed = await retryThenResend(
      async () => Promise.reject(refused),
      async (text) => void calls.push(`send:${text}`),
      "check my email",
    );
    expect(calls).toEqual([]);
    expect(failed?.actions).toEqual(["retry", "new_conversation"]);
  });

  it("if the resend after Try again fails, that failure is what shows", async () => {
    const failed = await retryThenResend(async () => {}, async () => Promise.reject(cap), "x");
    expect(failed?.actions).toEqual(["retry", "new_conversation"]);
  });
});
