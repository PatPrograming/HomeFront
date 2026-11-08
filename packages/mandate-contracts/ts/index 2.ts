// Mandate contracts for the frontend (brief §6). Mirrors packages/mandate-contracts/src/mandate_contracts.
// Keep in sync by hand; tests/test_contracts.py checks the tool names and event fields match.

export const TOOL_NAMES = [
  "inbox_read",
  "case_read",
  "datagov_search",
  "web_scrape",
  "email_send",
  "social_post",
  "records_bulk_export",
] as const;

export type ToolName = (typeof TOOL_NAMES)[number];

export type Decision = "ALLOW" | "DENY";

export type Reason =
  | "allowed"
  | "denied by policy"
  | "session revoked / delegator inactive"
  | "invalid token"
  | "held for staff review"
  | "approved by staff";

export interface GuardrailFlag {
  flagged: boolean;
  categories: string[];
}

export interface DecisionEvent {
  id: string;
  ts: string;
  sid: string;
  agent: string;
  actingFor: string;
  office: string;
  tool: string;
  resource: string | null;
  decision: Decision;
  reason: Reason;
  policyIds: string[];
  atlas: string[];
  guardrail: GuardrailFlag;
}

export interface MandateClaims {
  iss: "mandate-broker";
  sub: string;
  act: string;
  office: string;
  mode: "official" | "campaign";
  sid: string;
  scope: string[];
  iat: number;
  exp: number;
}
