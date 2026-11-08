import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Chat } from "../lib/useChat";

// Capture what ChatPanel hands the recovery buttons, then press Try again by calling it (no DOM library needed).
let pressed: { onRetry?: () => void } = {};
vi.mock("./StartFailure", () => ({
  default: (props: { onRetry: () => void }) => {
    pressed = props;
    return null;
  },
}));

const { default: ChatPanel } = await import("./ChatPanel");

describe("pressing Try again in the chat", () => {
  it("resets the conversation on the server first, then resends the refused message", async () => {
    const calls: string[] = [];
    const chat = { messages: [], error: null, send: async (text: string) => void calls.push(`send:${text}`) } as unknown as Chat;
    renderToStaticMarkup(
      <ChatPanel
        chat={chat}
        sid="s1"
        disabled={false}
        retryAgent={async () => void calls.push("retry")}
        initialFailure={{ error: "stopped 3 times", actions: ["retry", "new_conversation"], text: "check my email" }}
      />,
    );

    pressed.onRetry!();
    await vi.waitFor(() => expect(calls).toEqual(["retry", "send:check my email"]));
  });
});
