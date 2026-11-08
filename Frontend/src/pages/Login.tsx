import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import Layout from "../components/Layout";
import { useAuth } from "../lib/AuthProvider";
import { MOCK } from "../lib/mode";
import { isAdmin } from "../lib/roles";
import "./Auth.css";
import "./Console.css";

// Sign-in happens on Cognito's own managed-login page; this page only sends the browser there.
// In mock mode it offers the two demo users instead.
export default function Login() {
  const { session, configured, signIn, mockSignIn } = useAuth();
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from ?? null;
  const [error, setError] = useState<string | null>(null);

  if (session) return <Navigate to={from || (isAdmin(session) ? "/admin" : "/")} replace />;

  async function cognitoSignIn() {
    try {
      await signIn(from);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function demoSignIn(username: string, groups: string[], to: string) {
    mockSignIn(username, groups);
    navigate(from || to, { replace: true });
  }

  async function resetDemo() {
    // The static MOCK check lets the bundler drop mockApi.ts from production builds.
    if (MOCK) {
      const { resetMock } = await import("../lib/mockApi");
      resetMock();
      setError("Demo data reset.");
    }
  }

  return (
    <Layout>
      <div className="auth">
        <section className="auth-card">
          <h1>Sign in to HomeFront</h1>
          {MOCK ? (
            <>
              <p className="alert alert-info" role="note">
                Mock mode: no Cognito, no backend. Pick a demo user.
              </p>
              <div className="login-actions">
                <button type="button" className="btn btn-primary" onClick={() => demoSignIn("maya", ["staffer"], "/console")}>
                  Sign in as Maya (staffer)
                </button>
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => demoSignIn("chiefofstaff", ["admin"], "/admin")}
                >
                  Sign in as Chief of Staff (admin)
                </button>
                <button type="button" className="btn btn-ghost" onClick={resetDemo}>
                  Reset demo data
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="auth-sub">You will sign in on the office&apos;s Amazon Cognito page, then come straight back here.</p>
              {!configured && (
                <p className="alert alert-error" role="alert">
                  Cognito is not configured. Set <code>VITE_COGNITO_DOMAIN</code> and <code>VITE_COGNITO_CLIENT_ID</code>{" "}
                  in the repo-root <code>.env</code> (see Frontend/README.md).
                </p>
              )}
              <div className="login-actions">
                <button type="button" className="btn btn-primary" onClick={cognitoSignIn} disabled={!configured}>
                  Sign in with Cognito
                </button>
              </div>
            </>
          )}
          {error && <p role="status">{error}</p>}
        </section>
      </div>
    </Layout>
  );
}
