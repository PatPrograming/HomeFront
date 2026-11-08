import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SessionCard } from "../lib/api";
import IdentityLine from "./IdentityLine";

const roleCard: SessionCard = {
  sid: "sid-123",
  agent: "agent-1",
  actingFor: "maya",
  office: "rep-x",
  mode: "official",
  role: "caseworker",
  roleLabel: "Caseworker",
  allowedTools: ["inbox_read", "case_read"],
  expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
};

describe("IdentityLine", () => {
  it("shows the staffer and their office role in plain words", () => {
    const html = renderToStaticMarkup(<IdentityLine card={roleCard} revoked={false} />);
    expect(html).toContain("Maya · Caseworker");
    expect(html).not.toContain("caseworker"); // the raw role id stays out of the staff view
  });

  it("leaves the role out when the server sent none (older broker)", () => {
    const { roleLabel: _label, role: _role, ...old } = roleCard;
    const html = renderToStaticMarkup(<IdentityLine card={old} revoked={false} />);
    expect(html).not.toContain("identity-role");
    expect(html).toContain("acts for you");
  });
});

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
