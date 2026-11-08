import { decodeJwt } from "./pkce.js";

// Cognito groups (lane A): Maya is in "staffer", the Chief of Staff in "admin".
export const STAFFER = "staffer";
export const ADMIN = "admin";

// Groups come from the ACCESS token's `cognito:groups` claim (the same token the API checks).
export function groupsFromAccessToken(accessToken) {
  const groups = decodeJwt(accessToken)["cognito:groups"];
  return Array.isArray(groups) ? groups : [];
}

export function isAdmin(session) {
  return Boolean(session?.groups?.includes(ADMIN));
}

// What a route guard should do: "login" (no session), "forbidden" (wrong group) or "ok".
// `role` is optional; without it any signed-in staffer or admin may enter.
export function gate(session, role) {
  if (!session) return "login";
  const groups = session.groups ?? [];
  if (role === ADMIN) return groups.includes(ADMIN) ? "ok" : "forbidden";
  return groups.includes(STAFFER) || groups.includes(ADMIN) ? "ok" : "forbidden";
}
