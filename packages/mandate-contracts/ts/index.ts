// Mandate contracts for the frontend (brief §6). Mirrors packages/mandate-contracts/src/mandate_contracts.
// Keep in sync by hand; tests/test_contracts.py checks the tool names and event fields match.

/** Office roles (roles.py, docs/ROLES.md). The STAFFER item holds one; Cedar enforces it. */
export const ROLES = ["chief_of_staff", "caseworker", "communications_director", "legislative_aide", "intern"] as const;
export type Role = (typeof ROLES)[number];

/** The tools each role may use (roles.py ROLE_TOOLS); for display and mocks only, Cedar enforces it. */
export const ROLE_TOOLS: Record<Role, readonly string[]> = {
  chief_of_staff: ["inbox_read", "case_read", "datagov_search", "web_scrape", "email_send", "social_post", "email_send_list", "social_reply", "usaspending_search", "sam_entity_lookup", "report_save", "census_query", "openfda_search", "eia_query", "nasa_search", "noaa_climate", "nps_parks", "bls_series", "fec_search", "epa_search", "fcc_search"],
  caseworker: ["inbox_read", "case_read", "datagov_search", "web_scrape", "email_send", "census_query", "openfda_search", "eia_query", "nasa_search", "noaa_climate", "nps_parks", "bls_series", "fec_search", "epa_search", "fcc_search", "report_save"],
  communications_director: ["inbox_read", "datagov_search", "web_scrape", "email_send", "social_post", "social_reply", "census_query", "openfda_search", "eia_query", "nasa_search", "noaa_climate", "nps_parks", "bls_series", "fec_search", "epa_search", "fcc_search", "report_save"],
  legislative_aide: ["inbox_read", "datagov_search", "web_scrape", "census_query", "openfda_search", "eia_query", "nasa_search", "noaa_climate", "nps_parks", "bls_series", "fec_search", "epa_search", "fcc_search", "usaspending_search", "sam_entity_lookup", "report_save"],
  intern: ["datagov_search", "web_scrape", "census_query", "nasa_search", "nps_parks", "noaa_climate"],
};

export const ROLE_LABELS: Record<Role, string> = {
  chief_of_staff: "Chief of Staff",
  caseworker: "Caseworker",
  communications_director: "Communications Director",
  legislative_aide: "Legislative Aide",
  intern: "Intern",
};

export const TOOL_NAMES = [
  "inbox_read",
  "case_read",
  "datagov_search",
  "web_scrape",
  "email_send",
  "social_post",
  "email_send_list",
  "social_reply",
  "usaspending_search",
  "sam_entity_lookup",
  "report_save",
  "census_query",
  "openfda_search",
  "eia_query",
  "nasa_search",
  "noaa_climate",
  "nps_parks",
  "bls_series",
  "fec_search",
  "epa_search",
  "fcc_search",
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
  | "approved by staff"
  | "needs connection";

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
