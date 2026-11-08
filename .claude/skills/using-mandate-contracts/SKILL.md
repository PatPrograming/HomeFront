---
name: using-mandate-contracts
description: Explains the shared HomeFront contracts in packages/mandate-contracts (MCP tool names and inputs, mandate token claims, decision events, DynamoDB items and keys, Cedar context, entities and policies) and the rules for building against them. Use before writing or changing gateway, broker, agent, guardrail, dashboard or AWS setup code in HomeFront, whenever code names a tool, reads or writes a token, event or DynamoDB item, or calls Verified Permissions.
---

# Using the HomeFront contracts

`packages/mandate-contracts` is the single agreement between the gateway, the broker, the agent and the
dashboard. Import from it; never retype a tool name, claim, event field, DynamoDB key or Cedar context key
by hand.

## Where each contract lives

| Need | Import / file |
|---|---|
| Tool names, input models, untrusted/always-denied flags | `mandate_contracts.TOOLS`, `mandate_contracts.tools` |
| MCP `inputSchema` for each tool | `tool_schemas()` or `schemas/tools.json` |
| Mandate token claims, issuer, scope, 15-min TTL | `MandateClaims`, `ISSUER`, `SCOPE`, `TTL_SECONDS` |
| Dev token before the broker exists | `python -m mandate_contracts.dev` (needs `MANDATE_DEV_SECRET`) |
| ALLOW/DENY event for the audit feed | `DecisionEvent`, `Reason`, `GuardrailFlag` |
| DynamoDB keys and item shapes | `mandate_contracts.ddb`: `session_key`, `SessionItem`, `StafferItem`, `CaseItem`, `EmailItem`, `event_key` |
| Verified Permissions request parts | `mandate_contracts.cedar`: `build_context`, `action`, `agent_entity`, `case_entity`, `tool_entity` |
| Starter Cedar policies, by readable id | `STARTER_POLICIES` or `policies/<id>.cedar` |
| Frontend types | `packages/mandate-contracts/ts/index.ts` |

Python import path: add `packages/mandate-contracts/src` to `PYTHONPATH` (`scripts/run-gateway.sh` does).

## Rules

1. **Change the contract only with Huy's approval.** If your work package needs a new field or tool, stop
   and ask; don't edit `packages/mandate-contracts` from a work-package branch.
2. **Build every IsAuthorized context with `build_context()`.** It always sends `delegatorActive`,
   `recipientKnown` and `account`. A missing key makes Cedar skip the policy that reads it, and a skipped
   `forbid` fails open.
3. **Office comes from the token and DynamoDB, never from tool input.** Input models drop unknown keys such
   as `office`; pass the CASE item's `office` to `case_entity()`.
4. **Schemas check shape, not permission.** Don't reject `social_post` to `campaign` or `email_send` to an
   unknown address in validation; let Cedar deny it so the deny shows in the feed.
5. **Fail closed.** Any error verifying the token, reading DynamoDB or calling Verified Permissions becomes
   a DENY event, never an allow.
6. **`policyIds` in events use the readable ids** from `STARTER_POLICIES`, not AVP's generated ids. Map AVP
   ids back to these names.

## After changing the contract (approved changes only)

```bash
.venv/bin/python packages/mandate-contracts/export_schemas.py
PYTHONPATH=Backend:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src \
  .venv/bin/python -m pytest -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests
```

Update `ts/index.ts` by hand to match; the tests fail if tool names, event fields, claims or reasons drift.
