// Builds the JSON the "create agent" form POSTs to /api/v1/agents. One shape for all three jobs
// (email, social, research): the job is `kind`, the rest is name, agency, instructions, tools and
// max steps per run. The backend (CreateAgentRequest in agents_api.py) takes exactly these fields
// and answers with the saved agent (the Agent type in agents.ts).
import type { AgentKind } from "./agents";
import type { CreateAgentBody } from "./agentsApi";

export const MIN_STEPS = 1;
export const MAX_STEPS = 50;
export const DEFAULT_STEPS = 10;

/** What the form holds. `kind` is the agent's job. */
export interface AgentForm {
  kind: AgentKind;
  name: string;
  agency: string;
  instructions: string;
  toolGrants: string[];
  maxSteps: number;
}

/** The six fields the backend receives for a new agent. */
export type AgentPayload = Required<
  Pick<CreateAgentBody, "kind" | "name" | "agency" | "instructions" | "tool_grants" | "max_steps">
>;

export function clampSteps(n: number): number {
  return Number.isFinite(n) ? Math.min(MAX_STEPS, Math.max(MIN_STEPS, Math.round(n))) : DEFAULT_STEPS;
}

export function buildAgentPayload(form: AgentForm): AgentPayload {
  return {
    kind: form.kind,
    name: form.name.trim(),
    agency: form.agency.trim(),
    instructions: form.instructions,
    tool_grants: [...new Set(form.toolGrants)],
    max_steps: clampSteps(form.maxSteps),
  };
}
