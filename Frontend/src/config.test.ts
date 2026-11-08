import { describe, expect, it } from "vitest";
import { ADMIN_LINK, NAV_LINKS } from "./config";

describe("nav tabs", () => {
  it("puts Your assistant after Home and before the teammates' agent tabs", () => {
    expect(NAV_LINKS.map((l) => l.label)).toEqual([
      "Home",
      "Your assistant",
      "Agents",
      "Monitoring",
      "Approvals",
      "Activity",
      "Account",
    ]);
    expect(NAV_LINKS.find((l) => l.label === "Your assistant")?.href).toBe("/console");
  });

  it("keeps Admin out of the shared tabs (the navbar adds it for the admin group only)", () => {
    expect(NAV_LINKS).not.toContainEqual(ADMIN_LINK);
  });
});
