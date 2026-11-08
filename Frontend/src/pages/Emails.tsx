import { useState } from "react";
import Layout from "../components/Layout";
import Stepper from "../components/Stepper/Stepper";
import "./SocialPlatform.css";
import "./Emails.css";

const MAILBOXES = ["Public inbox", "Press", "FOIA", "Program-specific", "GovDelivery lists"];
const PLATFORMS = ["Microsoft 365/GCC", "Google Workspace", "GovDelivery", "Other"];
const MAIL_TYPES = ["FAQs", "Complaints", "Media requests", "Social benefits"];
const AGENT_ACTIONS = ["Draft", "Send", "Prioritize", "Only summarize"];

const STEPS = ["Select mailboxes", "Email frequency", "Agent restrictions"];

interface OptionGroupProps {
  legend: string;
  hint?: string;
  options: string[];
  type: "checkbox" | "radio";
  name: string;
  isOn: (o: string) => boolean;
  onChange: (o: string) => void;
}

// Accessible checkbox / radio card group.
export function OptionGroup({ legend, hint, options, type, name, isOn, onChange }: OptionGroupProps) {
  return (
    <fieldset className="field">
      <legend className="field-label">{legend}</legend>
      {hint && <p className="field-hint">{hint}</p>}
      <div className="opt-list">
        {options.map((o) => {
          const on = isOn(o);
          return (
            <label key={o} className={`opt${on ? " is-on" : ""}`}>
              <input
                type={type}
                name={name}
                className="sr-only"
                checked={on}
                onChange={() => onChange(o)}
              />
              <span className={`opt-mark ${type === "checkbox" ? "is-check" : "is-radio"}`} aria-hidden="true">
                {type === "checkbox" && (
                  <svg viewBox="0 0 16 16" width="14" height="14">
                    <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </span>
              {o}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

const toggle = (list: string[], v: string) =>
  list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

// Three-section email survey. Everything is local UI state for now.
export default function Emails() {
  const [step, setStep] = useState(1);
  const [mailboxes, setMailboxes] = useState<string[]>([]); // Q1 (multi)
  const [mailPlatforms, setMailPlatforms] = useState<string[]>([]); // Q2 (multi)
  const [mailType, setMailType] = useState(""); // Q3 (single)
  const [perDay, setPerDay] = useState(""); // Q4 (integer, kept as string for input)
  const [agentAction, setAgentAction] = useState(""); // Q5 (single)
  // Q6: free-text restrictions for the built-in AI agent.
  // TODO: pass this to the backend as an agent restriction.
  const [restrictions, setRestrictions] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const goTo = (n: number) => {
    setStep(n);
    setSubmitted(false);
    window.scrollTo({ top: 0 });
  };

  const step1Valid = mailboxes.length > 0 && mailPlatforms.length > 0 && mailType !== "";
  const step2Valid = /^\d+$/.test(perDay);
  const step3Valid = agentAction !== "";

  return (
    <Layout>
      <div className="container sp">
        <header className="sp-head">
          <Stepper steps={STEPS} current={step} />
        </header>

        {step === 1 && (
          <form className="content-main" aria-labelledby="em-title-1" onSubmit={(e) => e.preventDefault()}>
            <h1 id="em-title-1" className="sp-title">Select mailboxes</h1>
            <p className="sp-sub">Tell us which mailboxes the agents will work with.</p>

            <div className="card">
              <OptionGroup
                legend="Which mailboxes should agents handle?"
                hint="Select all that apply."
                options={MAILBOXES}
                type="checkbox"
                name="mailboxes"
                isOn={(o) => mailboxes.includes(o)}
                onChange={(o) => setMailboxes((c) => toggle(c, o))}
              />
            </div>
            <div className="card">
              <OptionGroup
                legend="Which mailing platform do you use?"
                hint="Select all that apply."
                options={PLATFORMS}
                type="checkbox"
                name="mail-platforms"
                isOn={(o) => mailPlatforms.includes(o)}
                onChange={(o) => setMailPlatforms((c) => toggle(c, o))}
              />
            </div>
            <div className="card">
              <OptionGroup
                legend="What mail type is most common?"
                hint="Select one."
                options={MAIL_TYPES}
                type="radio"
                name="mail-type"
                isOn={(o) => mailType === o}
                onChange={setMailType}
              />
            </div>

            <div className="sp-actions">
              <p className="sp-count" aria-live="polite">
                {step1Valid ? "Ready to continue" : "Answer all three questions to continue"}
              </p>
              <button type="button" className="btn btn-primary" disabled={!step1Valid} onClick={() => goTo(2)}>
                Confirm
              </button>
            </div>
          </form>
        )}

        {step === 2 && (
          <form className="content-main" aria-labelledby="em-title-2" onSubmit={(e) => e.preventDefault()}>
            <h1 id="em-title-2" className="sp-title">Email frequency</h1>
            <p className="sp-sub">Help us size the workload for your agents.</p>

            <div className="card">
              <div className="field">
                <label className="field-label" htmlFor="per-day">How many emails do you get per day?</label>
                <p className="field-hint">Enter a whole number.</p>
                <input
                  id="per-day"
                  type="number"
                  inputMode="numeric"
                  min={0}
                  step={1}
                  className="input number-input"
                  placeholder="e.g. 250"
                  value={perDay}
                  onChange={(e) => setPerDay(e.target.value)}
                />
              </div>
            </div>

            <div className="em-nav">
              <button type="button" className="btn btn-outline" onClick={() => goTo(1)}>Back</button>
              <button type="button" className="btn btn-primary" disabled={!step2Valid} onClick={() => goTo(3)}>
                Confirm
              </button>
            </div>
          </form>
        )}

        {step === 3 && (
          <form
            className="content-main"
            aria-labelledby="em-title-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (step3Valid) setSubmitted(true);
            }}
          >
            <h1 id="em-title-3" className="sp-title">Agent restrictions</h1>
            <p className="sp-sub">Define what the agent may do with your email.</p>

            <div className="card">
              <OptionGroup
                legend="Should the agent draft, send, prioritize emails, or only summarize?"
                hint="Select one."
                options={AGENT_ACTIONS}
                type="radio"
                name="agent-action"
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
              <p role="status" className="sp-count">Your email settings have been saved locally.</p>
            )}

            <div className="em-nav">
              <button type="button" className="btn btn-outline" onClick={() => goTo(2)}>Back</button>
              <button type="submit" className="btn btn-primary" disabled={!step3Valid}>Submit</button>
            </div>
          </form>
        )}
      </div>
    </Layout>
  );
}
