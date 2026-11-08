import { useEffect, useState } from "react";
import Layout from "../components/Layout";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/AuthProvider";
import "./Auth.css";

type Profile = {
  id: string;
  username?: string | null;
  full_name?: string | null;
  avatar_url?: string | null;
  created_at?: string;
  updated_at?: string;
};

export default function Account() {
  const { user, signOut } = useAuth();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .me()
      .then(setProfile)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <Layout>
      <div className="auth">
        <section className="auth-card auth-card-wide">
          <h1>Account</h1>
          <p className="auth-sub">
            Signed in as <strong>{user?.email}</strong>
          </p>
          {error && (
            <p role="alert" className="alert alert-error">
              Backend: {error}
            </p>
          )}
          {profile && (
            <dl className="auth-details">
              {profile.full_name && (
                <>
                  <dt>Name</dt>
                  <dd>{profile.full_name}</dd>
                </>
              )}
              {profile.username && (
                <>
                  <dt>Username</dt>
                  <dd>{profile.username}</dd>
                </>
              )}
              {profile.created_at && (
                <>
                  <dt>Member since</dt>
                  <dd>{new Date(profile.created_at).toLocaleDateString()}</dd>
                </>
              )}
            </dl>
          )}
          <button className="btn btn-outline" onClick={signOut}>
            Sign out
          </button>
        </section>
      </div>
    </Layout>
  );
}
