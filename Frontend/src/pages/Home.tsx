import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Layout from "../components/Layout";
import Hero from "../components/Hero/Hero";
import { api } from "../lib/api";
import "./Home.css";

// "Agentic Outreach / for Federal Agencies." — second line is accented in the hero.
const MISSION = ["Agentic Outreach for", "Federal Agencies."];

const DEFAULTS: { mission: string[]; buttons?: string[] } = { mission: MISSION };

const STEPS = [
  {
    n: "01",
    title: "Choose your channels",
    body: "Tick the platforms your agency is active on, such as X, Bluesky or Reddit.",
  },
  {
    n: "02",
    title: "Share the essentials",
    body: "Write the facts that must reach the public, attach official links, and add supporting images or graphs.",
  },
  {
    n: "03",
    title: "Agents do the outreach",
    body: "Agents email, post, reply, scrape and query api.data.gov under tightly scoped, auditable permissions.",
  },
];

const CONTROLS = [
  {
    n: "01",
    title: "Authenticate the agent",
    body: "Each agent gets its own workload identity and a short-lived token. No anonymous calls reach a tool.",
    tag: "Workload token",
  },
  {
    n: "02",
    title: "Allow selected MCP tools",
    body: "Grants are per agent and per tool, such as datagov.search or email.send. Anything not granted is denied by default.",
    tag: "Least privilege",
  },
  {
    n: "03",
    title: "Revoke access mid-task",
    body: "Pull a grant while the agent is still running. The change takes effect at once, with no need to restart the run.",
    tag: "Live revoke",
  },
  {
    n: "04",
    title: "Block every request after",
    body: "The gateway checks every tool call, so the next request is refused and logged with its MITRE ATLAS label.",
    tag: "AML.T0053 · AML.M0028",
  },
];

export default function Home() {
  const [content, setContent] = useState(DEFAULTS);

  useEffect(() => {
    api.home().then(setContent).catch(() => setContent(DEFAULTS));
  }, []);

  return (
    <Layout>
      <Hero mission={content.mission} buttons={content.buttons} />

      <section id="how-it-works" className="how">
        <div className="container">
          <p className="eyebrow">How it works</p>
          <h2 className="how-title">From a single brief to every channel.</h2>
          <ol className="how-grid">
            {STEPS.map((s) => (
              <li key={s.n} className="how-card">
                <span className="how-n">{s.n}</span>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="regulation" className="reg">
        <div className="container">
          <p className="eyebrow">How agents are regulated</p>
          <h2 className="reg-title">Every agent has an identity, and every permission can be taken back.</h2>
          <p className="reg-lede">
            Agents never hold standing access. Each tool call passes through a gateway that checks who the
            agent is and what it is allowed to do right now.
          </p>
          <ol className="reg-flow">
            {CONTROLS.map((c) => (
              <li key={c.n} className="reg-step">
                <span className="reg-n">{c.n}</span>
                <h3>{c.title}</h3>
                <p>{c.body}</p>
                <code className="reg-tag">{c.tag}</code>
              </li>
            ))}
          </ol>
          <Link className="btn btn-outline reg-cta" to="/monitoring">
            See it live in Monitoring
          </Link>
        </div>
      </section>
    </Layout>
  );
}
