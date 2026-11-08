// Permission picker logic for the new-agent page. The server decides what is allowed
// (GET /agent-permissions); this file only helps the person see what their choices change.

export type PermissionRisk = "low" | "medium" | "high";

export interface PermissionOption {
  name: string;
  label: string;
  effect: string;
  risk: PermissionRisk;
  outbound: boolean;
  held_for_review: boolean;
  allowed_for_kind: boolean;
  allowed_for_role: boolean;
  recommended: boolean;
  reason_unavailable: string | null;
}

export interface AgentPermissions {
  kind: string;
  role: string;
  role_label: string;
  tools: PermissionOption[];
}

export interface PermissionChange {
  name: string;
  label: string;
  effect: string;
  risk: PermissionRisk;
  beyondRecommendation: boolean;
  // Set only for an added tool the server holds for a person's approval.
  review: string | null;
}

const HELD_FOR_REVIEW = "Held for review: a person approves each one before anything goes out.";

export const isAvailable = (option: PermissionOption) => option.allowed_for_kind && option.allowed_for_role;

export const initialSelection = (options: PermissionOption[]): string[] =>
  options.filter((o) => o.recommended && isAvailable(o)).map((o) => o.name);

const toChange = (option: PermissionOption, added: boolean): PermissionChange => ({
  name: option.name,
  label: option.label,
  effect: option.effect,
  risk: option.risk,
  beyondRecommendation: added,
  review: added && option.held_for_review ? HELD_FOR_REVIEW : null,
});

/** What the selection adds to or removes from the recommendation. Unavailable tools never count. */
export function diffFromRecommended(options: PermissionOption[], selected: string[]) {
  const available = options.filter(isAvailable);
  return {
    added: available.filter((o) => !o.recommended && selected.includes(o.name)).map((o) => toChange(o, true)),
    removed: available.filter((o) => o.recommended && !selected.includes(o.name)).map((o) => toChange(o, false)),
  };
}
