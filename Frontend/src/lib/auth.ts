// Cognito login for the staff console: managed-login redirect, authorization code + PKCE, plain fetch.
// Tokens live in memory and in sessionStorage (this tab only, gone when the tab closes); never localStorage.
// There is no refresh: when the access token expires (1 h by default) the staffer signs in again.
import { MOCK } from "./mode";
import { authorizeUrl, challengeFor, decodeJwt, logoutUrl, randomString, tokenRequestBody } from "./pkce";
import { groupsFromAccessToken } from "./roles";

const SESSION_KEY = "mandate.session";
const PENDING_KEY = "mandate.pkce"; // verifier + state, kept only across the redirect to Cognito

/** The signed-in staffer, as the UI sees it. */
export interface Session {
  accessToken: string;
  username: string;
  email: string | null;
  groups: string[];
  /** Epoch milliseconds when the access token expires. */
  expiresAt: number;
}

/** Cognito's /oauth2/token answer (only the fields we use). */
export interface TokenResponse {
  access_token: string;
  id_token?: string;
  expires_in?: number;
}

interface Pending {
  verifier: string;
  state: string;
  returnTo: string | null;
}

export const cognito = {
  domain: (import.meta.env.VITE_COGNITO_DOMAIN || "").replace(/^https?:\/\//, "").replace(/\/$/, ""),
  clientId: import.meta.env.VITE_COGNITO_CLIENT_ID || "",
  // Must be one of the staff-web client's callback URLs (lane A set the site root: http://localhost:3000/).
  redirectUri: import.meta.env.VITE_COGNITO_REDIRECT_URI || `${window.location.origin}/`,
};

export const cognitoConfigured = Boolean(cognito.domain && cognito.clientId);

let current: Session | null = null;

const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);

// Turns Cognito's token response into what the UI needs.
export function sessionFromTokens({ access_token, id_token, expires_in }: TokenResponse): Session {
  const access = decodeJwt(access_token);
  const id = id_token ? decodeJwt(id_token) : {};
  const exp = typeof access.exp === "number" ? access.exp : null;
  return {
    accessToken: access_token,
    username: str(access.username) ?? str(id["cognito:username"]) ?? str(access.sub) ?? "",
    email: str(id.email) ?? null,
    groups: groupsFromAccessToken(access_token),
    expiresAt: exp ? exp * 1000 : Date.now() + (expires_in ?? 0) * 1000,
  };
}

function save(session: Session | null): void {
  current = session;
  if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  else sessionStorage.removeItem(SESSION_KEY);
}

// The signed-in session, or null when there is none or it has expired.
export function loadSession(): Session | null {
  if (!current) {
    try {
      current = JSON.parse(sessionStorage.getItem(SESSION_KEY) ?? "null") as Session | null;
    } catch {
      current = null;
    }
  }
  if (current && current.expiresAt <= Date.now()) save(null);
  return current;
}

export function getAccessToken(): string | null {
  return loadSession()?.accessToken ?? null;
}

// Step 1: remember a fresh verifier + state, then send the browser to Cognito's sign-in page.
// `returnTo` is the page the staffer asked for; without one, AuthProvider picks /admin or /console by group.
export async function beginLogin(returnTo: string | null = null): Promise<void> {
  const verifier = randomString();
  const state = randomString(16);
  const pending: Pending = { verifier, state, returnTo };
  sessionStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  window.location.assign(authorizeUrl({ ...cognito, state, challenge: await challengeFor(verifier) }));
}

// Step 2 (on /auth/callback): check state, swap the one-time code for tokens. Returns where to go next.
export async function completeLogin(search: string): Promise<string | null> {
  const params = new URLSearchParams(search);
  const pending = JSON.parse(sessionStorage.getItem(PENDING_KEY) || "null") as Pending | null;
  sessionStorage.removeItem(PENDING_KEY);

  const error = params.get("error");
  if (error) throw new Error(params.get("error_description") || error);
  const code = params.get("code");
  if (!pending || !code || params.get("state") !== pending.state) {
    throw new Error("Login response did not match this browser tab. Please sign in again.");
  }

  const res = await fetch(`https://${cognito.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: tokenRequestBody({ ...cognito, code, verifier: pending.verifier }),
  });
  if (!res.ok) throw new Error(`Cognito token exchange failed (HTTP ${res.status})`);
  save(sessionFromTokens((await res.json()) as TokenResponse));
  return pending.returnTo;
}

// Clears the tab's tokens, then ends the Cognito session too (skipped in mock mode).
export function signOut(): void {
  save(null);
  if (!MOCK && cognitoConfigured) {
    window.location.assign(logoutUrl({ ...cognito, logoutUri: `${window.location.origin}/` }));
  }
}

// Mock mode only: sign in as a demo user with an unsigned token shaped like Cognito's.
export function mockSignIn(username: string, groups: string[]): void {
  if (!MOCK) throw new Error("mock sign-in is only available in mock mode");
  const exp = Math.floor(Date.now() / 1000) + 3600;
  const payload = btoa(JSON.stringify({ username, "cognito:groups": groups, token_use: "access", exp }));
  save(sessionFromTokens({ access_token: `mock.${payload.replace(/=+$/, "")}.mock` }));
}
