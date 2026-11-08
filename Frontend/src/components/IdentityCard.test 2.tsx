import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SessionCard } from "../lib/api";
import IdentityCard from "./IdentityCard";

const card: SessionCard = {
  sid: "sid-123",
  agent: "agent-1",
  actingFor: "maya",
  office: "rep-x",
  mode: "official",
  allowedTools: ["inbox_read", "case_read"],
  expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
};

describe("IdentityCard", () => {
  it("counts down the session, not the mandate", () => {
    const html = renderToStaticMarkup(<IdentityCard card={card} revoked={false} />);
    expect(html).toContain("Session expires in");
    expect(html).not.toContain("Mandate expires");
    expect(html).not.toContain("Revoked");
    expect(html).toContain("<code>sid-123</code>");
  });

  it("shows the revoked state: no countdown and a greyed session id", () => {
    const html = renderToStaticMarkup(<IdentityCard card={card} revoked />);
    expect(html).toContain("Revoked: access was removed by the Chief of Staff");
    expect(html).not.toContain("expires in");
    expect(html).toContain('<code class="sid-revoked">sid-123</code>');
    expect(html).toContain("is-revoked");
  });
});
