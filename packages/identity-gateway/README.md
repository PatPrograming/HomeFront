# identity-gateway

The gateway is HomeFront's enforcement point. Every tool the AI agent uses sits behind it, and
every call goes through one function, `guarded()` in `src/identity_gateway/gateway.py`.

## The request path

```
agent ──MCP over HTTP, Authorization: Bearer <mandate token>──► /mcp
                                                                 │
   0. no token, forged token, expired token ─────────────────► HTTP 401
                                                                 │
   guarded(tool, args, token)
   1. verify the mandate token (HS256, issuer, expiry, claims)
   2. DynamoDB, fresh read: session active AND staffer active?  ─ no ─► DENY "session revoked / delegator inactive"
   3. build the facts from the token and DynamoDB, never from tool input
   4. Verified Permissions IsAuthorized (Cedar)                 ─ no ─► DENY "denied by policy"
   5. run the tool
   6. inbox_read / web_scrape output → scan() (flag only, text passed on unchanged)
   7. write a decision event to DynamoDB
                                                                 │
   ALLOW → tool result            DENY → MCP isError "DENIED (<reason>, ATLAS <id>)"
```

Any error in steps 2 to 7 (DynamoDB down, AVP down, a bug, a tool crash) is a DENY. If the
decision event cannot be written, the tool's result is withheld and the caller gets a DENY. The
tool has already run by then; `email_send` and `social_post` are simulated today, but a real
sender would need a "pending" event written before it acts.

A session lasts one hour; within it the agent can get fresh 15-minute mandate tokens. Offboarding
ends both at once.

## Why each step is there

| Step | Question it answers | What a judge might try | What happens |
|---|---|---|---|
| 1 | Is this token ours and still valid? | Tamper with the token, use `alg: none`, replay an expired one | 401 |
| 2 | Is the staffer this agent acts for still here? | Keep using a token after Maya is offboarded | DENY on the very next call (no cache, ConsistentRead) |
| 3 | Which office owns this case? Is this recipient on file? | Put `"office": "rep-y"` in the tool input | Ignored: input models drop unknown keys; the office comes from the CASE item |
| 4 | Do the Cedar policies allow it? | Bulk export, email an outside address, post to the campaign account | DENY with the policy name, e.g. `forbid-bulk-export` |
| 6 | Is the email or web page trying to instruct the agent? | Poisoned email | Flagged in the event; Cedar still decides what the agent can do |
| 7 | Who did what, for whom? | Restart the server | Events and revocations are in DynamoDB, so they survive |

## The facts Cedar sees

Built in step 3 with `mandate_contracts.cedar`:

- Principal `Mandate::Agent` with `office` and `mode` from the SESSION item.
- Resource `Mandate::Case` with `office` from the CASE item (for `case_read`), else `Mandate::Tool`.
- Context, always all three keys (a missing key would make Cedar skip a `forbid`, which fails open):
  `delegatorActive`, `recipientKnown` (the address belongs to a constituent of one of this
  office's cases, or is one `*.gov` address), `account` (for `social_post`).

## Files

| File | What it does |
|---|---|
| `gateway.py` | `guarded()`, the decision event, ATLAS labels, which tool handler runs |
| `tokens.py` | Mint and verify mandate tokens (`MANDATE_SIGNING_SECRET`, at least 32 characters) |
| `storage.py` | The single DynamoDB table: sessions, staffers, cases, emails, events |
| `authz.py` | `AvpAuthorizer` (Verified Permissions) and `StarterPolicyAuthorizer` (the same six policies in Python, for tests) |
| `mcp_server.py` | The MCP endpoint: lists the 7 contract tools, sends each call to `guarded()` |
| `app.py` | `/api/v1/tools`, `/api/v1/demo/matrix`, `/api/v1/mcp/call` (dashboard buttons, same `guarded()`) |
| `auth_stub.py` | Stand-in for lane A's Cognito checks; refuses everything until `Backend/app/core/cognito.py` exists |

### The teammates' agent tabs (a second, separate path)

`agents_api.py` is main's old gateway API, restored for the Agents, Monitoring, Approvals and Activity tabs:
agents with per-tool grants, a mailing list and an SES sender (`store.py`, `email_admin.py`, `agent_types.py`),
runs of Claude on Bedrock (`runner.py`), a human approval queue (`approvals.py`) and an activity log
(`audit.py`), in memory or in the `homefront-*` tables (`dynamo_repo.py`, `AGENT_STORE=dynamodb`).
It never mints or accepts a mandate token and never touches the `mandate` table.

`Backend/app/agent_iam.py` mounts it behind our Cognito login: GET routes need `require_staff`, every
other method (create, edit, grant, `/auth/token`, SES admin, test email, approve, start a run) needs
`require_admin`. An anonymous caller gets 401 everywhere, and `Backend/tests/test_route_auth.py` walks
every route to prove it. Its workload-token tool call is `POST /api/v1/agents/tool-call` (admin login in
`Authorization`, workload token in `X-Agent-Token`), and every call re-checks the agent's grants.

The broker and admin routes are in `Backend/app/routers/{sessions,staff,events}.py`.

## Run it

```bash
bash Backend/infra/setup.sh --apply    # once: DynamoDB table, AVP store, policies, writes Backend/infra/.env.aws
set -a; source Backend/infra/.env.aws; set +a
.venv/bin/python Backend/infra/seed.py --apply # staffers, cases, emails
MANDATE_SIGNING_SECRET=<32+ random chars> ./scripts/run-gateway.sh   # 127.0.0.1:8080
```

Environment: `MANDATE_SIGNING_SECRET`, `AWS_REGION` (us-east-1), `MANDATE_TABLE` (mandate),
`AVP_POLICY_STORE_ID`, `AVP_POLICY_NAMES` (from `.env.aws`), optional `MANDATE_AGENT_ID`
(agent-1) and `MANDATE_MCP_ALLOWED_HOSTS` (127.0.0.1:\*,localhost:\*). Without them the gateway
refuses every call.

## Test it

```bash
PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src \
  .venv/bin/python -m pytest -q packages/identity-gateway/tests Backend/tests/test_gateway_api.py
```

Tests use moto for DynamoDB and `StarterPolicyAuthorizer` for Cedar; they never call AWS. The
real policies are checked against AWS by `Backend/policy/tests.sh`.
