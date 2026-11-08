// Pure helpers for the OAuth "authorization code + PKCE" login against Cognito managed login.
// PKCE in one line: we invent a random secret (the verifier), send only its SHA-256 hash (the
// challenge) when we redirect to Cognito, and prove we own it when we swap the code for tokens.
// Nothing here touches the browser URL or storage, so it is easy to test.

function base64url(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// 32 random bytes → 43 URL-safe characters (RFC 7636 allows 43–128).
export function randomString(byteLength = 32) {
  return base64url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

// S256 challenge = base64url(sha256(verifier)).
export async function challengeFor(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

// The managed-login page Cognito shows: https://<domain>/oauth2/authorize?...
export function authorizeUrl({ domain, clientId, redirectUri, state, challenge }) {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: redirectUri,
    scope: "openid email profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `https://${domain}/oauth2/authorize?${params}`;
}

// Form body for POST https://<domain>/oauth2/token. Public client: no secret, the verifier instead.
export function tokenRequestBody({ clientId, redirectUri, code, verifier }) {
  return new URLSearchParams({
    grant_type: "authorization_code",
    client_id: clientId,
    code,
    redirect_uri: redirectUri,
    code_verifier: verifier,
  });
}

export function logoutUrl({ domain, clientId, logoutUri }) {
  const params = new URLSearchParams({ client_id: clientId, logout_uri: logoutUri });
  return `https://${domain}/logout?${params}`;
}

// Reads a JWT's payload. This does NOT verify the signature: the browser only uses it to decide what
// to show. The API verifies every token against Cognito's keys before trusting it.
export function decodeJwt(token) {
  const part = token.split(".")[1];
  if (!part) throw new Error("not a JWT");
  const b64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
  const json = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
  return JSON.parse(json);
}
