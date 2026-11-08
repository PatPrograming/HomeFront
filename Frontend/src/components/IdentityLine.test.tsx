import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SessionCard } from "../lib/api";
import IdentityLine from "./IdentityLine";

const card: SessionCard = {
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
    const html = renderToStaticMarkup(<IdentityLine card={card} revoked={false} />);
    expect(html).toContain("Maya · Caseworker");
    expect(html).not.toContain("caseworker"); // the raw role id stays out of the staff view
  });

  it("leaves the role out when the server sent none (older broker)", () => {
    const { roleLabel: _label, role: _role, ...old } = card;
    const html = renderToStaticMarkup(<IdentityLine card={old} revoked={false} />);
    expect(html).not.toContain("identity-role");
    expect(html).toContain("acts for you");
  });
});
