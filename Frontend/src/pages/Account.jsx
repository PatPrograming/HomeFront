import { useEffect, useState } from "react";
import Layout from "../components/Layout.jsx";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/AuthProvider.jsx";

// First end-to-end check: browser -> Supabase JWT -> FastAPI /api/v1/me -> Postgres (RLS).
export default function Account() {
  const { user, signOut } = useAuth();
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.me().then(setProfile).catch((e) => setError(e.message));
  }, []);

  return (
    <Layout>
      <section style={{ maxWidth: 520, margin: "48px auto", display: "grid", gap: 12 }}>
        <h1>Account</h1>
        <p>Signed in as {user?.email}</p>
        {error && <p role="alert" style={{ color: "crimson" }}>Backend: {error}</p>}
        {profile && <pre>{JSON.stringify(profile, null, 2)}</pre>}
        <button className="btn btn-outline" onClick={signOut}>
          Sign out
        </button>
      </section>
    </Layout>
  );
}
