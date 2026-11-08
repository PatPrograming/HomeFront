import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SessionCard } from "../lib/api";
import IdentityLine from "./IdentityLine";

const card: SessionCard = { sid: "s", agent: "agent-1", actingFor: "maya", office: "rep-x", mode: "official", allowedTools: [], expiresAt: "2026-11-08T05:15:00Z" };

describe("IdentityLine (staff)", () => {
  it("names the assistant by the conversation's task, without the raw id or the expiry", () => {
    const html = renderToStaticMarkup(<IdentityLine card={card} revoked={false} task="Post the town hall" />);
    expect(html).toContain("Your assistant acts for you, office rep-x");
    expect(html).toContain("Working on: Post the town hall");
    expect(html).not.toContain("agent-1");
    expect(html).not.toMatch(/ends at|5:15/i);
  });

  it("Revoked replaces it for everyone", () => {
    expect(renderToStaticMarkup(<IdentityLine card={card} revoked task="x" />)).toContain("Revoked");
  });
});
