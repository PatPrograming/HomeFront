import { describe, expect, it } from "vitest";
import { diffFromRecommended, initialSelection, type PermissionOption } from "./permissions";

// Options as GET /agent-permissions?kind= returns them (docs/loops/agent-permissions.md, Design 6).
const opt = (o: Partial<PermissionOption> & { name: string }): PermissionOption => ({
  label: o.name,
  effect: "does a thing",
  risk: "low",
  outbound: false,
  held_for_review: false,
  allowed_for_kind: true,
  allowed_for_role: true,
  recommended: false,
  reason_unavailable: null,
  ...o,
});

const SEND = opt({ name: "email.send", label: "Send email", effect: "Send an email to one person", risk: "high", outbound: true, held_for_review: true, recommended: true });
const LIST = opt({ name: "email.send_list", label: "Email the mailing list", effect: "Email everyone on the office mailing list", risk: "high", outbound: true, held_for_review: true });
const SCRAPE = opt({ name: "web.scrape", label: "Read web pages", effect: "Read public web pages", risk: "medium" });
const REPORT = opt({ name: "report.save", label: "Save reports", effect: "Save a report you can read later", risk: "low" });
const POST = opt({
  name: "social.post", label: "Post to social media", effect: "Post to the office social accounts", risk: "high", outbound: true, held_for_review: true,
  allowed_for_role: false, reason_unavailable: "Your role (Caseworker) can't post to social media.",
});
const OPTIONS = [SEND, LIST, SCRAPE, REPORT, POST];

const text = (line: object) => JSON.stringify(Object.values(line));

describe("initialSelection", () => {
  it("is exactly the server's recommended set", () => {
    expect(initialSelection(OPTIONS).sort()).toEqual(["email.send"]);
  });

  it("is empty when nothing is recommended (custom kind)", () => {
    expect(initialSelection(OPTIONS.map((o) => ({ ...o, recommended: false })))).toEqual([]);
  });

  it("never includes an unavailable tool, even one flagged recommended", () => {
    const broken = [{ ...POST, recommended: true }, SEND];
    expect(initialSelection(broken)).toEqual(["email.send"]);
  });
});

describe("diffFromRecommended", () => {
  it("reports no changes when the selection equals the recommendation", () => {
    expect(diffFromRecommended(OPTIONS, ["email.send"])).toEqual({ added: [], removed: [] });
  });

  it("lists a tool the user adds, with its plain-words effect and risk", () => {
    const { added, removed } = diffFromRecommended(OPTIONS, ["email.send", "web.scrape"]);
    expect(removed).toEqual([]);
    expect(added).toHaveLength(1);
    expect(added[0]).toMatchObject({ name: "web.scrape", effect: "Read public web pages", risk: "medium" });
  });

  it("marks every added tool as beyond the recommendation", () => {
    const { added } = diffFromRecommended(OPTIONS, ["email.send", "web.scrape", "report.save"]);
    expect(added.map((l) => l.name).sort()).toEqual(["report.save", "web.scrape"]);
    expect(added.every((l) => l.beyondRecommendation === true)).toBe(true);
  });

  it("lists a recommended tool the user unchecks as removed, not beyond the recommendation", () => {
    const { added, removed } = diffFromRecommended(OPTIONS, []);
    expect(added).toEqual([]);
    expect(removed).toHaveLength(1);
    expect(removed[0]).toMatchObject({ name: "email.send", effect: "Send an email to one person", beyondRecommendation: false });
  });

  it("reports a swap as one removal and one addition", () => {
    const { added, removed } = diffFromRecommended(OPTIONS, ["web.scrape"]);
    expect(added.map((l) => l.name)).toEqual(["web.scrape"]);
    expect(removed.map((l) => l.name)).toEqual(["email.send"]);
  });

  it("says an added outbound tool is held for review", () => {
    const { added } = diffFromRecommended(OPTIONS, ["email.send", "email.send_list"]);
    expect(added[0].name).toBe("email.send_list");
    expect(text(added[0])).toMatch(/held for review/i);
  });

  it("does not claim 'held for review' for an added tool that is not outbound", () => {
    const { added } = diffFromRecommended(OPTIONS, ["email.send", "web.scrape"]);
    expect(text(added[0])).not.toMatch(/held for review/i);
  });

  it("carries no Cedar or policy ids in the wording", () => {
    const { added } = diffFromRecommended(OPTIONS, ["email.send", "email.send_list"]);
    expect(text(added[0])).not.toMatch(/forbid|permit|role-|cedar|AML\./i);
  });

  it("ignores a selected tool that is unavailable instead of listing it as added", () => {
    const { added } = diffFromRecommended(OPTIONS, ["email.send", "social.post"]);
    expect(added.map((l) => l.name)).not.toContain("social.post");
  });

  it("ignores selected names the server never offered", () => {
    const { added } = diffFromRecommended(OPTIONS, ["email.send", "made.up"]);
    expect(added).toEqual([]);
  });
});
