# mandate-contracts

Work package WP0, **Shared contracts**.

The shapes every part of Mandate agrees on: tool names and inputs, the mandate token, the decision
event, the DynamoDB items and keys, and the Cedar request shape and policies. The gateway, the agent and the frontend build against these, so
changing a file here needs the manager's (Huy's) approval.

| File | What it defines | Brief |
|---|---|---|
| `src/mandate_contracts/tools.py` | The 7 MCP tools, their input models, which outputs are untrusted | §6.1 |
| `src/mandate_contracts/token.py` | Mandate token claims (`sub` agent, `act` staffer, `office`, `mode`, `sid`), 15-min lifetime | §6.2 |
| `src/mandate_contracts/events.py` | The ALLOW/DENY decision event the dashboard shows | §6.3 |
| `src/mandate_contracts/ddb.py` | `pk`/`sk` keys and item shapes for the single `mandate` table | §6.4 |
| `src/mandate_contracts/cedar.py` | Cedar context keys with safe defaults, entity and action builders, starter policies by readable id | §6.5 |
| `src/mandate_contracts/dev.py` | Dev-only HS256 token until the broker exists | §6.2 |
| `ts/index.ts` | The same types for the frontend | |
| `policies/*.cedar` | Generated: one file per starter policy, for AVP setup | |
| `schemas/tools.json` | Generated JSON schemas for the tools (MCP `inputSchema`) | |
| `samples/` | Example events and dev token claims for mocks | |

Agents and teammates: the project skill `.claude/skills/using-mandate-contracts` explains how to build
against this package.

## Two rules worth knowing

- **Schemas check shape, not permission.** `social_post.account` accepts any string, including
  `"campaign"`. Cedar in the gateway decides whether the call is allowed, and the demo needs the
  agent's bad call to reach the gateway so the deny shows up in the feed.
- **The agent can't choose its office.** Unknown input keys such as `office` are dropped. The gateway
  takes the office from the token and DynamoDB only.

## Use it

```bash
# Python: add packages/mandate-contracts/src to PYTHONPATH (run-gateway.sh does this), then
python -c "from mandate_contracts import TOOLS; print(list(TOOLS))"

# Dev token for the agent or curl (secret from your env, never committed)
MANDATE_DEV_SECRET=... python -m mandate_contracts.dev

# After changing tools.py or token.py, regenerate the JSON and run the tests
.venv/bin/python packages/mandate-contracts/export_schemas.py
.venv/bin/python -m pytest -q packages/mandate-contracts/tests
```
