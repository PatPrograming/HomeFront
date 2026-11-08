import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import Layout from "../components/Layout";
import { api, ApiError } from "../lib/api.js";
import { describeError, TERMINAL_RUN_STATUSES, type Agent, type Run } from "../lib/agents";
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

const STEPS = ["Select platforms", "Add content", "Send to agent"];
const POLL_MS = 1500;

// The social agent drafts one post per platform. Nothing is published by HomeFront: each draft
// waits on the Approvals page, and the person then posts it from their own signed-in account.
function buildTask(platforms: { id: string; name: string }[], message: string, links: string[]) {
  const urls = links.map((l) => l.trim()).filter(Boolean);
  return [
    `Draft one social media post for each of these platforms: ${platforms.map((p) => `${p.name} (platform "${p.id}")`).join(", ")}.`,
    `Submit each draft with social.post. Adapt the wording to each platform and stay within its character limit.`,
    urls.length
      ? `Include the most relevant of these official links in every draft (Facebook and LinkedIn share only a link, so it is required there):\n${urls.map((u) => `- ${u}`).join("\n")}`
      : `No links were provided. Facebook and LinkedIn share only a link, so skip those platforms and say so.`,
    `Main message (the facts that must be communicated; do not add facts that are not here):\n${message.trim()}`,
  ].join("\n\n");
}

const isActive = (run: Run | null) => !!run && !TERMINAL_RUN_STATUSES.includes(run.status);

// Three-stage flow: (1) pick the platforms the agency is active on, (2) provide the message,
// links and images, (3) hand it to a social agent, whose drafts land on the Approvals page.
export default function SocialPlatform() {
  const [step, setStep] = useState(1);
  const [selected, setSelected] = useState<PlatformId[]>([]);
  const [message, setMessage] = useState("");
  const [links, setLinks] = useState([""]);
  const [images, setImages] = useState<ImageItem[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const imagesRef = useRef(images);
  imagesRef.current = images;

  // Step 3: the social agents the person owns, and the run that drafts the posts.
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [agentId, setAgentId] = useState("");
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [starting, setStarting] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

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

  useEffect(() => {
    if (step !== 3 || agents) return;
    api
      .agents()
      .then((list: Agent[]) => {
        const social = list.filter((a) => a.kind === "social" && a.active !== false);
        setAgents(social);
        setAgentId((cur) => cur || social[0]?.agent_id || "");
      })
      .catch((e: unknown) => setAgentsError(describeError(e)));
  }, [step, agents]);

  const runId = isActive(run) ? run!.run_id : null;
  useEffect(() => {
    if (!runId || !agentId) return;
    const id = window.setInterval(() => {
      api
        .run(agentId, runId)
        .then(setRun)
        .catch((e: unknown) => setRunError(describeError(e)));
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [runId, agentId]);

  async function draftPosts() {
    setStarting(true);
    setRunError(null);
    try {
      setRun(await api.startRun(agentId, buildTask(chosen, message, links)));
    } catch (e) {
      const unconfigured = e instanceof ApiError && (e as ApiError & { status?: number }).status === 503;
      setRunError(
        unconfigured
          ? "The agent runner isn't configured on the server: it needs BEDROCK_REGION (or AWS_REGION) and access to Claude on Amazon Bedrock."
          : describeError(e),
      );
    } finally {
      setStarting(false);
    }
  }

  const draftsQueued = (run?.steps ?? []).filter((s) => s.decision === "pending_approval").length;

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
            <p className="sp-sub sp-fine">
              HomeFront never asks for your social logins. Agents write the drafts, and you publish each one from
              your own signed-in account.
            </p>

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
        ) : step === 2 ? (
          <form
            className="content"
            aria-labelledby="sp-title-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (message.trim()) goTo(3);
            }}
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
                    Graphs, maps or photos that support the message. Share links can't attach images, so you add
                    these yourself when you post.
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
                  </li>
                ))}
              </ul>
              <button type="button" className="btn btn-ghost" onClick={() => goTo(1)}>
                Change platforms
              </button>
              <div className="summary-actions">
                <button type="submit" className="btn btn-primary" disabled={!message.trim()}>
                  Continue
                </button>
                <button type="button" className="btn btn-outline" onClick={() => goTo(1)}>
                  Back
                </button>
              </div>
            </aside>
          </form>
        ) : (
          <section className="send" aria-labelledby="sp-title-3">
            <h1 id="sp-title-3" className="sp-title">Send it to your social agent</h1>
            <p className="sp-sub">
              The agent drafts one post per platform. Each draft waits on the Approvals page. After you approve it,
              you get a link that opens the platform with the post filled in, and you press Post.
            </p>

            <div className="card send-card">
              <h2 className="send-h">Publishing to</h2>
              <ul className="send-platforms">
                {chosen.map((p) => (
                  <li key={p.id} style={brandVars(p.color)}>
                    <span className="summary-icon">
                      <PlatformIcon name={p.id} size={16} />
                    </span>
                    {p.name}
                  </li>
                ))}
              </ul>
              <p className="send-message">{message.trim()}</p>
              {images.length > 0 && (
                <p className="muted send-fine">
                  {images.length} image{images.length === 1 ? "" : "s"} added. They stay in this browser: add them
                  yourself when you post.
                </p>
              )}
            </div>

            {agentsError && <p className="alert alert-error">Couldn't load your agents: {agentsError}</p>}
            {!agents && !agentsError && <p className="muted">Loading your agents…</p>}

            {agents && agents.length === 0 && (
              <div className="empty">
                <p>You don't have a social media agent yet.</p>
                <Link to="/agents/new" className="btn btn-outline">
                  Create one
                </Link>
              </div>
            )}

            {agents && agents.length > 0 && (
              <div className="card send-card">
                <label className="field">
                  <span className="field-label">Agent</span>
                  <select
                    className="input"
                    value={agentId}
                    disabled={starting || isActive(run)}
                    onChange={(e) => setAgentId(e.target.value)}
                  >
                    {agents.map((a) => (
                      <option key={a.agent_id} value={a.agent_id}>
                        {a.name} · {a.agency}
                      </option>
                    ))}
                  </select>
                </label>

                {runError && (
                  <p className="alert alert-error" role="alert">
                    {runError}
                  </p>
                )}

                {run && (
                  <div className="send-status" role="status" aria-live="polite">
                    {isActive(run) && <p>The agent is drafting your posts…</p>}
                    {run.status === "completed" && draftsQueued > 0 && (
                      <p>
                        <strong>
                          {draftsQueued} draft{draftsQueued === 1 ? "" : "s"} ready for your review.
                        </strong>{" "}
                        Nothing has been posted.
                      </p>
                    )}
                    {run.status === "completed" && draftsQueued === 0 && (
                      <p>The agent finished without queuing any drafts. {run.output ? `It said: ${run.output.slice(0, 300)}` : ""}</p>
                    )}
                    {!isActive(run) && run.status !== "completed" && (
                      <p className="alert alert-error">
                        The run ended as “{run.status}”{run.error ? `: ${run.error}` : "."}
                        {draftsQueued > 0 ? ` ${draftsQueued} draft${draftsQueued === 1 ? " was" : "s were"} queued before that.` : ""}
                      </p>
                    )}
                  </div>
                )}

                <div className="form-actions">
                  {run && draftsQueued > 0 ? (
                    <Link to="/approvals" className="btn btn-primary">
                      Review drafts
                    </Link>
                  ) : (
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={!agentId || starting || isActive(run)}
                      onClick={draftPosts}
                    >
                      {starting || isActive(run) ? "Drafting…" : run ? "Try again" : "Draft posts"}
                    </button>
                  )}
                  <button type="button" className="btn btn-outline" disabled={isActive(run)} onClick={() => goTo(2)}>
                    Back
                  </button>
                </div>
              </div>
            )}
          </section>
        )}
      </div>
    </Layout>
  );
}
