import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { AgentAccessView, type AgentCredentials } from "./AgentAccessView";

export default function AgentAccess() {
  const [mode, setMode] = useState<AgentCredentials | null>(null);
  useEffect(() => {
    let live = true;
    api
      .assistantStatus()
      .then((s) => live && setMode(s.agentCredentials))
      .catch(() => {}); // the notice is advice; the rest of Technical details works without it
    return () => {
      live = false;
    };
  }, []);
  return <AgentAccessView mode={mode} />;
}
