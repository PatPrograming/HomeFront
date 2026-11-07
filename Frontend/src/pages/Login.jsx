import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import Layout from "../components/Layout.jsx";
import { useAuth } from "../lib/AuthProvider.jsx";

export default function Login() {
  const { user, loading, configured, signIn, signUp } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const redirectTo = location.state?.from || "/account";

  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  if (!loading && user) return <Navigate to={redirectTo} replace />;

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "signin") {
        const { error: err } = await signIn(email, password);
        if (err) throw err;
        navigate(redirectTo, { replace: true });
      } else {
        const { data, error: err } = await signUp(email, password, fullName);
        if (err) throw err;
        // With email confirmation on, there is no session until the link is clicked.
        if (data.session) navigate(redirectTo, { replace: true });
        else setNotice("Check your email to confirm your account, then sign in.");
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Layout>
      <form onSubmit={onSubmit} style={{ maxWidth: 380, margin: "48px auto", display: "grid", gap: 12 }}>
        <h1>{mode === "signin" ? "Sign in" : "Create account"}</h1>

        {!configured && (
          <p role="alert">
            Supabase isn&apos;t configured. Copy <code>Frontend/.env.example</code> to{" "}
            <code>Frontend/.env</code> and fill in the values.
          </p>
        )}

        {mode === "signup" && (
          <input
            placeholder="Full name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            autoComplete="name"
          />
        )}
        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          required
        />
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === "signin" ? "current-password" : "new-password"}
          minLength={6}
          required
        />

        {error && <p role="alert" style={{ color: "crimson" }}>{error}</p>}
        {notice && <p role="status">{notice}</p>}

        <button className="btn btn-primary" type="submit" disabled={busy || !configured}>
          {busy ? "Working…" : mode === "signin" ? "Sign in" : "Sign up"}
        </button>
        <button
          type="button"
          className="btn btn-outline"
          onClick={() => setMode(mode === "signin" ? "signup" : "signin")}
        >
          {mode === "signin" ? "Need an account? Sign up" : "Have an account? Sign in"}
        </button>
      </form>
    </Layout>
  );
}
