import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import { ApiError } from "../lib/api";
import { connectionsApi } from "../lib/connections";
import "./Auth.css";
import "./Console.css";
import "./Account.css";

type Outcome = { kind: "working" } | { kind: "done"; label: string } | { kind: "stopped" } | { kind: "expired" } | { kind: "failed" };

// Where the provider sends the browser back to (/account/connected?code&state). The code is single-use, so it is
// posted once; the server checks the state and keeps the tokens, this page only shows the result.
export default function ConnectCallback() {
  const [params] = useSearchParams();
  const code = params.get("code");
  const state = params.get("state");
  const providerError = params.has("error");
  const declined = providerError || !code || !state;
  const [outcome, setOutcome] = useState<Outcome>(declined ? { kind: "stopped" } : { kind: "working" });
  const posted = useRef(false);

  useEffect(() => {
    if (providerError || !code || !state || posted.current) return;
    posted.current = true;
    connectionsApi
      .finish(code, state)
      .then((c) => setOutcome({ kind: "done", label: c.label }))
      .catch((e: unknown) => setOutcome({ kind: e instanceof ApiError && e.status === 400 ? "expired" : "failed" }));
  }, [providerError, code, state]);

  return (
    <Layout>
      <div className="auth">
        <section className="auth-card" aria-live="polite">
          <h1>Connect an account</h1>
          {outcome.kind === "working" && <p>Finishing the connection…</p>}
          {outcome.kind === "done" && <p>{outcome.label} is connected. Your assistant can now use it when you ask.</p>}
          {outcome.kind === "stopped" && <p>The account was not connected because the sign-in was cancelled or declined. Nothing changed.</p>}
          {outcome.kind === "expired" && <p>This sign-in link has expired or is invalid. Go back to your account and try connecting again.</p>}
          {outcome.kind === "failed" && <p>Something went wrong while connecting. Nothing was saved. Please try again.</p>}
          <div className="login-actions">
            <Link to="/account" className="btn btn-outline">
              Back to Account
            </Link>
            {outcome.kind === "done" && (
              <Link to="/console" className="btn btn-outline">
                Back to the chat
              </Link>
            )}
          </div>
        </section>
      </div>
    </Layout>
  );
}
