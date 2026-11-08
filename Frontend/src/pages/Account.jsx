import { useEffect, useState } from "react";
import Layout from "../components/Layout.jsx";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/AuthProvider.jsx";
import "./Console.css";

// Who the API says you are (GET /api/v1/me checks your Cognito token): the WP1 "email + group" check.
// Read-only: profiles live in Cognito, not in this app.
export default function Account() {
  const { session, signOut } = useAuth();
  const [me, setMe] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.me().then(setMe).catch((e) => setError(e.message || String(e)));
  }, []);

  const dash = <span className="muted">—</span>;

  return (
    <Layout>
      <div className="mandate-console login">
        <section className="panel">
          <h1>Account</h1>
          {error && (
            <p className="alert" role="alert">
              The API could not confirm your sign-in: {error}
            </p>
          )}
          <dl className="facts">
            <div>
              <dt>Username</dt>
              <dd>{me?.username ?? dash}</dd>
            </div>
            <div>
              <dt>Email</dt>
              <dd>{me?.email ?? dash}</dd>
            </div>
            <div>
              <dt>Groups</dt>
              <dd>{me ? me.groups.join(", ") || "none" : dash}</dd>
            </div>
            <div className="sid">
              <dt>Cognito sub</dt>
              <dd>{me ? <code>{me.sub}</code> : dash}</dd>
            </div>
            <div>
              <dt>Signed in until</dt>
              <dd>{new Date(session.expiresAt).toLocaleTimeString([], { hour12: false })}</dd>
            </div>
          </dl>
          <div className="login-actions">
            <button type="button" className="btn-plain" onClick={signOut}>
              Sign out
            </button>
          </div>
        </section>
      </div>
    </Layout>
  );
}
