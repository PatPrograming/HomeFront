import { describe, expect, it } from "vitest";
import { gate, groupsFromAccessToken, isAdmin } from "./roles.js";

const jwt = (payload) => `h.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.sig`;

describe("groupsFromAccessToken", () => {
  it("reads cognito:groups", () => {
    expect(groupsFromAccessToken(jwt({ "cognito:groups": ["admin"] }))).toEqual(["admin"]);
  });
  it("returns no groups when the claim is missing or malformed", () => {
    expect(groupsFromAccessToken(jwt({ username: "x" }))).toEqual([]);
    expect(groupsFromAccessToken(jwt({ "cognito:groups": "admin" }))).toEqual([]);
  });
});

describe("gate", () => {
  const maya = { username: "maya", groups: ["staffer"] };
  const chief = { username: "chiefofstaff", groups: ["admin"] };
  const nobody = { username: "x", groups: [] };

  it("sends a signed-out visitor to login, for any route", () => {
    expect(gate(null)).toBe("login");
    expect(gate(null, "admin")).toBe("login");
  });
  it("lets staffers and admins into the staff console", () => {
    expect(gate(maya)).toBe("ok");
    expect(gate(chief)).toBe("ok");
  });
  it("keeps users with no group out", () => {
    expect(gate(nobody)).toBe("forbidden");
  });
  it("lets only admins into admin routes", () => {
    expect(gate(maya, "admin")).toBe("forbidden");
    expect(gate(nobody, "admin")).toBe("forbidden");
    expect(gate(chief, "admin")).toBe("ok");
    expect(isAdmin(chief)).toBe(true);
    expect(isAdmin(maya)).toBe(false);
  });
});
