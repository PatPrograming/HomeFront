import { useEffect, useState } from "react";
import Layout from "../components/Layout.jsx";
import Hero from "../components/Hero/Hero.jsx";

const DEFAULTS = {
  mission: ["Mission", "statement."],
  buttons: ["button1", "button2"],
};

export default function Home() {
  const [content, setContent] = useState(DEFAULTS);

  useEffect(() => {
    fetch("/api/home")
      .then((r) => r.json())
      .then(setContent)
      .catch(() => setContent(DEFAULTS));
  }, []);

  return (
    <Layout>
      <Hero mission={content.mission} buttons={content.buttons} />
    </Layout>
  );
}
