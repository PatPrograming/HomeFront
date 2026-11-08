// Email setup → Branding: the agency logo at the top of every email, and a preview of the template.
import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import { agentsApi as api } from "../lib/agentsApi";
import { describeError, type Agent, type EmailPreview } from "../lib/agents";

// Mirrors identity_gateway/logo_store.py; the server has the final say.
export const LOGO_TYPES = ["image/png", "image/jpeg", "image/gif"];
export const MAX_LOGO_BYTES = 512 * 1024;

/** Why this file can't be a logo, or null if it's worth uploading. */
export function logoProblem(file: { type: string; size: number }): string | null {
  if (!LOGO_TYPES.includes(file.type))
    return "Use a PNG, JPEG or GIF image. SVG doesn't display in most email clients.";
  if (file.size > MAX_LOGO_BYTES) return `Logos are limited to 512 KB; this one is ${Math.ceil(file.size / 1024)} KB.`;
  return null;
}

const readAsDataUrl = (file: File) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Couldn't read the file"));
    reader.readAsDataURL(file);
  });

export default function EmailBranding({ agent, onSaved }: { agent: Agent; onSaved: (a: Agent) => void }) {
  const logo = agent.email_logo ?? null;
  const [preview, setPreview] = useState<EmailPreview | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPreview = useCallback(() => {
    api
      .emailPreview(agent.agent_id)
      .then(setPreview)
      .catch(() => setPreview(null));
  }, [agent.agent_id]);

  // Re-render whenever anything that shows in the template changes.
  useEffect(loadPreview, [loadPreview, logo?.uploaded_at, agent.agency, agent.email_settings]);

  async function change(action: () => Promise<Agent>) {
    setBusy(true);
    setError(null);
    try {
      onSaved(await action());
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow picking the same file again
    if (!file) return;
    const problem = logoProblem(file);
    if (problem) {
      setError(problem);
      return;
    }
    await change(async () => api.uploadEmailLogo(agent.agent_id, await readAsDataUrl(file), file.name));
  }

  return (
    <div className="email-branding">
      <span className="field-label">Agency logo</span>
      <div className="logo-row">
        <div className="logo-frame" aria-hidden={!preview?.logo_data_url}>
          {preview?.logo_data_url ? (
            <img src={preview.logo_data_url} alt={`${agent.agency} logo`} />
          ) : (
            <span className="muted">{logo ? "Loading…" : "No logo"}</span>
          )}
        </div>
        <div className="logo-actions">
          <label className={`btn btn-outline upload-btn${busy ? " is-disabled" : ""}`}>
            {busy ? "Uploading…" : logo ? "Replace logo" : "Upload logo"}
            <input type="file" accept={LOGO_TYPES.join(",")} onChange={onFile} disabled={busy} hidden />
          </label>
          {logo && (
            <button
              className="btn btn-ghost"
              type="button"
              disabled={busy}
              onClick={() => change(() => api.deleteEmailLogo(agent.agent_id))}
            >
              Remove
            </button>
          )}
        </div>
      </div>
      <p className="muted mailing-list-hint">
        Shown at the top of every email next to “{agent.agency}”. PNG, JPEG or GIF up to 512 KB; a transparent PNG
        about 200 px tall looks sharpest.
      </p>
      {error && (
        <p className="alert alert-error" role="alert">
          {error}
        </p>
      )}

      <div className="form-actions">
        <button
          className="btn btn-outline"
          type="button"
          aria-expanded={showPreview}
          disabled={!preview}
          onClick={() => setShowPreview(!showPreview)}
        >
          {showPreview ? "Hide email preview" : "Preview email"}
        </button>
      </div>
      {showPreview && preview && (
        <div className="email-preview">
          <p className="muted mailing-list-hint">
            <strong>From:</strong> {preview.from ?? "not set"} · <strong>Subject:</strong> {preview.subject} · sample
            text, sent in this template
          </p>
          {/* sandbox="" : the HTML is ours and escaped, but never let it run script or navigate. */}
          <iframe className="email-preview-frame" title="Email preview" sandbox="" srcDoc={preview.html} />
        </div>
      )}
    </div>
  );
}
