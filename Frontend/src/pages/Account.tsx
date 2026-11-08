import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import { api, type Me } from "../lib/api";
import { useSession, useAuth } from "../lib/AuthProvider";
import { beginConnect, connectionsApi, type ConnectionView } from "../lib/connections";
import "./Auth.css";
import "./Console.css";
import "./Account.css";

function accountStatus(a: ConnectionView): string {
  if (a.connected) {
    const since = a.connectedAt ? ` since ${new Date(a.connectedAt).toLocaleDateString()}` : "";
    return `Connected as ${a.handle}${since}`;
  }
  if (!a.supported) return "Not supported yet";
  if (!a.configured) return "Not set up yet — ask your admin";
  return "Not connected";
}

// Who the API says you are (GET /api/v1/me checks your Cognito token): the WP1 "email + group" check.
// Read-only: profiles live in Cognito, not in this app.
export default function Account() {
  const { signOut } = useAuth();
  const session = useSession();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .me()
      .then(setMe)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const [accounts, setAccounts] = useState<ConnectionView[] | null>(null);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [params] = useSearchParams();
  const highlighted = params.get("connect");

  const loadAccounts = () =>
    connectionsApi
      .list()
      .then(setAccounts)
      .catch((e: unknown) => setAccountsError(e instanceof Error ? e.message : String(e)));

  useEffect(() => {
    loadAccounts();
  }, []);

  // ?connect=<provider> only points at a row; starting a sign-in always takes a click.
  useEffect(() => {
    if (accounts && highlighted) document.getElementById(`connect-${highlighted}`)?.scrollIntoView?.({ block: "center" });
  }, [accounts, highlighted]);

  const runAction = (provider: string, action: () => Promise<unknown>) => {
    setBusy(provider);
    setAccountsError(null);
    action()
      .then(loadAccounts)
      .catch((e: unknown) => setAccountsError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(null));
  };

  const dash = <span className="muted">—</span>;

  return (
    <Layout>
      <div className="auth">
        <section className="auth-card auth-card-wide">
          <h1>Account</h1>
          {error && (
            <p className="alert alert-error" role="alert">
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
          <h2 className="accounts-heading">Connected accounts</h2>
          <p className="muted">Connect the accounts your assistant may send from. It cannot send or post until you do.</p>
          {accountsError && (
            <p className="alert alert-error" role="alert">
              Could not update your connected accounts: {accountsError}
            </p>
          )}
          <ul className="accounts">
            {accounts?.map((a) => (
              <li key={a.provider} id={`connect-${a.provider}`} className={a.provider === highlighted ? "account-row account-row-highlight" : "account-row"}>
                <div className="account-info">
                  <strong>{a.label}</strong>
                  <span className="muted">{accountStatus(a)}</span>
                </div>
                {a.connected ? (
                  <button type="button" className="btn btn-outline" disabled={busy === a.provider} onClick={() => runAction(a.provider, () => connectionsApi.disconnect(a.provider))}>
                    Disconnect
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-outline"
                    disabled={!a.supported || !a.configured || busy === a.provider}
                    onClick={() => runAction(a.provider, () => beginConnect(a.provider))}
                  >
                    Connect {a.label}
                  </button>
                )}
              </li>
            ))}
          </ul>
          <div className="login-actions">
            <button type="button" className="btn btn-outline" onClick={signOut}>
              Sign out
            </button>
          </div>
        </section>
      </div>
    </Layout>
  );
}
