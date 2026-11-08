# Loop: agent-permissions

Status: BUILDING
Profile: feature (security: trial full)
Round: 1 / 4
Base commit: 78824f6 (origin/main 23fa467 + Lane G roles merge f17833b + main-breakage fix)
Branch: Agent-Permissions-Huy

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

### Goal

The "Your assistant" chat can do every Agents-tab job (email, mailing-list email, social post and reply,
agency research APIs, procurement market research, saved report) through `guarded()`. Creating an agent
shows a permission picker pre-filled with the least-privilege set for its kind, capped by the creator's
office role, plus a side panel that explains each change. The server enforces the same cap on create, on
edit and on every call, reading the role fresh from the STAFFER item.

### Out of scope

- Opening runs, workload-token minting, revoke, SES admin, approvals or PATCH to non-admins. They stay
  `require_admin`. Only create (`POST /agents`) and grants (`PUT /agents/{id}/grants`,
  `POST /agents/{id}/grants/{tool}`) move to `require_staff`.
- Showing assistant-saved reports in the UI. `report_save` stores them, and C2 checks the store.
- Console.tsx, the chat sidebar and `Backend/app/routers/sessions.py`. `sessions.allowed_tools()` already
  iterates `TOOLS`, so it picks up the new tools with no edit. `IdentityCard`'s "blocked" list will get
  longer, and `toolLabel()` turns unknown names into words.
- Editing `identity_gateway/connections.py` (Lane I). We only import it.

### Context (reuse; do not rebuild)

- Contracts: `mandate_contracts/{tools.py (TOOLS, _Input extra=ignore), roles.py (ROLE_TOOLS,
  ROLE_GOVERNED_TOOLS, _ACTIONS, role_policy_id, ROLE_POLICIES generated), cedar.py (STARTER_POLICIES),
  ddb.py (DraftItem.tool Literal), events.py (Reason)}`, `ts/index.ts`, `schemas/tools.json` (run
  `export_schemas.py`). The lead approved this contract change. Follow `using-mandate-contracts`.
- Gateway: `gateway.py` `LEGACY_TOOLS`, `OUTBOUND_TOOLS`, `_run` → `mcp_tools.handlers.invoke_tool`,
  `_atlas` (uses `TOOL_RESIDUAL_RISK`, which lacks `email.send_list` and the agency APIs, so fall back to
  `mcp_tools.registry.get_tool(dotted).atlas_techniques`). `store.constituent_emails(office)` is the
  mailing list for `email_send_list`. `handle_email_send_list` already takes gateway-injected `recipients`.
  `handle_report_save` already takes a gateway-injected `agent_id`, and its sink is set by `agents_api`.
- Agents tab: `agent_types.py` (ceilings, `disallowed_tools`), `agents_api.py` (`_enforce_kind_ceiling`
  audits and raises 403, `call_tool` already re-checks the kind at call time, `Actor`, `_accessible_agent`
  returns 404, `runs.get(run_id).created_by`), `runner.api_tool_name` (dotted to underscore),
  `agent_iam.include_agents_api` (splits reads and writes by method).
- `assistant.py` `EDITABLE_FIELDS` and `_within_role` (category to tool) must know the new outbound tools,
  or approving a held `email_send_list` or `social_reply` draft raises KeyError.
- `setup.sh` only creates policies (`exists:` / `created:`) and never updates one, so every Cedar change
  must be a new policy file, not an edit to an existing one.

### Design (within the lead's decisions; changes are marked CHANGED)

1. **Role table** (`roles.py`). 15 new governed tools: `email_send_list`, `social_reply`,
   `usaspending_search`, `sam_entity_lookup`, `report_save`, and the 10 research APIs (`census_query`,
   `openfda_search`, `eia_query`, `nasa_search`, `noaa_climate`, `nps_parks`, `bls_series`, `fec_search`,
   `epa_search`, `fcc_search`). The role sets are the lead's (RESEARCH = the 10 APIs):
   - chief_of_staff: every governed tool.
   - caseworker: current + RESEARCH + `report_save`.
   - communications_director: current + `social_reply` + RESEARCH + `report_save`.
   - legislative_aide: current + RESEARCH + `usaspending_search` + `sam_entity_lookup` + `report_save`.
   - intern: `datagov_search`, `web_scrape`, `census_query`, `nasa_search`, `nps_parks`, `noaa_climate`.

   New helpers in `roles.py`: `mandate_tool_name(dotted) -> str`, which raises KeyError for anything not
   in `TOOLS` after `.`→`_`, and `role_allows(role, dotted) -> bool`. `_ACTIONS` gets plain words for
   every new tool and becomes public as `TOOL_ACTIONS`.
2. **Tools** (`tools.py`). One input model per new tool, mirroring `registry.py` properties (`q`,
   `keywords`, `fiscal_year:int`, `name`, `title`/`body`, `for`/`in` via alias, and so on).
   `EmailSendListInput` has only `subject` and `body`; recipients are never input.
   `SocialReplyInput(account, reply_to, text, platform="x")`. `SocialPostInput` gains `platform="x"`
   (Lane I). `untrusted_output=True` for the 10 APIs, `usaspending_search` and `sam_entity_lookup`.
3. **Cedar**, additive only (CHANGED: new files instead of editing `read-tools` / `forbid-campaign-post`,
   because `setup.sh` cannot update an existing AVP policy). New starter policies:
   - `research-tools`: permit the 10 APIs + `usaspending_search` + `sam_entity_lookup` when
     `delegatorActive`.
   - `email-send-list-office`: permit when `delegatorActive`.
   - `social-reply-official`: permit when `delegatorActive && account == "official"`.
   - `forbid-campaign-reply`: forbid when `mode == "official" && account == "campaign"`.
   - `report-save`: permit when `delegatorActive`.

   Plus 15 generated `role-*` forbids. `StarterPolicyAuthorizer` mirrors all of them.
4. **Gateway.**
   - `LEGACY_TOOLS` covers every mapped tool via the dotted name.
   - `OUTBOUND_TOOLS += email_send_list, social_reply`, with `account` sent to Cedar for both social
     tools.
   - New autonomy categories (`autonomy.py`): `MAILING_LIST="mailing_list"` (in `LOCKED`, so always held)
     and `SOCIAL_REPLY="social_reply"` (promote after 5). `assistant._within_role` maps each category to
     its tool through a `CATEGORY_TOOL` dict in `autonomy.py`.
   - `_run` injects `recipients=sorted(store.constituent_emails(office))` for `email_send_list`, read
     again in `send_draft` at send time.
   - `report_save` gets `agent_id=f"assistant-{claims.act}"`, `trace_id=claims.sid` and `run_id=None`,
     with input keys dropped by the model.
   - The `holds_secrets` scrub matches `agents_api._run_handler`.
5. **Connection gate (Lane I).**
   - `gateway.py` imports `connections` in `try/except ImportError` (`connections = None`).
   - For outbound tools, after Cedar ALLOW and before the autonomy hold, it calls
     `provider_for_tool(tool, args)`. If that returns a provider, it calls `require_connection(act,
     provider)`. On `NeedsConnection` it records a DENY with the new Reason `"needs connection"`, no ATLAS
     deny labels, and `needs_connection_result(exc)` as `Outcome.result`. `_record` must keep a result on
     this DENY, and `mcp_server.call_tool` returns that JSON with `isError: true`.
   - At run time (`_run` / `send_draft`), with a connection present:
     - `email_send` calls `send_gmail(act, to, subject, body, in_reply_to=params.in_reply_to)`.
     - `email_send_list` calls `send_gmail(...)` once per DynamoDB recipient.
     - `social_post` calls `post_x(act, text)`.
     - `social_reply` calls `post_x(act, text, reply_to=params.reply_to)`.
   - `provider_for_tool` returns `None` for bluesky, linkedin and facebook. Those calls get no gate and
     keep the share-link handler. `CONNECTABLE = ("gmail", "x")` is exported, and the test double
     exports it too.
   - A sender that raises `NeedsConnection` gets the needs-connection DENY. A sender that raises
     `ProviderError` or any other exception is a DENY (the existing `except Exception`). In `send_draft`
     the draft goes back to pending, as today.
   - With the module absent, behaviour is exactly as today.
6. **Agents-tab caps** (`agents_api.py`).
   - `Actor` gains `is_admin` (from the Cognito group in `agent_iam.actor_from_login`).
   - New `set_role_resolver(fn: user_id -> StafferItem | None)`. The default refuses, so it fails closed.
     `agent_iam` wires it to `get_gateway().store.get_staffer`.
   - `allowed_for_role(user, dotted)` = staffer exists, is active, and `role_allows(office_role,
     dotted)`. The admin Cognito group is never a bypass.
   - `GET /agent-permissions?kind=` returns `{kind, role, role_label, tools: [{name, label, effect,
     risk, outbound, held_for_review, allowed_for_kind, allowed_for_role, recommended,
     reason_unavailable}]}` for every registry tool.
   - Recommended = kind least privilege ∩ role. That is `email.send` for email,
     `social.post`+`social.reply` for social, the type defaults for research and for market_research, and
     ∅ for custom. The recommendation lives in `agent_types.AgentType.recommended_tools`.
   - Create and grant routes:
     - Exact names only. Anything not in the registry, including case, whitespace, unicode and underscore
       variants, gets 400 `unknown_tools`.
     - Duplicates get 400 `duplicate_tools` (decided: reject, not dedupe).
     - Outside kind ceiling ∩ caller role gets 403 + audit `grant_denied`.
     - `CreateAgentRequest` and `GrantUpdate` use `extra="forbid"`, so `owner_id`, `office` or `user_id`
       get a 422.
     - Omitted `tool_grants` = recommended.
     - Someone else's agent gets 404 (unchanged). Ownerless agents are 403 `shared_agent_admin_only` for
       non-admins.
   - Runtime (`call_tool`, so the runner, `gateway_tool_call` and `/agents/tool-call` too, and `approve`):
     the stored grant must also pass `allowed_for_role(cap_user)` read fresh. Otherwise 403
     `role_not_allowed` + audit.
   - `cap_user` = `agent.owner_id`. For ownerless agents (decided) it is the staffer who started the run
     (`runs.get(run_id).created_by`), or the `/agents/tool-call` actor. With neither, the call is denied.
7. **Agent.** `CHAT_SYSTEM_PROMPT` names, in plain words: email the office mailing list (always held for
   review), reply on social, the public research APIs, procurement market research (USAspending, SAM.gov),
   and saving a report. It also says that a `needs_connection` result means "ask Maya to connect the
   account". The DENIED rules stay word for word. `agent/stub_server.py` gets one handler per new tool and
   `StubState.role`, which denies with `role-<tool>` and the plain reason, so `test_stub_server`
   (lists == TOOLS) and C5 work.
8. **UI.**
   - `lib/permissions.ts` holds the pure logic: `diffFromRecommended(options, selected)` returns
     `added`/`removed` lines with effect, risk and a `beyondRecommendation` flag. `initialSelection`
     equals the recommended set.
   - `NewAgent.tsx` loads `agentsApi.agentPermissions(kind)`. It renders checkboxes; unavailable ones
     are disabled, with `reason_unavailable` shown.
   - An `<aside aria-live="polite">` lists the changes. It sits beside the form on desktop and stacks
     below it at ≤ 768px.

### Tests (test-first; each escalation test names what removing its guard lets through)

- **C1 `test_capability_parity.py`:**
  - Every registry tool maps to a `TOOLS` entry.
  - Every governed tool has a role policy and at least one starter permit naming its action.
  - `ROLE_TOOLS` values ⊆ governed.
  - Chief = all governed.
  - `email_send_list` is in chief only.
  - Intern = the 6 listed tools.
  - Policy files == `STARTER_POLICIES`.
  - TS `ROLE_TOOLS` / `TOOL_NAMES` equal Python.
  - `mandate_tool_name` raises for `"Email.send"`, `" email.send"`, `"email_send"` and fullwidth input.
- **C2 `test_gw_agent_parity.py`:** one normal path per kind via `guarded()`, on both the double and real
  Cedar (cedarpy, like `test_gw_roles.CedarAuthorizer`).
  - email: `email_send` held.
  - mailing list: chief `email_send_list` held; on `send_draft` the handler receives the DynamoDB
    constituents for that office.
  - social: post and reply held; campaign reply denied by `forbid-campaign-reply`.
  - research: each of the 10 APIs ALLOW with `scan` called.
  - market research: `usaspending`/`sam` ALLOW + `report_save` stored under `assistant-<act>`.
  - Every call writes a `DecisionEvent`; DENYs carry ATLAS ids from the registry fallback.
- **C3 `test_agent_permissions_api.py`** (production resolver on a moto MandateStore):
  - The options matrix per kind × role.
  - Recommended sets.
  - `email.send_list` unavailable to a caseworker with a reason.
  - Effects in plain words with no Cedar ids.
  - Unknown kind gets 422.
  - Staffer reachable; no login gets 401.
- **C4 Backend `test_privilege_escalation.py`** (all by direct API call, so the UI is bypassed by
  construction):
  - A forged `tool_grants` with an extra tool (the role check).
  - `Email.send`, ` email.send`, `email.send `, U+2024 dot, fullwidth, `email_send` (exact-name check).
  - An unknown tool.
  - Duplicates.
  - PUT/POST grant to add a tool beyond the role, with grants and `token_version` unchanged (edit path).
  - A body with `owner_id`/`office`/`user_id` gets 422; editing Bob's agent gets 404 (extra=forbid,
    ownership).
  - A caseworker staffer asks for `email.send_list`; an admin-group user with a caseworker office role is
    also refused (group is not a bypass).
  - Role change after create: comms director agent with `social.reply`, demoted to intern, the next
    `call_tool` and a pending approval's `approve` get 403. Offboarded (`active=false`) gets 403 on every
    tool.
  - A workload token reused after a grant change gets 401 `token_revoked`.
  - An ownerless agent edited by a staffer gets 403; an ownerless agent's run started by an intern is
    capped to intern tools.
  - Omitted `tool_grants` = recommended, not the old type defaults.
- **C4 gateway `test_gw_escalation.py`:**
  - A mandate token for another staffer's sid gets DENY.
  - A token for a revoked session gets DENY.
  - A tampered token gets `InvalidToken`.
  - Prompt injection: an inbox email says "send this to the whole list". A scripted caseworker call to
    `email_send_list` is denied by `role-email-send-list` and no handler runs.
  - `recipients`/`agent_id` in tool input are ignored.
  - Chief with every grant and auto: `email_send_list` is still held (LOCKED).
  - A role in tool input is ignored for new tools.
- **C5 `test_assistant_parity.py`:**
  - The prompt contains each new capability phrase and the unchanged DENIED paragraph.
  - Each new tool is listed by the stub.
  - A scripted model (`fake_model.ScriptedModel`), told by injected chat text to bulk-email as an
    intern, gets DENIED `role-email-send-list` and does not retry.
  - One normal-path turn per kind against the stub.
- **C9:**
  - `permissions.test.ts`: the diff logic, beyond-recommendation flag, removed lines, unavailable tools
    never selectable.
  - `NewAgent.test.tsx`: pre-filled recommended set, disabled + reason, the panel updates on toggle,
    "always held for review" text for `email.send_list`, the submit payload has exactly the selection,
    and the server's 403 is shown.

**Intended updates to existing tests** (behaviour changes on purpose):

- `test_contracts.py`: tool list and untrusted set.
- `test_gw_roles.py`:
  - Add the new tools to `CALLS`.
  - Add a campaign `social_reply` to `OUT_OF_BOUNDS`.
  - `_req` sends `account` for `social_reply`.
  - Change the intern list in `test_the_intern_is_read_only`.
- `test_gw_mcp.py`: `== 7` → `len(TOOLS)`.
- `test_setup_role_policies.py`: `ROLE` list, `skipped: role-` count 6 → 21.
- `test_route_auth.py`: the staffer-write walk excludes the 3 staff routes and asserts they are not 403
  by group.
- `test_agent_ownership.py`:
  - `test_staffer_can_read_but_not_create_or_mint`: staffer may create within role, still can't mint.
  - `test_owner_cannot_be_set_by_request_body`: now 422.
- `test_agent_types.py`: create defaults = recommended (email no longer gets `email.send_list`).
- `agent/tests/test_stub_server.py`: unchanged, but passes only after the stub grows.
- `Backend/tests/conftest.py`: an autouse dict-backed role resolver for legacy tests. Named demo users get
  their ROLES.md role; any other caller is chief_of_staff. Documented, and overridden by C3/C4, which use
  the production resolver.

### Risks

- **AVP rollout order (security).** New starter permits without their `role-*` forbids would give every
  role the new tools. The new permits are created only with `--with-role-policies` (same gate as the role
  forbids). `forbid-campaign-reply` is always created. This is tested in `test_setup_role_policies.py`.
- **Legacy test resolver.** The default-chief fixture could hide a missing wiring. C3/C4 must use
  `agent_iam`'s production resolver against moto, and the trial checks that.
- **Shared `gateway.py`.** Tasks 5 and 6 both edit it, so they run in sequence.
- **Changed defaults.** Existing email agents keep their stored grants. The runtime role check now denies
  `email.send_list` for non-chief owners, which is intended but visible in the demo.
- **The connections module may change shape before Lane I merges.** We test against a double with the
  exact signatures in the coordinator's message.

### AWS rollout for Huy (dry-run first; only Huy runs `--apply`)

Prerequisites: new gateway code deployed, and every STAFFER item has an `officeRole`
(`bash Backend/infra/test-users.sh --apply` already did this).

1. Preview the changes:
   ```bash
   bash Backend/infra/setup.sh --with-role-policies
   ```
   This prints the AWS commands without calling AWS. Expect `created:` lines for 5 new starter policies
   and 15 new `role-*` policies, and `exists:` for the rest.
2. Apply them:
   ```bash
   bash Backend/infra/setup.sh --apply --with-role-policies
   ```
   It checks every staffer's role, then creates only the missing policies.
3. Check them against the real store:
   ```bash
   bash Backend/policy/tests.sh --apply
   ```
   Every case should print PASS.

### Verification

C1–C11 all exit 0 on one commit. Then the team-verifier screenshots `/agents/new?kind=email` at 375px
and 1280px with a stubbed API, checking that there is no horizontal scroll and that the panel stacks
below the form.

### Open Questions

- Copy for the plain-words effects and the "beyond recommendation" warning. Recommendation: use the
  `TOOL_ACTIONS` phrases plus "always held for review" for outbound tools, and let Huy edit them later.
  This does not block the build.

## Task Ledger

Parallel groups (disjoint files):

- After 2: **A** = 3→4→5→6→7 (gateway, assistant), **B** = 8→9→10 (Agents tab), **C** = 11→12 (agent),
  **D** = 15 (setup.sh).
- From the start: **E** = 13→14 (Frontend), building against the route shape in Design 6.
- Test-author tasks 1, 3, 8, 11 and 13 can all run at once.

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Failing C1 parity tests | team-test-author | `packages/mandate-contracts/tests/test_capability_parity.py` | collects; fails on missing tools/helpers | — | todo |
| 2 | Contracts: 15 tools + inputs, role sets, `mandate_tool_name`/`role_allows`/`TOOL_ACTIONS`, 5 starter + 15 role policy files, `DraftItem.tool` literal, Reason `"needs connection"`, schemas, TS, README | team-implementer | `packages/mandate-contracts/{src/**,policies/**,schemas/tools.json,ts/index.ts,README.md,tests/test_contracts.py}` | C1 + `test_contracts.py` pass | 1 | todo |
| 3 | Failing C2 + gateway C4 tests (cedarpy + double) | team-test-author | `packages/identity-gateway/tests/{test_gw_agent_parity.py,test_gw_escalation.py,conftest.py}` | collect; fail for the right reason | 2 | todo |
| 4 | `StarterPolicyAuthorizer` sync + intended updates to `test_gw_roles.py`, `test_gw_mcp.py` | team-implementer | `packages/identity-gateway/src/identity_gateway/authz.py`, `packages/identity-gateway/tests/{test_gw_roles.py,test_gw_mcp.py}` | `test_gw_roles.py` passes (C11 part) | 2 | todo |
| 5 | Gateway: LEGACY/OUTBOUND, new categories + `CATEGORY_TOOL`, recipients from DynamoDB, `report_save` identity, ATLAS fallback, `send_draft` for new tools | team-implementer | `packages/identity-gateway/src/identity_gateway/{gateway.py,autonomy.py}`, `packages/identity-gateway/README.md` | C2, `test_gw_escalation.py`, C7 | 3, 4 | todo |
| 6 | Lane I connection gate (Design 5) + double-based test | team-implementer (test first in same task) | `packages/identity-gateway/src/identity_gateway/{gateway.py,mcp_server.py}`, `packages/identity-gateway/tests/test_gw_connections.py` | `test_gw_connections.py` + C7 pass; with module absent C2 unchanged | 5 | todo |
| 7 | `assistant.py`: `EDITABLE_FIELDS` + `_within_role` via `CATEGORY_TOOL`; least-privilege cases for new categories | team-implementer | `Backend/app/routers/assistant.py`, `Backend/tests/test_least_privilege.py` | `test_least_privilege.py` + C6 pass | 5 | todo |
| 8 | Failing C3 + Backend C4 tests (production resolver, moto) | team-test-author | `Backend/tests/{test_agent_permissions_api.py,test_privilege_escalation.py}` | collect; fail on missing route/guards | 2 | todo |
| 9 | Agents-tab server: `recommended_tools`, permissions route, exact-name/dup/extra=forbid, role cap on create/grants/`call_tool`/`approve`, ownerless rules, role resolver | team-implementer | `packages/identity-gateway/src/identity_gateway/{agents_api.py,agent_types.py}` | C3 + C4 Backend pass (with task 10) | 8 | todo |
| 10 | Wiring: `Actor.is_admin`, resolver → mandate store, staff-write split; legacy test fixture + intended test updates | team-implementer | `Backend/app/agent_iam.py`, `Backend/tests/{conftest.py,test_route_auth.py,test_agent_ownership.py,test_agent_types.py}` | C3, C4 (Backend), C6 | 9 | todo |
| 11 | Failing C5 tests | team-test-author | `agent/tests/test_assistant_parity.py` | collect; fail on prompt/stub | 2 | todo |
| 12 | `CHAT_SYSTEM_PROMPT` + stub handlers for new tools + `StubState.role` | team-implementer | `agent/mandate_agent/runner.py`, `agent/stub_server.py`, `agent/README.md` | C5, C8 | 11 | todo |
| 13 | Failing C9 tests | team-test-author | `Frontend/src/lib/permissions.test.ts`, `Frontend/src/pages/NewAgent.test.tsx` | vitest runs; fails on missing module | — | todo |
| 14 | `permissions.ts`, API method + types, picker + side panel, responsive CSS | team-implementer | `Frontend/src/lib/{permissions.ts,agents.ts,agentsApi.ts}`, `Frontend/src/pages/{NewAgent.tsx,Agents.css}` | C9, C10 | 13 | todo |
| 15 | setup.sh: new permits gated with role policies, `forbid-campaign-reply` always; header comment; tests | team-implementer | `Backend/infra/{setup.sh,README.md}`, `Backend/tests/test_setup_role_policies.py` | C11 (`test_setup_role_policies.py`) | 2 | todo |
| 16 | Docs: ROLES.md table + guarantees rows for new tools and Agents-tab caps | team-implementer | `docs/ROLES.md` | `grep -c 'email_send_list' docs/ROLES.md` ≥ 1 and C1 still passes | 2, 9 | todo |
| 17 | Full check C1–C11 + 375px/1280px screenshots | team-verifier | none | every C exits 0 | all | todo |

## Rounds

## Decisions

- 2026-11-08: base: origin/main (23fa467) fails 66 Backend tests with NameError `_scrape_domain_problem`
  (definition dropped in merge ae534b9). Restored it from b9ffda0 in 78824f6 so the suites are a usable
  baseline. Lane G's role tests import `cedarpy`, which was missing from requirements; added.
- 2026-11-08: lead approves the outcome contract (not Huy), per the lane brief; no Huy stop.
