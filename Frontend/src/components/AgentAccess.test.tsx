import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AgentAccessView } from "./AgentAccessView";

describe("which AWS credentials the agent runs with (admins)", () => {
  it("warns when the agent shares the server's credentials, and says how to fix it", () => {
    const html = renderToStaticMarkup(<AgentAccessView mode="shared-server" />);
    expect(html).toContain("shares the server&#x27;s AWS credentials");
    expect(html).toContain("HOMEFRONT_AGENT_ROLE_ARN");
  });

  it("says so quietly when it has its own role, and nothing before the answer arrives", () => {
    expect(renderToStaticMarkup(<AgentAccessView mode="limited-role" />)).toContain("Bedrock-only");
    expect(renderToStaticMarkup(<AgentAccessView mode={null} />)).toBe("");
  });
});
