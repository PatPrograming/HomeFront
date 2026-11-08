import { useState, type FormEvent } from "react";
import { api } from "../../lib/api.js";
import { describeError, type Agent, type SocialPlatformsInfo } from "../../lib/agents";
import "./SocialConnect.css";

// Where someone without an account can make one. Connecting happens on the
// platform's own page (X) or with an app password they create there (Bluesky).
const SIGNUP: Record<string, string> = {
  x: "https://x.com/i/flow/signup",
  bluesky: "https://bsky.app",
};

interface Props {
  agent: Agent;
  platformId: string;
  platformName: string;
  info: SocialPlatformsInfo | null;
  /** Called after a connect/disconnect so the parent reloads the agent. */
  onChanged: () => void | Promise<void>;
}

export default function SocialConnect({ agent, platformId, platformName, info, onChanged }: Props) {
  const [identifier, setIdentifier] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const live = !!info?.live.includes(platformId);
  const connection = agent.social_connections?.[platformId];
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  if (!live) {
    return (
      <p className="sc-status muted">
        <strong>{platformName}</strong>: coming soon. It can't be posted to yet.
      </p>
    );
  }

  if (connection?.connected) {
    return (
      <div className="sc-row">
        <p className="sc-status">
          <span className="sc-dot" aria-hidden="true" />
          <strong>{platformName}</strong>: posts as <strong>@{connection.handle}</strong>
        </p>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={busy}
          onClick={() =>
            run(async () => {
              await api.disconnectSocial(agent.agent_id, platformId);
              await onChanged();
            })
          }
        >
          Disconnect
        </button>
        {error && <p className="sc-error" role="alert">{error}</p>}
      </div>
    );
  }

  if (platformId === "x") {
    const configured = info?.configured.x !== false;
    return (
      <div className="sc-box">
        <p className="sc-status">
          <strong>{platformName}</strong>: not connected
        </p>
        {configured ? (
          <>
            <p className="sc-hint">
              You'll sign in on X and approve access. No X account yet?{" "}
              <a href={SIGNUP.x} target="_blank" rel="noreferrer">Create one</a> first, or sign up on X's login page.
            </p>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const { authorize_url } = await api.connectX(agent.agent_id);
                  window.location.assign(authorize_url);
                })
              }
            >
              {busy ? "Opening X…" : "Connect X account"}
            </button>
          </>
        ) : (
          <p className="sc-hint">X isn't set up on this server yet (the server admin needs to add an X app).</p>
        )}
        {error && <p className="sc-error" role="alert">{error}</p>}
      </div>
    );
  }

  // Bluesky: handle + app password.
  const links = info?.links.bluesky;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      await api.connectBluesky(agent.agent_id, identifier, appPassword);
      setAppPassword("");
      setIdentifier("");
      await onChanged();
    });
  };
  return (
    <form className="sc-box" onSubmit={submit}>
      <p className="sc-status">
        <strong>{platformName}</strong>: not connected
      </p>
      <p className="sc-hint">
        No Bluesky account yet? <a href={links?.signup || SIGNUP.bluesky} target="_blank" rel="noreferrer">Sign up</a>.
        Then create an <a href={links?.app_passwords || "https://bsky.app/settings/app-passwords"} target="_blank" rel="noreferrer">app password</a>{" "}
        (Settings, Privacy and security, App passwords). Don't use your main password: an app password can be
        deleted at any time to cut off access.
      </p>
      <div className="sc-fields">
        <input
          className="input"
          aria-label="Bluesky handle"
          placeholder="agency.bsky.social"
          autoComplete="off"
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
        />
        <input
          className="input"
          type="password"
          aria-label="Bluesky app password"
          placeholder="xxxx-xxxx-xxxx-xxxx"
          autoComplete="off"
          value={appPassword}
          onChange={(e) => setAppPassword(e.target.value)}
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !identifier.trim() || !appPassword.trim()}>
          {busy ? "Connecting…" : "Connect"}
        </button>
      </div>
      <p className="sc-hint">We use the app password once to sign in, then discard it.</p>
      {error && <p className="sc-error" role="alert">{error}</p>}
    </form>
  );
}
