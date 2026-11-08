import { describe, expect, it } from "vitest";
import {
  authorizeUrl,
  challengeFor,
  decodeJwt,
  logoutUrl,
  randomString,
  tokenRequestBody,
} from "./pkce";

// Vitest runs in Node, so Node's Buffer is there; it builds test tokens independently of the code under test.
declare const Buffer: { from(text: string): { toString(encoding: "base64url"): string } };

describe("PKCE helpers", () => {
  it("makes a URL-safe verifier of 43 characters", () => {
    const v = randomString();
    expect(v).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(randomString()).not.toBe(v);
  });

  it("computes the RFC 7636 S256 challenge", async () => {
    // Test vector from RFC 7636, appendix B.
    const verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
    expect(await challengeFor(verifier)).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it("builds the Cognito authorize URL with code + S256", () => {
    const url = new URL(
      authorizeUrl({
        domain: "mandate.auth.us-east-1.amazoncognito.com",
        clientId: "abc",
        redirectUri: "http://localhost:3000/auth/callback",
        state: "s1",
        challenge: "c1",
      }),
    );
    expect(url.origin + url.pathname).toBe("https://mandate.auth.us-east-1.amazoncognito.com/oauth2/authorize");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      response_type: "code",
      client_id: "abc",
      redirect_uri: "http://localhost:3000/auth/callback",
      scope: "openid email profile",
      state: "s1",
      code_challenge: "c1",
      code_challenge_method: "S256",
    });
  });

  it("sends the verifier and no client secret in the token request", () => {
    const body = tokenRequestBody({ clientId: "abc", redirectUri: "http://x/cb", code: "k", verifier: "v" });
    expect(Object.fromEntries(body)).toEqual({
      grant_type: "authorization_code",
      client_id: "abc",
      code: "k",
      redirect_uri: "http://x/cb",
      code_verifier: "v",
    });
    expect(body.has("client_secret")).toBe(false);
  });

  it("builds the logout URL", () => {
    expect(logoutUrl({ domain: "d.example", clientId: "abc", logoutUri: "http://x/" })).toBe(
      "https://d.example/logout?client_id=abc&logout_uri=http%3A%2F%2Fx%2F",
    );
  });

  it("decodes a JWT payload (base64url, unicode)", () => {
    const payload = { username: "maya", "cognito:groups": ["staffer"], name: "Mấy" };
    const b64 = Buffer.from(JSON.stringify(payload)).toString("base64url");
    expect(decodeJwt(`h.${b64}.sig`)).toEqual(payload);
    expect(() => decodeJwt("garbage")).toThrow();
  });
});
