# Loop: mandate-gateway

Status: BUILDING
Profile: feature (trial depth: full, because this is authentication and authorization)
Round: 1 / 4
Base commit: 839c456
Branch: claude/gateway-huy-23ae16

## Original Request

"Turn the HomeFront gateway into Mandate's enforcement point (WP2 + WP3 + WP7 setup). Deliver: (1) a storage layer over the single DynamoDB table using `mandate_contracts.ddb` keys and items, ConsistentRead on every check, replacing `InMemoryStore` for sessions, staffers, cases, emails and decision events (events survive restart); (2) an authorizer calling Verified Permissions `IsAuthorized` with `mandate_contracts.cedar` (`build_context` always, entities from the token and DynamoDB, never from tool input), behind an interface with a fake for tests; (3) `guarded(tool, args, token)` as the single path for every tool call: verify mandate token (HS256 `MANDATE_SIGNING_SECRET`, issuer, exp, required claims) → 401 if bad → session active AND staffer active → build facts (`recipientKnown` from the constituent emails on file or `*.gov`; `account`; case office from the CASE item) → IsAuthorized → run the tool (casework tools `inbox_read`/`case_read` read DynamoDB; `datagov_search`, `web_scrape`, `email_send`, `social_post` reuse `mcp_tools` handlers) → call an injectable `scan(text) -> GuardrailFlag` (default no-op; lane C's `guardrail_scanner.scan` later) for tools with `untrusted_output` → write a `DecisionEvent` with reason, policy ids (readable names from `STARTER_POLICIES`) and ATLAS ids (keep the existing enricher). Any exception at any step → DENY event, never allow. (4) A real MCP endpoint (Python MCP SDK / FastMCP, streamable HTTP) at `/mcp` in the same FastAPI app exposing the 7 tools from `mandate_contracts.TOOLS` with their schemas, each handler calling `guarded()`; DENY returns MCP `isError` with text `DENIED (<reason>, ATLAS <id>)`. Keep `POST /api/v1/mcp/call` only as a thin dashboard path through `guarded()`. (5) The broker and admin API exactly as in `docs/LANES.md`: `POST /api/v1/sessions`, `POST /api/v1/sessions/{sid}/mandate`, `POST /api/v1/staff/{id}/offboard` (sets staffer inactive and revokes all its sessions), `GET /api/v1/events?since=`, using lane A's `require_staff` / `require_admin` / `require_agent_client` from `Backend/app/core/cognito.py` (until lane A merges, add a minimal stub module with the same names that tests override, and mark it clearly for replacement). Close the open token endpoint: no route mints an agent token without both credentials. Server binds 127.0.0.1. (6) `Backend/infra/setup.sh` (idempotent, `--dry-run`): DynamoDB table `mandate` (pk/sk strings), Verified Permissions policy store (validation off) with the six policies from `packages/mandate-contracts/policies/*.cedar`, writing the policy-id → readable-name map to an untracked env/JSON file; and `Backend/infra/seed.py` loading staffers (maya: staffer, rep-x; chiefofstaff: admin, rep-x) plus `agent/data/*.json` if present else a small fixture. (7) `Backend/policy/tests.sh` running AWS CLI `IsAuthorized` cases (own-office case allow; other-office deny; delegatorActive=false deny; bulk export deny; email to unknown recipient deny; campaign post deny), with `--dry-run`. (8) Tests for every row of brief §10 against fakes (moto for DynamoDB is fine), including offboard → next call DENY, old token after offboard DENY, AVP/DynamoDB error → DENY, agent-supplied `office` ignored, restart keeps revocations and events; keep the existing 12 tests green (update them where the in-memory behaviour intentionally changes)."

## Desired Outcome

Every agent tool call, over real MCP at `/mcp` or the dashboard REST path, goes through one
`guarded()` function that verifies the mandate token, checks DynamoDB session and staffer status,
asks Verified Permissions, runs the tool, scans untrusted output and records a durable decision
event, and any failure denies. Staff, agent and admin routes broker sessions, mint mandate tokens
only with both credentials, offboard staffers and serve the event feed. AWS setup, seed and policy
test scripts exist with `--dry-run`.

## Outcome Checks

`ENV` below stands for `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src`, written out in each command.

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Full suite from LANES.md plus gateway tests | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests packages/identity-gateway/tests` | exit 0 |
| C2 | Brief §10 edge cases, one test per row | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q packages/identity-gateway/tests/test_gw_edge_cases.py` | stdout matches /\b(1[0-9]\|[2-9][0-9]) passed/ |
| C3 | In-process MCP client lists 7 tools and gets DENY for records_bulk_export | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q packages/identity-gateway/tests/test_gw_mcp.py` | stdout matches /[1-9][0-9]* passed/ |
| C4 | Broker, admin and events API | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q Backend/tests/test_gateway_api.py` | stdout matches /[1-9][0-9]* passed/ |
| C5 | setup.sh parses and dry-runs both resources | `bash -n Backend/infra/setup.sh && env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null bash Backend/infra/setup.sh --dry-run 2>&1` | stdout contains create-policy-store |
| C6 | tests.sh parses and dry-runs six IsAuthorized cases | `bash -n Backend/policy/tests.sh && env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null bash Backend/policy/tests.sh --dry-run \| grep -c is-authorized` | stdout matches /^6\s*$/ |
| C7 | The open token endpoint and 0.0.0.0 binds are gone | `! grep -rnE '"/auth/token"\|0\.0\.0\.0' Backend/app packages/identity-gateway/src scripts/run-gateway.sh` | exit 0 |
| C8 | seed.py dry-run loads staffers | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python Backend/infra/seed.py --dry-run` | stdout contains STAFFER#chiefofstaff |

Every check that runs Python or an AWS script strips AWS credentials first (lead rule, 2026-11-07), so no check can reach real AWS.

## Not Checkable By Command

- Real AWS runs of `setup.sh`, `seed.py` and `tests.sh` (cost money, need Huy's expiring credentials): Huy, after the loop.
- Live demo with lane C's agent and lane D's console over real Cognito tokens: Huy and the lead at Integration 1 and 2.
- Whether the human owner can explain `guarded()`: Huy reads `packages/identity-gateway/README.md`.

## Plan

Interfaces (the tests are written against these):

- `identity_gateway/storage.py` — `MandateStore(table)`, `MandateStore.from_env()` (`MANDATE_TABLE`, `AWS_REGION`, optional `MANDATE_DDB_ENDPOINT_URL`), `create_table(dynamodb_resource, name="mandate")`. Methods: `get_session/put_session(sid, SessionItem)`, `get_staffer/put_staffer(id, StafferItem)`, `set_staffer_active(id, bool)`, `revoke_sessions_for(staffer_id) -> int`, `get_case/put_case`, `get_email/put_email`, `constituent_emails() -> set[str]` (lower-cased), `put_event(DecisionEvent)`, `events_since(since: str | None, limit=200) -> (list[DecisionEvent], next_cursor)`. Every read passes `ConsistentRead=True`.
- `identity_gateway/authz.py` — `AuthzRequest(agent_id, office, mode, tool, resource, context)`, `AuthzDecision(allowed, policy_ids)`, `Authorizer` protocol `is_authorized(req)`, `AvpAuthorizer(client, policy_store_id, policy_names)` (+ `from_env()`), `StarterPolicyAuthorizer` (test double that evaluates the six starter policies in Python).
- `identity_gateway/tokens.py` — `InvalidToken`, `mint_mandate_token(claims, secret)`, `verify_mandate_token(token, secret) -> MandateClaims`, `signing_secret()`.
- `identity_gateway/gateway.py` — `Gateway(store, authorizer, secret, scan=no_scan, enricher=None)`, `Gateway.guarded(tool, args, token) -> Outcome` (raises `InvalidToken` after recording a DENY `invalid token` event), `Outcome.allowed`, `Outcome.event`, `Outcome.result`, `Outcome.deny_text()`; `get_gateway()` / `set_gateway()`.
- `identity_gateway/mcp_server.py` — low-level MCP `Server` with the 7 contract tools; stateless streamable HTTP at `/mcp`; bearer check returns HTTP 401 before MCP runs.
- `identity_gateway/app.py` — `router` with `/tools`, `/demo/matrix`, `POST /mcp/call`; old in-memory routes and `store.py` removed.
- `Backend/app/routers/{sessions,staff,events}.py` — broker, offboard, events feed; auth dependencies from `app.core.cognito` with a fallback to `identity_gateway.auth_stub` (denies everything) until lane A merges.
- `Backend/infra/{setup.sh,seed.py}`, `Backend/policy/{tests.sh,request.py}`, READMEs.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Failing tests for the interfaces above and §10 | team-test-author | `packages/identity-gateway/tests/**`, `Backend/tests/test_gateway_api.py`, `Backend/tests/test_routes.py` | tests collect and fail on missing modules | — | in progress |
| 2 | storage, authz, tokens, gateway (`guarded`) | lead | `packages/identity-gateway/src/**` | C2 | — | in progress |
| 3 | MCP endpoint + REST path + app wiring | lead | `packages/identity-gateway/src/**`, `Backend/app/{agent_iam,main}.py` | C3 | 2 | pending |
| 4 | Broker/admin/events routes | lead | `Backend/app/routers/{sessions,staff,events}.py` | C4, C7 | 2 | pending |
| 5 | setup.sh, seed.py, tests.sh, READMEs, run-gateway.sh | lead | `Backend/infra/**`, `Backend/policy/**`, `scripts/run-gateway.sh`, `packages/identity-gateway/README.md` | C5, C6, C8 | 2 | pending |

## Rounds

## Decisions

- 2026-11-07 16:15 — The approval stop is skipped: the lead session's brief already fixed the deliverables and named the outcome checks, and Integration 1 is at 17:00. The only stop is the one the brief asks for: before running AWS scripts for real.
- The lead writes the implementation (tasks 2–5) while `team-test-author` writes the tests from the interface list, so the tests are still written by someone who did not write the code.
- `Reason` has no "error" value in the contract; AWS or internal errors deny with reason `denied by policy` and no policy ids. Reported to the lead as a possible contract addition.
