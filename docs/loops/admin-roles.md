# Loop: admin-roles

Status: BUILDING
Profile: feature (authorization: full trial)
Round: 1 / 4
Base commit: 679f0ad (Lane G, claude/roles-huy-631b8e)
Branch: Admin-Roles-Huy

## Original Request

Huy, via the lead: "an admin tab that manages permissions per group and assigns users to groups."

The lead's brief (goal, verbatim): "an admin-only "People & roles" tab where an admin (1) sees each office role
(Chief of Staff, Caseworker, Communications Director, Legislative Aide, Intern) and what it may do, (2) assigns
each user to a role, and (3) changes what a role may do, all enforced server-side."

Escalation tests the brief requires (verbatim): "staffer calling the admin routes; admin assigning a non-existent
role; giving a role an always-denied or admin-only tool; an admin demoting themselves out of admin with no other
admin left; a role change taking effect mid-session; forged role in the request body."

## Desired Outcome

An admin opens "People & roles", sees every Cognito user with their group and office role, sees what each office
role may do, assigns a user's role or group, and edits a role's tools after reading a plain-words warning. Cedar
in Verified Permissions still decides every tool call: the role's tool list comes from DynamoDB on every call and
reaches Cedar as `principal.roleTools`, so a change applies on the next call, and the reviewed policies never
change at runtime. Every change writes an audit event. Only the admin group, checked on the server, gets in.

## Outcome Checks

Every command strips AWS credentials (`scripts/offline-pytest.sh`). No check calls AWS.

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Role table in DynamoDB reaches real Cedar: edits apply on the next call, mid-session, fail closed, forbids win | `scripts/offline-pytest.sh -q packages/identity-gateway/tests/test_gw_role_table.py` | exit 0 |
| C2 | People & roles API: admin only (real token check), list, assign, edit, preview words, audit, every escalation case | `scripts/offline-pytest.sh -q Backend/tests/test_people_roles.py` | exit 0 |
| C3 | Contracts: role policies are role-agnostic and match the files; validator rejects always-denied, admin-only, unknown, duplicate | `scripts/offline-pytest.sh -q packages/mandate-contracts/tests` | exit 0 |
| C4 | setup.sh updates changed role policies only with `--update-role-policies`, dry run by default | `scripts/offline-pytest.sh -q Backend/tests/test_setup_role_policies.py` | exit 0 |
| C5 | Backend and packages suite stays green | `scripts/offline-pytest.sh -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests packages/identity-gateway/tests` | exit 0 |
| C6 | Agent suite stays green | `scripts/offline-pytest.sh -q agent/tests packages/guardrail-scanner/tests` | exit 0 |
| C7 | Console: People page tests, typecheck, build, all tests | `cd Frontend && npx tsc --noEmit && npm run -s build && npx vitest run` | exit 0 |
| C8 | Console has the People page test | `cd Frontend && npx vitest run src/pages/People.test.tsx` | exit 0 |

At round 0, C1, C2 and C8 fail because the files do not exist. C3 and C4 fail on the new assertions.

## Not Checkable By Command

- Real Cognito `ListUsers` and group changes against pool `us-east-1_MLfQpodxG`, and the IAM permissions they
  need: Huy, after rollout.
- The updated role policies in the real AVP store (`setup.sh --apply --update-role-policies`): Huy.
- The page looks right at 375px and on desktop: the trial's browser check, in mock mode.

## Plan

1. **Contracts** (`roles.py`, `cedar.py`, `ddb.py`, policies):
   - Role policy template becomes role-agnostic: `forbid(action == X) unless { principal has roleTools &&
     principal.roleTools.contains("X") }`. Policy ids stay `role-<tool>`; files regenerated.
   - `ADMIN_ONLY_TOOLS` (tools only the Chief of Staff role may hold), `assignable_tools(role)`,
     `validate_role_tools(role, tools)` (unknown role, unknown or duplicate tool, always-denied, admin-only),
     `effective_role_tools(role, stored)` (unknown role → empty; no stored list → the code default
     `ROLE_TOOLS`; always intersected with `assignable_tools`), `describe_role_change(role, before, after)`.
   - `cedar.agent_entity` gains `roleTools` (a Cedar set). `ddb.role_key`, `RoleItem`, `admin_event_key`,
     `AdminEventItem`.
2. **Store + gateway** (`storage.py`, `authz.py`, `gateway.py`): `get_role_tools(role)` reads `ROLE#<role>` fresh
   (ConsistentRead) on every call; `put_role_tools`, `list_staffers`, `set_office_role`, `set_staffer_group`,
   `put_admin_event`, `admin_events`. `AuthzRequest.role_tools` flows to AVP and the test double.
3. **Consumers of the role table**: `sessions.allowed_tools` and `assistant._within_role` use the store.
4. **API** (`Backend/app/routers/people.py`, `Backend/app/services/directory.py`): `GET /admin/people`,
   `GET /admin/roles`, `POST /admin/roles/{role}/preview`, `PUT /admin/roles/{role}`, `PUT /admin/people/{username}`,
   `GET /admin/audit`. `require_admin` plus a fresh DynamoDB check that the caller's STAFFER item is an active
   admin. Bodies `extra="forbid"`. Last-admin guard. Cognito via a small directory wrapper; tests inject a fake.
5. **setup.sh** `--update-role-policies`: update a `role-*` policy whose definition differs (dry run default).
6. **Console**: `People.tsx` at `/admin/people`, admin route + nav link, api + mock, test.
7. **Docs**: `docs/ROLES.md` section, `Backend/infra/README.md` IAM + rollout.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Contracts | lead | packages/mandate-contracts/** | C3 | - | todo |
| 2 | Store + gateway | lead | packages/identity-gateway/src/identity_gateway/{storage,authz,gateway}.py, tests | C1 | 1 | todo |
| 3 | Role table consumers | lead | Backend/app/routers/{sessions,assistant}.py | C5 | 2 | todo |
| 4 | API | lead | Backend/app/routers/people.py, Backend/app/services/directory.py, Backend/app/main.py, Backend/tests/test_people_roles.py | C2 | 2 | todo |
| 5 | setup.sh | lead | Backend/infra/setup.sh, Backend/tests/test_setup_role_policies.py | C4 | 1 | todo |
| 6 | Console | lead | Frontend/src/** | C7, C8 | 4 | todo |
| 7 | Docs | lead | docs/ROLES.md, Backend/infra/README.md | review | 4 | todo |

## Rounds

## Decisions

- The lead's brief is the approved contract; no separate AWAITING_APPROVAL stop (same as Lane G). The brief set
  the goal, the design preference and the escalation tests.
- Built inline by this lane instead of team agents, like Lane G, for the demo deadline. Independence comes from
  the full /trial.
- Design (a): the role→tools table lives in DynamoDB (`ROLE#<role>`), the gateway reads it on every call and
  passes it to Cedar as `principal.roleTools`. Policies stay reviewed code and never change at runtime. A role
  with no stored item uses the reviewed code default, so nothing changes until an admin edits a role.
- The role-agnostic forbid keeps the Lane G property: a role can only take tools away from the starter permits,
  never add one; `forbid-bulk-export` and `forbid-campaign-post` still bind every role.
- Defense in depth: the gateway intersects the stored list with the assignable set, so a bad item written
  straight to DynamoDB still cannot grant an always-denied or admin-only tool.
- Admin routes also check the caller's STAFFER item (`role == "admin"`, active) fresh from DynamoDB, so a demoted
  or offboarded admin loses the tab at once, not when their access token expires.
