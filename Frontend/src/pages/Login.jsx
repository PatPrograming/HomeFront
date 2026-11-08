import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import Layout from "../components/Layout.jsx";
import { useAuth } from "../lib/AuthProvider.jsx";
import { MOCK } from "../lib/mode.js";
import { isAdmin } from "../lib/roles.js";
import "./Console.css";

// Sign-in happens on Cognito's own managed-login page; this page only sends the browser there.
// In mock mode it offers the two demo users instead.
export default function Login() {
  const { session, configured, signIn, mockSignIn } = useAuth();
  const navigate = useNavigate();
  const from = useLocation().state?.from;
  const [error, setError] = useState(null);

  if (session) return <Navigate to={from || (isAdmin(session) ? "/admin" : "/console")} replace />;

  async function cognitoSignIn() {
    try {
      await signIn(from);
    } catch (e) {
      setError(e.message || String(e));
    }
  }

  async function demoSignIn(username, groups, to) {
    mockSignIn(username, groups);
    navigate(from || to, { replace: true });
  }

  async function resetDemo() {
    // The static MOCK check lets the bundler drop mockApi.js from production builds.
    if (MOCK) {
      const { resetMock } = await import("../lib/mockApi.js");
      resetMock();
      setError("Demo data reset.");
    }
  }

  return (
    <Layout>
      <div className="mandate-console login">
        <section className="panel">
          <h1>Sign in to HomeFront</h1>
          {MOCK ? (
            <>
              <p className="alert" role="note">
                Mock mode: no Cognito, no backend. Pick a demo user.
              </p>
              <div className="login-actions">
                <button type="button" className="btn-primary" onClick={() => demoSignIn("maya", ["staffer"], "/console")}>
                  Sign in as Maya (staffer)
                </button>
                <button
                  type="button"
                  className="btn-plain"
                  onClick={() => demoSignIn("chiefofstaff", ["admin"], "/admin")}
                >
                  Sign in as Chief of Staff (admin)
                </button>
                <button type="button" className="btn-plain" onClick={resetDemo}>
                  Reset demo data
                </button>
              </div>
            </>
          ) : (
            <>
              <p>You will sign in on the office&apos;s Amazon Cognito page, then come straight back here.</p>
              {!configured && (
                <p className="alert" role="alert">
                  Cognito is not configured. Set <code>VITE_COGNITO_DOMAIN</code> and <code>VITE_COGNITO_CLIENT_ID</code>{" "}
                  in the repo-root <code>.env</code> (see Frontend/README.md).
                </p>
              )}
              <div className="login-actions">
                <button type="button" className="btn-primary" onClick={cognitoSignIn} disabled={!configured}>
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
