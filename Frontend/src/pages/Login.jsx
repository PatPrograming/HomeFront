import { useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import Layout from "../components/Layout.jsx";
import { useAuth } from "../lib/AuthProvider.jsx";
import "./Auth.css";

// Modes: signin, signup, confirm (enter the code Cognito emailed).
export default function Login() {
  const { user, loading, configured, signIn, signUp, confirmSignUp, resendCode, refresh } =
    useAuth();
  const location = useLocation();
  const redirectTo = location.state?.from || "/account";

  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  // Once the session loads, leave for the page that sent us here.
  if (!loading && user) return <Navigate to={redirectTo} replace />;

  function toConfirm(message) {
    setMode("confirm");
    setCode("");
    setNotice(message);
  }

  async function onSubmit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "signin") {
        const { isSignedIn, nextStep } = await signIn(email, password);
        if (isSignedIn) await refresh();
        else if (nextStep.signInStep === "CONFIRM_SIGN_UP") {
          await resendCode(email);
          toConfirm("Your email isn't confirmed yet. We sent a new code.");
        } else {
          setError(`Additional sign-in step required: ${nextStep.signInStep}`);
        }
      } else if (mode === "signup") {
        const { nextStep } = await signUp(email, password, fullName);
        if (nextStep.signUpStep === "CONFIRM_SIGN_UP") {
          toConfirm(`We emailed a confirmation code to ${email}.`);
        } else {
          await refresh();
        }
      } else {
        const signedIn = await confirmSignUp(email, code.trim());
        if (signedIn) await refresh();
        else {
          setMode("signin");
          setNotice("Email confirmed. Sign in to continue.");
        }
      }
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onResend() {
    setError(null);
    try {
      await resendCode(email);
      setNotice(`Sent a new code to ${email}.`);
    } catch (err) {
      setError(err.message || String(err));
    }
  }

  const title = { signin: "Sign in", signup: "Create account", confirm: "Confirm your email" }[mode];
  const submit = { signin: "Sign in", signup: "Sign up", confirm: "Confirm" }[mode];

  const subtitle = {
    signin: "Welcome back. Sign in to manage your agency's communications.",
    signup: "Set up access for your agency's communications team.",
    confirm: "Enter the code we emailed you to finish setting up your account.",
  }[mode];

  return (
    <Layout>
      <div className="auth">
        <form className="auth-card" onSubmit={onSubmit}>
          <h1>{title}</h1>
          <p className="auth-sub">{subtitle}</p>

          {!configured && (
            <p role="alert" className="alert alert-info">
              Sign-in isn&apos;t configured. Copy <code>.env.example</code> to{" "}
              <code>.env</code> at the repo root and fill in the Cognito values from{" "}
              <code>./infra/deploy.sh</code>.
            </p>
          )}

          {mode === "signup" && (
            <div className="field">
              <label className="field-label" htmlFor="fullName">Full name</label>
              <input
                id="fullName"
                className="input"
                placeholder="Full name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                autoComplete="name"
              />
            </div>
          )}
          <div className="field">
            <label className="field-label" htmlFor="email">Email</label>
            <input
              id="email"
              className="input"
              type="email"
              placeholder="you@agency.gov"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              readOnly={mode === "confirm"}
              required
            />
          </div>
          {mode === "confirm" ? (
            <div className="field">
              <label className="field-label" htmlFor="code">Confirmation code</label>
              <input
                id="code"
                className="input"
                placeholder="6-digit code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                required
              />
            </div>
          ) : (
            <div className="field">
              <label className="field-label" htmlFor="password">Password</label>
              <input
                id="password"
                className="input"
                type="password"
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                minLength={8}
                title="At least 8 characters, with a lowercase letter and a number"
                required
              />
              {mode === "signup" && (
                <p className="field-hint">
                  At least 8 characters, including a lowercase letter and a number.
                </p>
              )}
            </div>
          )}

          {error && <p role="alert" className="alert alert-error">{error}</p>}
          {notice && <p role="status" className="alert alert-info">{notice}</p>}

          <button className="btn btn-primary" type="submit" disabled={busy || !configured}>
            {busy ? "Working…" : submit}
          </button>
          {mode === "confirm" ? (
            <>
              <button type="button" className="btn btn-outline" onClick={onResend} disabled={busy}>
                Resend code
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setMode("signin")}>
                Back to sign in
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => setMode(mode === "signin" ? "signup" : "signin")}
            >
              {mode === "signin" ? "Need an account? Sign up" : "Have an account? Sign in"}
            </button>
          )}
        </form>
      </div>
    </Layout>
  );
}
