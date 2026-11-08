import { decodeJwt } from "./pkce";

// Cognito groups (lane A): Maya is in "staffer", the Chief of Staff in "admin".
export const STAFFER = "staffer";
export const ADMIN = "admin";

// Groups come from the ACCESS token's `cognito:groups` claim (the same token the API checks).
export function groupsFromAccessToken(accessToken: string): string[] {
  const groups = decodeJwt(accessToken)["cognito:groups"];
  return Array.isArray(groups) ? groups.filter((g): g is string => typeof g === "string") : [];
}

/** The part of a session the role checks need. */
export interface HasGroups {
  groups?: string[];
}

export type Role = typeof ADMIN | "any" | undefined;
export type Verdict = "login" | "forbidden" | "ok";

export function isAdmin(session: HasGroups | null | undefined): boolean {
  return Boolean(session?.groups?.includes(ADMIN));
}

// What a route guard should do: "login" (no session), "forbidden" (wrong group) or "ok".
// `role` is optional; without it any signed-in staffer or admin may enter. role="any" lets in anyone
// signed in, even with no group (the Account page, so they can see why and sign out).
export function gate(session: HasGroups | null | undefined, role?: Role): Verdict {
  if (!session) return "login";
  if (role === "any") return "ok";
  const groups = session.groups ?? [];
  if (role === ADMIN) return groups.includes(ADMIN) ? "ok" : "forbidden";
  return groups.includes(STAFFER) || groups.includes(ADMIN) ? "ok" : "forbidden";
}
