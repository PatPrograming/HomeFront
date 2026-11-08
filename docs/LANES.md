# Mandate — lane handbook

Four lanes build WP1–WP7 in parallel, each in its own session, worktree and branch, each running `/converge`.
This file is what every lane must know. The full plan is `MANAGER_BRIEF.md` (v2) at
`/Users/tranquanghuy/Projects/aws-a10-hack-2026/MANAGER_BRIEF.md`; read §1–§6, your lane's WPs in §9, and §10–§13.
Planning-folder audits worth reading: `/Users/tranquanghuy/Projects/aws-a10-hack-2026/docs/STATUS.md`
(repo audit, security findings), `docs/AUTH.md`, `docs/AGENT_CHECK.md` in the same folder.

Lead: Huy's manager session. It tracks `docs/TASKS.md`, reviews each lane's result and proposes merges. **Humans merge.**

## The event, in one paragraph

AWS x A10 Hack 2026, track A10 Prompt 1: "Build an identity and access management solution for AI agents using
enterprise authentication, workload identity, and secure authorization." Judges are A10 security engineers. The live
demo must do, with real enforcement: (1) authenticate an agent, (2) allow selected MCP tools, (3) revoke access
mid-task, (4) block subsequent requests. Teams can be disqualified for over-dependence on AI: **every teammate must
be able to explain all code**, so write small, plain code with a plain-words `README.md` per folder. Region
**us-east-1** only. **Fail closed** everywhere.

## Timeline (PT, Sat Nov 7 → Sun Nov 8)

| Time | Milestone |
|---|---|
| 17:00 | Integration 1: agent → real MCP endpoint on the gateway (dev token) |
| 18:00 | Integration 2: real Cognito tokens + broker + Offboard |
| 20:00 | All 4 demo steps work end to end, live, with AVP + DynamoDB |
| 23:00 | Feature freeze; each owner walks the team through their code |

Size your converge plan to hit your lane's part of these. Cut scope before missing a milestone (brief §13.8:
token replay demo, KMS → HS256, styling go first).

## Lanes

| Lane | WPs | Owner | Branch | Writes only in |
|---|---|---|---|---|
| A · Cognito | WP1 | Huy | `Cognito-Huy` | `Backend/infra/cognito.sh`, `Backend/app/core/cognito.py`, `Backend/app/core/auth.py`, `Backend/app/core/config.py` (Cognito settings only), `Backend/tests/test_cognito*.py`, `Backend/infra/README.md` (Cognito section) |
| B · Gateway | WP2 + WP3 + WP7 `setup.sh` | gateway author (TBD), Huy until claimed | `Gateway-Huy` | `packages/identity-gateway/**`, `Backend/app/agent_iam.py`, `Backend/app/main.py`, `Backend/app/routers/{sessions,staff,events}.py`, `Backend/infra/{setup.sh,seed.py}`, `Backend/policy/**`, `Backend/tests/test_gateway*.py`, `Backend/requirements.txt`, `scripts/run-gateway.sh` |
| C · Agent + Guardrails | WP4 + WP5 | Huy | `Agent-Guardrails-Huy` | `agent/**`, `packages/guardrail-scanner/**`, `docs/GUARDRAIL_EVAL.md` |
| D · Console | WP6 | frontend teammate | `Console-Huy` | `Frontend/**` |

Nobody edits `packages/mandate-contracts` (WP0) without the lead's approval. If you need a contract change, stop and
say exactly what and why. Touching a file outside your row is a blocker: ask the lead.

## Interfaces between lanes (build against these; use a stub until the other lane merges)

### A → B: Cognito verification, in `Backend/app/core/cognito.py`

```python
@dataclass(frozen=True)
class StaffIdentity:  username: str; sub: str; email: str | None; groups: frozenset[str]
@dataclass(frozen=True)
class AgentClientIdentity:  client_id: str; scopes: frozenset[str]

def verify_staff_token(token: str) -> StaffIdentity        # raises HTTPException(401)
def verify_agent_client_token(token: str) -> AgentClientIdentity  # 401 unless client_id == COGNITO_AGENT_CLIENT_ID and "mandate/tools" in scopes
# FastAPI dependencies:
require_staff        -> StaffIdentity   # any user in group staffer or admin
require_admin        -> StaffIdentity   # group admin only (403 otherwise)
require_agent_client -> AgentClientIdentity
```

Env: `COGNITO_REGION=us-east-1`, `COGNITO_USER_POOL_ID`, `COGNITO_STAFF_CLIENT_ID`, `COGNITO_AGENT_CLIENT_ID`,
`COGNITO_DOMAIN`. Keys come from the pool's JWKS (PyJWT `PyJWKClient`); check `exp`, `iss`, `token_use`, client id.
Tests use a locally generated RSA key and a patched JWKS, never real AWS. Staffer id = Cognito **username**
(`maya`, `chiefofstaff`); that is the `act` claim and the `STAFFER#<id>` key. Users: Maya (group `staffer`, office
`rep-x`), ChiefOfStaff (group `admin`, office `rep-x`). Office lives in the STAFFER item, not in Cognito.

### B → C and D: HTTP API (FastAPI, prefix `/api/v1`, server on 127.0.0.1:8080)

| Method + path | Auth | Body → response |
|---|---|---|
| `POST /api/v1/sessions` | staff Cognito access token (`require_staff`) | `{}` → `{sid, agent, actingFor, office, mode, allowedTools, expiresAt}`; writes `SESSION#<sid>` active |
| `POST /api/v1/sessions/{sid}/mandate` | agent client_credentials token (`require_agent_client`) | `{}` → `{mandateToken, expiresAt}`; only for an active session whose staffer is active; mandate token = `MandateClaims`, HS256 with `MANDATE_SIGNING_SECRET`, 15 min |
| `POST /api/v1/staff/{id}/offboard` | `require_admin` | `{}` → `{staffer, active: false, revokedSessions: n}` |
| `GET /api/v1/events?since=<cursor>` | `require_staff` | → `{events: DecisionEvent[], next: <cursor>}`; cursor = last event's sort key |
| MCP (streamable HTTP) `http://127.0.0.1:8080/mcp` | `Authorization: Bearer <mandateToken>` | the 7 tools in `mandate_contracts.TOOLS` |

So the broker needs **both** a logged-in staffer (step 1) and the agent's own Cognito client credentials (step 2)
before any mandate token exists. The sid is shown to the staffer and passed to the agent (`agent.py --sid`).

MCP behaviour: bad/expired/missing mandate token → HTTP 401. Every tool call goes through `guarded()`:
verify token → DynamoDB session + staffer active (ConsistentRead) → Cedar `IsAuthorized` built with
`mandate_contracts.cedar` → run tool → `scan()` untrusted output → write `DecisionEvent`. DENY →
MCP result `isError: true`, text `DENIED (<reason>, ATLAS <id>)`. Any exception anywhere → DENY.

### C → B: Guardrails scan, in `packages/guardrail-scanner` (import `guardrail_scanner`)

```python
def scan(text: str) -> GuardrailFlag   # mandate_contracts.GuardrailFlag
```

Calls Bedrock `ApplyGuardrail` (`source="INPUT"`) with `GUARDRAIL_ID` / `GUARDRAIL_VERSION`. Flags and logs only;
the caller passes the **original** text on. Never attach the guardrail to the model call. It is a best-effort
detector: on error return `GuardrailFlag(flagged=False, categories=["SCAN_ERROR"])` (Cedar is the guarantee). B
calls it for tools where `TOOLS[name].untrusted_output` is true, through one injectable function that defaults to
a no-op until C merges.

### C → B: seed data

`agent/data/emails.json` (25 labelled emails, ids `email-1…`) and `agent/data/cases.json` (20 `rep-x`, 5 `rep-y`,
ids `case-1…`), shaped as `EmailItem` / `CaseItem` plus `id`. B's `Backend/infra/seed.py` loads them if present,
else a small built-in fixture. Constituent emails on file decide `recipientKnown` (plus any `*.gov` address).

### Agent (C) environment

`MANDATE_GATEWAY_URL` (default `http://127.0.0.1:8080/mcp`), `BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-5`,
`AWS_REGION=us-east-1`, `GUARDRAIL_ID`, `GUARDRAIL_VERSION`, Cognito agent client id/secret from env. Until B's
endpoint exists, `agent/stub_server.py` (FastMCP, same 7 tools, `http://127.0.0.1:8000/mcp`).

## Rules for every lane

1. Load the project skill `using-mandate-contracts` first. Import names from `mandate_contracts`; never retype them.
2. Python: venv from `/usr/local/bin/python3.12` (the default `python3` is a broken Intel Anaconda build).
   Run the full suite before each commit:
   `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests` (+ your lane's tests). Keep existing tests green; update them only when the behaviour change is intended.
3. **AWS:** converge checks never call AWS or Bedrock (they cost money, need expiring credentials, and break
   offline). Test with fakes (moto for DynamoDB, a fake authorizer for AVP, a stubbed Bedrock client). Scripts that
   create AWS resources must be idempotent and support `--dry-run`. **Before running anything that creates or
   changes AWS resources, stop and ask Huy in your session, showing the exact commands.** Never delete resources.
   Never create IAM access keys. Never print credentials. On `ExpiredToken`, stop and ask Huy to refresh.
4. Never commit secrets or `.env` files. Frontend never calls AWS directly.
5. Commit to your branch with Conventional Commits. **Do not push, open PRs or merge**; the lead proposes merges.
6. Leave a short plain-words `README.md` in each folder you create, so a teammate can explain it to judges.
7. Don't add features beyond your WPs in brief §9. Scope cuts first.
8. When a converge loop ends, report: what passes (with the check output), what is not checked by command and
   needs Huy (real AWS runs, live demo), and the exact files changed.
