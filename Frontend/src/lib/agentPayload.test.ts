import { describe, expect, it } from "vitest";
import { buildAgentPayload, clampSteps } from "./agentPayload";

describe("buildAgentPayload", () => {
  it("sends the job (kind), name, agency, instructions, tools and max steps", () => {
    expect(
      buildAgentPayload({
        kind: "social",
        name: "  Press bot ",
        agency: " Dept. of Housing ",
        instructions: "Post updates.",
        toolGrants: ["social.post", "social.post", "social.reply"],
        maxSteps: 12,
      }),
    ).toEqual({
      kind: "social",
      name: "Press bot",
      agency: "Dept. of Housing",
      instructions: "Post updates.",
      tool_grants: ["social.post", "social.reply"],
      max_steps: 12,
    });
  });

  it("keeps max steps inside the backend's 1-50 range", () => {
    expect(clampSteps(0)).toBe(1);
    expect(clampSteps(500)).toBe(50);
    expect(clampSteps(Number.NaN)).toBe(10);
  });
});
