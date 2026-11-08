import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { DecisionEvent } from "../../../packages/mandate-contracts/ts/index";
import { describeEvent } from "../lib/agentActivity";
import { agentActions } from "../lib/errors";
import type { Chat } from "../lib/useChat";
import ChatPanel from "./ChatPanel";
import StartFailure from "./StartFailure";

const noop = () => {};

describe("when the assistant can't start", () => {
  it("reads the actions the server offers from a 503, and nothing from other errors", () => {
    // The shape ApiError has: the server's JSON body under `data`.
    const apiError = (body: unknown) => Object.assign(new Error("x"), { status: 503, data: body });
    const cap = apiError({ detail: { message: "stopped 3 times", code: "agent_start_cap", actions: ["retry", "new_conversation"] } });
    expect(agentActions(cap)).toEqual(["retry", "new_conversation"]);
    expect(agentActions(apiError({ detail: "The assistant could not start: expired" }))).toEqual([]);
    expect(agentActions(apiError({ detail: { actions: ["delete_everything"] } }))).toEqual([]);
    expect(agentActions(new TypeError("Failed to fetch"))).toEqual([]);
  });

  it("offers Try again and a new conversation", () => {
    const html = renderToStaticMarkup(
      <StartFailure actions={["retry", "new_conversation"]} onRetry={noop} onNewConversation={noop} busy={false} />,
    );
    expect(html).toContain("Try again");
    expect(html).toContain("Start a new conversation");
  });

  it("shows nothing when there is no way back to offer", () => {
    expect(renderToStaticMarkup(<StartFailure actions={[]} onRetry={noop} busy={false} />)).toBe("");
    expect(renderToStaticMarkup(<StartFailure actions={["new_conversation"]} onRetry={noop} busy={false} />)).toBe("");
  });
});

describe("why the assistant stopped", () => {
  const chatWith = (detail: string): Chat =>
    ({
      messages: [
        { id: "u1", ts: "2026-11-08T08:00:00Z", role: "user", text: "Check my email" },
        { id: "a1", ts: "2026-11-08T08:00:05Z", role: "assistant", text: "I stopped: the AI model is busy right now.", detail },
      ],
      error: null,
      send: async () => {},
    }) as unknown as Chat;
  const activity = (admin: boolean) => ({ card: null, events: [], categories: [], grants: [], admin });

  it("shows admins the technical detail and staffers only the plain words", () => {
    const forAdmin = renderToStaticMarkup(<ChatPanel chat={chatWith("ClientError: ThrottlingException")} sid="s1" disabled={false} activity={activity(true)} />);
    const forStaff = renderToStaticMarkup(<ChatPanel chat={chatWith("ClientError: ThrottlingException")} sid="s1" disabled={false} activity={activity(false)} />);
    expect(forAdmin).toContain("ClientError: ThrottlingException");
    expect(forStaff).toContain("busy right now");
    expect(forStaff).not.toContain("ThrottlingException");
  });

  it("describes a server problem as a server problem, not as something the assistant may not do", () => {
    const event = { decision: "DENY", reason: "AWS credentials expired on the server", tool: "inbox_read", resource: "email-1", policyIds: [], atlas: [] } as unknown as DecisionEvent;
    const { text, blocked } = describeEvent(event);
    expect(blocked).toBe(true);
    expect(text).toContain("AWS sign-in has expired");
    expect(text).not.toContain("outside what it may do");
  });
});
