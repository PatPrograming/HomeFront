import { useEffect, useRef, useState, type CSSProperties } from "react";
import Layout from "../components/Layout";
import Stepper from "../components/Stepper/Stepper";
import PlatformIcon, { PLATFORMS, type PlatformId } from "../components/PlatformIcons/PlatformIcons";
import { OptionGroup } from "./Emails";
import "./SocialPlatform.css";
import "./Emails.css";

const SOCIAL_ACTIONS = ["Draft", "Post/comment", "Only summarize"];

interface ImageItem {
  id: string;
  file: File;
  url: string;
}

const brandVars = (color: string) =>
  ({ "--brand": color, "--glyph-cut": color }) as CSSProperties;

const STEPS = ["Select platforms", "Add content", "Agent restrictions"];

// Three-stage flow: (1) pick the platforms the agency is active on, (2) provide the message,
// links and images, (3) set agent restrictions. Everything is local UI state for now.
export default function SocialPlatform() {
  const [step, setStep] = useState(1);
  const [selected, setSelected] = useState<PlatformId[]>([]);
  const [message, setMessage] = useState("");
  const [links, setLinks] = useState([""]);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [agentAction, setAgentAction] = useState("");
  // TODO: pass restrictions to the backend as an agent restriction.
  const [restrictions, setRestrictions] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const imagesRef = useRef(images);
  imagesRef.current = images;

  // Release object URLs for any previews still around on unmount.
  useEffect(() => () => imagesRef.current.forEach((i) => URL.revokeObjectURL(i.url)), []);

  const togglePlatform = (id: PlatformId) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((p) => p !== id) : [...cur, id]));

  const goTo = (n: number) => {
    setStep(n);
    setSubmitted(false);
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
        ) : step === 3 ? (
          <form
            className="content-main"
            aria-labelledby="sp-title-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (agentAction !== "") setSubmitted(true);
            }}
          >
            <h1 id="sp-title-3" className="sp-title">Agent restrictions</h1>
            <p className="sp-sub">Define what the agent may do on social platforms.</p>

            <div className="card">
              <OptionGroup
                legend="Should the agent draft, post/comment, or only summarize new updates?"
                hint="Select one."
                options={SOCIAL_ACTIONS}
                type="radio"
                name="social-agent-action"
                isOn={(o) => agentAction === o}
                onChange={setAgentAction}
              />
            </div>

            <div className="card">
              <div className="field">
                <label className="field-label" htmlFor="restrictions">
                  Are there topics the agent must never answer or other restrictions?
                </label>
                <p className="field-hint" id="restrictions-hint">
                  These will be applied as restrictions to the AI agent. Optional.
                </p>
                <textarea
                  id="restrictions"
                  className="textarea"
                  rows={6}
                  aria-describedby="restrictions-hint"
                  placeholder="e.g. Never comment on pending litigation or give legal advice…"
                  value={restrictions}
                  onChange={(e) => setRestrictions(e.target.value)}
                />
                <span className="field-count">{restrictions.length} characters</span>
              </div>
            </div>

            {submitted && (
              <p role="status" className="sp-count">Your social platform settings have been saved locally.</p>
            )}

            <div className="em-nav">
              <button type="button" className="btn btn-outline" onClick={() => goTo(2)}>Back</button>
              <button type="submit" className="btn btn-primary" disabled={agentAction === ""}>Submit</button>
            </div>
          </form>
        ) : (
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
                          value= {link} /*{link.length > 8 && link.startsWith("https://") ? link : `https://${link}`} to be included handling backend data.*/
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
                  <p className="field-hint">Graphs, maps or photos that support the message.</p>
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
        )}
      </div>
    </Layout>
  );
}
