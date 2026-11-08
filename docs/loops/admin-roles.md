# Loop: admin-roles

Status: DONE
Profile: feature (authorization: full trial)
Round: 2 / 4
Base commit: 0799095 (Lane H, Agent-Permissions-Huy; contracts 404cdff). First built on Lane G 679f0ad (old tip a605f42), then replayed here on the lead's ruling.
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
| C3 | Contracts: role policies read principal.roleTools and match the files; validator rejects always-denied, locked, unknown, duplicate | `scripts/offline-pytest.sh -q packages/mandate-contracts/tests` | exit 0 |
| C4 | setup.sh updates changed role policies only with `--update-role-policies`, dry run by default | `scripts/offline-pytest.sh -q Backend/tests/test_setup_role_policies.py` | exit 0 |
| C5 | Gateway and contracts suites stay green (run per package: see Decisions) | `scripts/offline-pytest.sh -q packages/identity-gateway/tests && scripts/offline-pytest.sh -q packages/mandate-contracts/tests packages/atlas-enricher/tests` | exit 0 |
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
   - Lane H's `LOCKED_TOOLS` and `effective_role_tools(role, tools)` (owned by Lane H) are reused, not
     duplicated. Added: `assignable_tools(role)`, `validate_role_tools(role, tools)` (unknown role, unknown or
     duplicate tool, always-denied, locked), `describe_role_change(role, before, after)`. A locked tool's
     forbid also names its roles.
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
| 1 | Contracts | lead | packages/mandate-contracts/** | C3 | - | done |
| 2 | Store + gateway | lead | packages/identity-gateway/src/identity_gateway/{storage,authz,gateway}.py, tests | C1 | 1 | done |
| 3 | Role table consumers | lead | Backend/app/routers/{sessions,assistant}.py | C5 | 2 | done |
| 4 | API | lead | Backend/app/routers/people.py, Backend/app/services/directory.py, Backend/app/main.py, Backend/tests/test_people_roles.py | C2 | 2 | done |
| 5 | setup.sh | lead | Backend/infra/setup.sh, Backend/tests/test_setup_role_policies.py | C4 | 1 | done |
| 6 | Console | lead | Frontend/src/** | C7, C8 | 4 | done |
| 7 | Docs | lead | docs/ROLES.md, Backend/infra/README.md | review | 4 | done |

## Rounds

### Round 1

- Outcome checks (re-run 2026-11-08 by the resumed lane session, C5 per package): 8/8 pass. C1 9, C2 63, C3 179,
  C4 9, C5 identity-gateway 717 + contracts/enricher 182, C6 103, C7 vitest 113 + tsc + build, C8 7. The first run
  (C5 as one combined pytest run, 202 failed from same-named module collisions) is kept as `round-1.first-run.json`.
- Backend suite, HEAD 7c5e966 vs base 0799095: 193 failed / 636 passed vs 196 failed / 561 passed. No failure exists
  only at HEAD; the shared ones are `test_agent_permissions_api.py` (77), `test_privilege_escalation.py` (115) and
  `test_gateway_api.py::test_session_role_and_allowed_tools_come_from_the_staffer_item` (Lane H's base). HEAD fixes 3
  `test_setup_role_policies.py` failures.
- Integrity scan: 11 flags, all benign (fixture `noqa` imports, the console's dev-only mock module, logged output).
- Full /trial: advocate PARTIAL (no mutation probes; stopped early). Falsifier FALSIFIED on one item, 7/7 mutations
  caught.
- Gap list:
  1. (falsifier #8) The last-admin guard counted any enabled Cognito admin, but the admin routes also need an active
     admin STAFFER item. The only working admin could demote themselves after promoting someone with no STAFFER item,
     or while an offboarded admin stayed in Cognito, leaving nobody able to manage roles.
- Decision: next round. Gap 1 fixed in 8d4bb4c (`_is_working_admin`, shared with `require_people_admin`), with
  `test_an_admin_who_cannot_use_the_admin_routes_does_not_count_as_another_admin`; it fails with the new condition
  removed.

### Round 2

- Outcome checks at 8d4bb4c: 8/8 pass (C2 now 64). Compared with round 1: fixed [], regressed [].
- Judge (full trial, both reports, HEAD 8d4bb4c): **ACCEPT**. It re-ran the last-admin scenarios A (promoted through
  the tab, no STAFFER item), B (other admin offboarded through the real route), C (Cognito admin, no STAFFER item)
  and E (Cognito admin whose STAFFER role is staffer). At HEAD each gives 409. With the fix mutated away, all four and
  the new test fail. Every other requirement is MET; the claims audit backs all 5 claims. Advocate error noted:
  it called the last-admin item proven from a happy-case test.
- Gap list: empty.
- Follow-ups for Huy (not blockers): run `setup.sh --apply --update-role-policies` after the new gateway is deployed;
  decide whether admin routes should be scoped to the admin's own office (`ROLE#<role>` is global today).
- Decision: DONE.

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
- Lead ruling (relayed 2026-11-08): rebase onto Lane H 8f7dba6+ (done on 0799095, after Lane H's gateway commit
  landed), reuse Lane H's `LOCKED_TOOLS` (`email_send_list` → Chief of Staff only; greyed "locked by policy" in the
  editor; server refuses whatever the client sends), and keep `role_allows(role, dotted, tools=None)` for Lane H's
  Agents-tab cap.
- `test_gw_guarded.py::test_scan_runs_only_for_untrusted_output_tools` fails on Lane H's base 0799095 itself (it
  asserts only two untrusted tools; Lane H added the research APIs). Not mine to change; reported to Lane H and
  deselected in C5.
- Admin routes also check the caller's STAFFER item (`role == "admin"`, active) fresh from DynamoDB, so a demoted
  or offboarded admin loses the tab at once, not when their access token expires.
