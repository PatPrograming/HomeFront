import Layout from "../components/Layout";
import Hero from "../components/Hero/Hero";
import "./Home.css";

const DEFAULTS = {
  mission: ["Agentic Outreach for", "Federal Agencies."],
  buttons: ["Social Platform", "button2"],
};

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

export default function Home() {
  return (
    <Layout>
      <Hero mission={MISSION} />

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
    </Layout>
  );
}
