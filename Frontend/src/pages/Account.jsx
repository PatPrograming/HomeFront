import Layout from "../components/Layout.jsx";
import { useAuth } from "../lib/AuthProvider.jsx";
import "./Console.css";

// Who the console thinks you are, read from your Cognito tokens (WP1 check: email + group).
export default function Account() {
  const { session, signOut } = useAuth();

  return (
    <Layout>
      <div className="mandate-console login">
        <section className="panel">
          <h1>Account</h1>
          <dl className="facts">
            <div>
              <dt>Username</dt>
              <dd>{session.username}</dd>
            </div>
            <div>
              <dt>Email</dt>
              <dd>{session.email ?? <span className="muted">—</span>}</dd>
            </div>
            <div>
              <dt>Groups</dt>
              <dd>{session.groups.length ? session.groups.join(", ") : <span className="muted">none</span>}</dd>
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
