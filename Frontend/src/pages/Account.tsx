import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/AuthProvider";
import "./Auth.css";

// First end-to-end check: browser -> Cognito ID token -> FastAPI /api/v1/me -> DynamoDB.
export default function Account() {
  const { user, signOut } = useAuth();
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.me().then(setProfile).catch((e) => setError(e.message));
  }, []);

  return (
    <Layout>
      <div className="auth">
        <section className="auth-card auth-card-wide">
          <h1>Account</h1>
          <p className="auth-sub">
            Signed in as <strong>{user?.email}</strong>
          </p>
          {error && <p role="alert" className="alert alert-error">Backend: {error}</p>}
          {profile && <pre className="auth-pre">{JSON.stringify(profile, null, 2)}</pre>}
          <button className="btn btn-outline" onClick={signOut}>
            Sign out
          </button>
        </section>
      </div>
    </Layout>
  );
}
