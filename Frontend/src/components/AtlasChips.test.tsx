import { describe, expect, it } from "vitest";
import { atlasHoverText, type AtlasEntry } from "./AtlasChips";

describe("atlasHoverText", () => {
  it("joins name and full description for hover", () => {
    const entry: AtlasEntry = {
      id: "AML.M0028",
      name: "Enforce Access Controls for AI Agent Tools",
      description: "Limit AI agent tool access to authorized operations.",
    };
    expect(atlasHoverText(entry, "AML.M0028")).toBe(
      "Enforce Access Controls for AI Agent Tools\n\nLimit AI agent tool access to authorized operations.",
    );
  });

  it("falls back to the id when the pack entry is missing", () => {
    expect(atlasHoverText(undefined, "AML.T0053")).toBe("AML.T0053");
  });
});
