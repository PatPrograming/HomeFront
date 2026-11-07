import { useEffect, useState } from "react";
import Layout from "../components/Layout.jsx";
import Hero from "../components/Hero/Hero.jsx";
import { api } from "../lib/api.js";

const DEFAULTS = {
  mission: ["Mission", "statement."],
  buttons: ["button1", "button2"],
};

export default function Home() {
  const [content, setContent] = useState(DEFAULTS);

  useEffect(() => {
    api.home().then(setContent).catch(() => setContent(DEFAULTS));
  }, []);

  return (
    <Layout>
      <Hero mission={content.mission} buttons={content.buttons} />
    </Layout>
  );
}
