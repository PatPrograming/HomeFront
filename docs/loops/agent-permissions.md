# Loop: agent-permissions

Status: PLANNING
Profile: feature (security: trial full)
Round: 0 / 4
Base commit: 78824f6 (origin/main 23fa467 + Lane G roles merge f17833b + main-breakage fix)
Branch: claude/agent-perms-huy-lane-h

## Original Request

> "In the agents tab, we have agents for email, social media and research, ensure that in the your assitant console tab can perform all the following functions from the user prompt. Also when creating an agent, add a selection of permission it can do and then provide the recommended permissions, when the user change the permission before enter, it should warn on the side how the change in permission affects. test for edge cases of users trying to get extra privilege"

## Desired Outcome

A staffer can ask the "Your assistant" chat to do anything the Agents-tab agents (email, social,
research, market research) can do, and every such call goes through the mandate gateway: per-call
DynamoDB check, Cedar decision, role forbids, outbound drafts held for review, ATLAS-labelled event.
Creating an agent shows a permission picker pre-filled with the least-privilege recommendation for
its kind, capped by the creator's role, with a side panel that explains each change; the server
enforces the same caps on create, edit and every run, and tests prove each escalation attempt fails.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Contracts: every Agents-tab capability has a mandate tool, Cedar action, role policy and starter permit; role table is the single source | `scripts/offline-pytest.sh -q packages/mandate-contracts/tests/test_capability_parity.py` | exit 0 |
| C2 | Assistant does each agent kind's job through guarded() (email, mailing list, social post/reply, research APIs, market research, report) with Cedar + role + held drafts + ATLAS events | `scripts/offline-pytest.sh -q packages/identity-gateway/tests/test_gw_agent_parity.py` | exit 0 |
| C3 | Permission catalog API: options = kind ceiling ∩ creator role, recommended = least privilege, plain-words effects | `scripts/offline-pytest.sh -q Backend/tests/test_agent_permissions_api.py` | exit 0 |
| C4 | Escalation suite: forged payloads, name variants, unknown/duplicate tools, edit-to-add, other user/office, admin-only tools, role change after create, direct API bypass, token reuse across sessions, prompt injection | `scripts/offline-pytest.sh -q Backend/tests/test_privilege_escalation.py packages/identity-gateway/tests/test_gw_escalation.py` | exit 0 |
| C5 | Assistant agent: system prompt names the new capabilities and a scripted model's forbidden tool call is DENIED | `scripts/offline-pytest.sh -q agent/tests/test_assistant_parity.py` | exit 0 |
| C6 | Backend + packages suite stays green | `scripts/offline-pytest.sh -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests` | exit 0 |
| C7 | Gateway suite stays green | `scripts/offline-pytest.sh -q packages/identity-gateway/tests` | exit 0 |
| C8 | Agent + guardrail suite stays green | `scripts/offline-pytest.sh -q agent/tests packages/guardrail-scanner/tests` | exit 0 |
| C9 | Permission picker + side warning UI tests | `cd Frontend && npx vitest run src/pages/NewAgent.test.tsx src/lib/permissions.test.ts` | exit 0 |
| C10 | Frontend typecheck, all tests, build | `cd Frontend && npm run -s build && npx vitest run` | exit 0 |
| C11 | New Cedar policies validate in the local Cedar engine and setup.sh deploys them only as a dry run by default | `scripts/offline-pytest.sh -q Backend/tests/test_setup_role_policies.py packages/identity-gateway/tests/test_gw_roles.py` | exit 0 |

## Not Checkable By Command

- A real Bedrock model choosing the right new tool from a plain-language prompt: Huy, in the live console
  (offline checks use a scripted model).
- The new Cedar policies in the real AVP policy store: Huy runs the dry-run, then `--apply` himself.
- Real agency API calls with gateway-held keys and real SES sends: Huy, live (tests stub the HTTP layer).
- The picker and side panel look right at 375px and on desktop: team-verifier and the trial check in the
  browser preview with a stubbed API.

## Plan

(team-architect)

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|

## Rounds

## Decisions

- 2026-11-08: base: origin/main (23fa467) fails 66 Backend tests with NameError `_scrape_domain_problem`
  (definition dropped in merge ae534b9). Restored it from b9ffda0 in 78824f6 so the suites are a usable
  baseline. Lane G's role tests import `cedarpy`, which was missing from requirements; added.
- 2026-11-08: lead approves the outcome contract (not Huy), per the lane brief; no Huy stop.
