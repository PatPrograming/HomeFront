import { describe, expect, it, vi } from "vitest";

vi.mock("./mode", () => ({ MOCK: false }));
vi.mock("./auth", () => ({ getAccessToken: () => "t" }));
import { buildBrief, buildFirstTask, badLinks, type BriefInput } from "./agentBrief";

const base: BriefInput = {
  platforms: [{ id: "x", name: "X" }],
  mailboxes: [],
  mailSystems: [],
  mailType: "",
  perDay: "",
  action: "Draft",
  restrictions: "",
  message: "",
  links: "",
};

describe("agent brief", () => {
  it("is empty when nothing was answered", () => {
    expect(buildBrief("market_research", base)).toBe("");
  });

  it("lists social platforms, action and restrictions", () => {
    const brief = buildBrief("social", { ...base, restrictions: "No politics" });
    expect(brief).toContain("Platforms the agency is active on: X");
    expect(brief).toContain("What you may do: Draft");
    expect(brief).toContain("No politics");
  });

  it("lists email answers", () => {
    const brief = buildBrief("email", { ...base, mailboxes: ["Press", "FOIA"], perDay: "250", mailType: "FAQs" });
    expect(brief).toContain("Mailboxes you handle: Press, FOIA");
    expect(brief).toContain("Emails received per day: 250");
  });

  it("starts no task without a message", () => {
    expect(buildFirstTask("social", base)).toBe("");
  });

  it("builds a social first task with normalized links", () => {
    const task = buildFirstTask("social", { ...base, message: "Roads closed", links: "agency.gov/update" });
    expect(task).toContain('X (platform "x")');
    expect(task).toContain("- https://agency.gov/update");
    expect(task).toContain("Roads closed");
  });

  it("flags invalid links", () => {
    expect(badLinks("agency.gov\nnot a link")).toBe(true);
    expect(badLinks("agency.gov")).toBe(false);
  });
});
