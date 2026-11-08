import { useCallback, useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import Layout from "../components/Layout";
import SocialConnect from "../components/SocialConnect/SocialConnect";
import { api } from "../lib/api.js";
import { describeError, type Agent, type PublishResult, type SocialPlatformsInfo } from "../lib/agents";
import Stepper from "../components/Stepper/Stepper";
import PlatformIcon, { PLATFORMS, type PlatformId } from "../components/PlatformIcons/PlatformIcons";
import "./SocialPlatform.css";

interface ImageItem {
  id: string;
  file: File;
  url: string;
}

const brandVars = (color: string) =>
  ({ "--brand": color, "--glyph-cut": color }) as CSSProperties;

const STEPS = ["Select platforms", "Add content"];

const platformsList = (r: PublishResult) =>
  r.queued.map((q) => PLATFORMS.find((p) => p.id === q.platform)?.name || q.platform).join(", ");

const OAUTH_ERRORS: Record<string, string> = {
  denied: "Access wasn't granted, so nothing was connected.",
  state_expired: "That connection attempt expired. Please try again.",
  unknown_agent: "That agent no longer exists.",
  failed: "Couldn't finish connecting the account. Please try again.",
};

// Two-stage flow: (1) pick the platforms the agency is active on and connect an account for
// each (signing up on the platform first if needed), (2) provide the message and links, which
// are queued as posts for a person to approve. Nothing is posted until then.
export default function SocialPlatform() {
  const [step, setStep] = useState(1);
  const [selected, setSelected] = useState<PlatformId[]>([]);
  const [message, setMessage] = useState("");
  const [links, setLinks] = useState([""]);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [params, setParams] = useSearchParams();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [agentId, setAgentId] = useState(params.get("agent") || "");
  const [info, setInfo] = useState<SocialPlatformsInfo | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [published, setPublished] = useState<PublishResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const imagesRef = useRef(images);
  imagesRef.current = images;

  const loadAgents = useCallback(async () => {
    try {
      const all = (await api.agents()) as Agent[];
      // Agents that may post: active, and granted social.post.
      const posters = all.filter((a) => a.active && a.tool_grants.includes("social.post"));
      setAgents(posters);
      setAgentId((cur) => (posters.some((a) => a.agent_id === cur) ? cur : posters[0]?.agent_id || ""));
    } catch (e) {
      setAgents([]);
      setNotice({ kind: "error", text: describeError(e) });
    }
  }, []);

  useEffect(() => {
    loadAgents();
    api.socialPlatforms().then(setInfo).catch(() => setInfo(null));
  }, [loadAgents]);

  // Coming back from X's consent page: ?connected=x&agent=... or ?social_error=...
  useEffect(() => {
    const connected = params.get("connected");
    const err = params.get("social_error");
    if (!connected && !err) return;
    if (connected) {
      setSelected((cur) => (cur.includes(connected as PlatformId) ? cur : [...cur, connected as PlatformId]));
      setNotice({ kind: "ok", text: "Account connected." });
    } else if (err) {
      setNotice({ kind: "error", text: OAUTH_ERRORS[err] || OAUTH_ERRORS.failed });
    }
    const next = new URLSearchParams(params);
    ["connected", "social_error"].forEach((k) => next.delete(k));
    setParams(next, { replace: true });
  }, [params, setParams]);

  // Release object URLs for any previews still around on unmount.
  useEffect(() => () => imagesRef.current.forEach((i) => URL.revokeObjectURL(i.url)), []);

  const togglePlatform = (id: PlatformId) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((p) => p !== id) : [...cur, id]));

  const goTo = (n: number) => {
    setStep(n);
    window.scrollTo({ top: 0 });
  };

  const setLink = (i: number, value: string) => setLinks((cur) => cur.map((l, idx) => (idx === i ? value : l)));
  const removeLink = (i: number) =>
    setLinks((cur) => (cur.length === 1 ? [""] : cur.filter((_, idx) => idx !== i)));

  const addImages = (fileList: FileList | null) => {
    if (!fileList) return;
    addFiles(Array.from(fileList));
  };
  const addFiles = (files: File[]) => {
    const added = files
      .filter((f) => f.type.startsWith("image/"))
      .map((file) => ({ id: `${file.name}-${file.size}-${crypto.randomUUID()}`, file, url: URL.createObjectURL(file) }));
    setImages((cur) => [...cur, ...added]);
  };
  const removeImage = (id: string) =>
    setImages((cur) => {
      const gone = cur.find((i) => i.id === id);
      if (gone) URL.revokeObjectURL(gone.url);
      return cur.filter((i) => i.id !== id);
    });

  const chosen = PLATFORMS.filter((p) => selected.includes(p.id));
  const agent = agents?.find((a) => a.agent_id === agentId) || null;
  const isLive = (id: string) => !!info?.live.includes(id);
  const isConnected = (id: string) => !!agent?.social_connections?.[id]?.connected;
  const ready = chosen.filter((p) => isLive(p.id) && isConnected(p.id));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!agent || !message.trim()) return;
    setSubmitting(true);
    setNotice(null);
    try {
      setPublished(
        (await api.publishSocial(agent.agent_id, {
          platforms: ready.map((p) => p.id),
          message,
          links: links.filter((l) => l.trim()),
        })) as PublishResult,
      );
      window.scrollTo({ top: 0 });
    } catch (err) {
      setNotice({ kind: "error", text: describeError(err) });
    } finally {
      setSubmitting(false);
    }
  };

  const platformName = (id: string) => PLATFORMS.find((p) => p.id === id)?.name || id;

  if (published) {
    return (
      <Layout>
        <div className="container sp">
          <h1 className="sp-title">
            {published.queued.length
              ? `${published.queued.length} post${published.queued.length > 1 ? "s" : ""} waiting for approval`
              : "Nothing was queued"}
          </h1>
          <p className="sp-sub">
            {published.queued.length
              ? `Nothing is posted until a person approves it. Review ${platformsList(published)} on the Approvals page.`
              : "None of the selected platforms could take this post."}
          </p>
          {published.skipped.length > 0 && (
            <ul className="sp-skipped">
              {published.skipped.map((s) => (
                <li key={s.platform}>
                  <strong>{platformName(s.platform)}</strong>: {s.message}
                </li>
              ))}
            </ul>
          )}
          <div className="sp-actions">
            <button type="button" className="btn btn-outline" onClick={() => setPublished(null)}>
              Back to the post
            </button>
            <Link to="/approvals" className="btn btn-primary">
              Go to Approvals
            </Link>
          </div>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="container sp">
        <header className="sp-head">
          <Stepper steps={STEPS} current={step} />
        </header>

        {step === 1 ? (
          <section aria-labelledby="sp-title-1">
            <h1 id="sp-title-1" className="sp-title">Where does your agency post?</h1>
            <p className="sp-sub">
              Select every platform your agency is active on. You can change this at any time.
            </p>

            {notice && (
              <p className={`sp-notice is-${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
                {notice.text}
              </p>
            )}

            {agents && agents.length === 0 ? (
              <p className="sp-notice is-error">
                You need an agent that's allowed to post to social media first.{" "}
                <Link to="/agents/new">Create one</Link>, or grant <code>social.post</code> to an existing agent on{" "}
                <Link to="/agents">My agents</Link>.
              </p>
            ) : (
              agents &&
              agents.length > 1 && (
                <div className="field sp-agent">
                  <label className="field-label" htmlFor="sp-agent">Posting agent</label>
                  <select
                    id="sp-agent"
                    className="input"
                    value={agentId}
                    onChange={(e) => setAgentId(e.target.value)}
                  >
                    {agents.map((a) => (
                      <option key={a.agent_id} value={a.agent_id}>
                        {a.name} ({a.agency})
                      </option>
                    ))}
                  </select>
                </div>
              )
            )}

            <fieldset className="platform-grid">
              <legend className="sr-only">Social platforms</legend>
              {PLATFORMS.map((p) => {
                const on = selected.includes(p.id);
                return (
                  <label
                    key={p.id}
                    className={`platform${on ? " is-on" : ""}`}
                    style={brandVars(p.color)}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={on}
                      onChange={() => togglePlatform(p.id)}
                    />
                    <span className="platform-check" aria-hidden="true">
                      <svg viewBox="0 0 16 16" width="14" height="14">
                        <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </span>
                    <span className="platform-icon">
                      <PlatformIcon name={p.id} size={34} />
                    </span>
                    <span className="platform-name">{p.name}</span>
                    <span className="platform-note">{p.note}</span>
                  </label>
                );
              })}
            </fieldset>

            {agent && chosen.length > 0 && (
              <section className="sp-connect" aria-labelledby="sp-connect-title">
                <h2 id="sp-connect-title" className="sp-connect-title">Connect your accounts</h2>
                <p className="field-hint">
                  Posts go out from your own accounts, and only after a person approves them. Don't have an account
                  yet? Each one below links to where you can create it.
                </p>
                {chosen.map((p) => (
                  <SocialConnect
                    key={`${agent.agent_id}-${p.id}`}
                    agent={agent}
                    platformId={p.id}
                    platformName={p.name}
                    info={info}
                    onChanged={loadAgents}
                  />
                ))}
              </section>
            )}

            <div className="sp-actions">
              <p className="sp-count" aria-live="polite">
                {selected.length === 0
                  ? "No platforms selected"
                  : `${selected.length} platform${selected.length > 1 ? "s" : ""} selected`}
              </p>
              <button
                type="button"
                className="btn btn-primary"
                disabled={selected.length === 0}
                onClick={() => goTo(2)}
              >
                Confirm
                <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                  <path d="M3 8h10M9 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </div>
          </section>
        ) : (
          <form
            className="content"
            aria-labelledby="sp-title-2"
            onSubmit={submit}
          >
            <div className="content-main">
              <h1 id="sp-title-2" className="sp-title">What does the public need to know?</h1>
              <p className="sp-sub">
                Give us the essentials. Keep it factual: agents will adapt it for each platform.
              </p>

              <div className="card">
                <div className="field">
                  <label className="field-label" htmlFor="message">Main message</label>
                  <p className="field-hint" id="message-hint">
                    The minimum information that must be communicated, such as current figures,
                    affected areas and required actions.
                  </p>
                  <textarea
                    id="message"
                    className="textarea"
                    rows={8}
                    aria-describedby="message-hint"
                    placeholder="e.g. The CDC confirms a new outbreak reaching 14 states, with 312 reported deaths as of today…"
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                  />
                  <span className="field-count">{message.length} characters</span>
                </div>
              </div>

              <div className="card">
                <fieldset className="field">
                  <legend className="field-label">Relevant links</legend>
                  <p className="field-hint">Official pages, press releases or datasets to reference.</p>
                  <div className="link-list">
                    {links.map((link, i) => (
                      <div className="link-row" key={i}>
                        <input
                          type="url"
                          className="input"
                          aria-label={`Link ${i + 1}`}
                          placeholder="https://www.agency.gov/update"
                          value={link}
                          onChange={(e) => setLink(i, e.target.value)}
                        />
                        <button
                          type="button"
                          className="icon-btn"
                          aria-label={`Remove link ${i + 1}`}
                          onClick={() => removeLink(i)}
                          disabled={links.length === 1 && !link}
                        >
                          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                            <path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                          </svg>
                        </button>
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="btn btn-ghost add-link"
                    onClick={() => setLinks((cur) => [...cur, ""])}
                  >
                    + Add another link
                  </button>
                </fieldset>
              </div>

              <div className="card">
                <div className="field">
                  <span className="field-label" id="img-label">Images</span>
                  <p className="field-hint">
                    Graphs, maps or photos that support the message. Images aren't included in posts yet.
                  </p>
                  <div className="image-grid" role="group" aria-labelledby="img-label">
                    <button
                      type="button"
                      className="dropzone"
                      onClick={() => fileRef.current?.click()}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => {
                        e.preventDefault();
                        addImages(e.dataTransfer.files);
                      }}
                    >
                      <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
                        <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                      </svg>
                      <span>Add image</span>
                    </button>
                    {images.map((img) => (
                      <div className="thumb" key={img.id}>
                        <img src={img.url} alt={img.file.name} />
                        <button
                          type="button"
                          className="thumb-remove"
                          aria-label={`Remove ${img.file.name}`}
                          onClick={() => removeImage(img.id)}
                        >
                          <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
                            <path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                      </div>
                    ))}
                  </div>
                  <input
                    ref={fileRef}
                    type="file"
                    accept="image/*"
                    multiple
                    hidden
                    onChange={(e) => {
                      addImages(e.target.files);
                      e.target.value = "";
                    }}
                  />
                </div>
              </div>
            </div>

            <aside className="summary" aria-label="Selected platforms">
              <h2>Publishing to</h2>
              <ul>
                {chosen.map((p) => (
                  <li key={p.id} style={brandVars(p.color)}>
                    <span className="summary-icon">
                      <PlatformIcon name={p.id} size={16} />
                    </span>
                    {p.name}
                    <span className="summary-state">
                      {!isLive(p.id) ? "Coming soon" : isConnected(p.id) ? `@${agent?.social_connections?.[p.id]?.handle}` : "Not connected"}
                    </span>
                  </li>
                ))}
              </ul>
              <button type="button" className="btn btn-ghost" onClick={() => goTo(1)}>
                Change platforms
              </button>
              <div className="summary-actions">
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={!message.trim() || !agent || ready.length === 0 || submitting}
                >
                  {submitting ? "Submitting…" : "Submit for approval"}
                </button>
                {ready.length === 0 && (
                  <p className="summary-warn">Connect at least one account to submit.</p>
                )}
                {notice?.kind === "error" && <p className="summary-warn" role="alert">{notice.text}</p>}
                <button type="button" className="btn btn-outline" onClick={() => goTo(1)}>
                  Back
                </button>
              </div>
            </aside>
          </form>
        )}
      </div>
    </Layout>
  );
}
