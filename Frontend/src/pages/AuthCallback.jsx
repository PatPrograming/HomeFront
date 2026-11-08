import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Layout from "../components/Layout.jsx";
import { useAuth } from "../lib/AuthProvider.jsx";
import "./Console.css";

// Cognito sends the browser back here with ?code=…&state=…; we swap the code for tokens once.
export default function AuthCallback() {
  const { finishLogin } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState(null);
  const started = useRef(false); // React StrictMode runs effects twice in dev; the code works only once

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    finishLogin(window.location.search)
      .then((to) => navigate(to, { replace: true }))
      .catch((e) => setError(e.message || String(e)));
  }, [finishLogin, navigate]);

  return (
    <Layout>
      <div className="mandate-console login">
        <section className="panel">
          <h1>{error ? "Sign-in failed" : "Signing you in…"}</h1>
          {error && (
            <>
              <p className="alert" role="alert">
                {error}
              </p>
              <Link to="/login">Try again</Link>
            </>
          )}
        </section>
      </div>
    </Layout>
  );
}
