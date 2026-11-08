# infra: the teammates' alternative AWS stack

**The running app does not use this folder.** The deployed identity is the `mandate` setup:

- Cognito pool `us-east-1_MLfQpodxG` with the `staff-web` (PKCE) and `mandate-agent` (client credentials) app clients and the `staffer` / `admin` groups, made by `Backend/infra/cognito.sh`.
- The single `mandate` DynamoDB table and the Verified Permissions policy store, made by `Backend/infra/setup.sh`.

See `Backend/infra/README.md` for those.

## What is here

| File | What it does |
|---|---|
| `cloudformation/homefront.yaml` | A CloudFormation stack `homefront`: a second Cognito pool `homefront-users` with one SRP app client, the tables `homefront-profiles`, `homefront-usernames`, `homefront-agents`, and an IAM policy for them |
| `deploy.sh` | Deploys that stack and prints `.env` values for it |
| `../scripts/create_tables.py`, `../scripts/run-dynamodb-local.sh` | Create the same three tables in DynamoDB Local (Docker) |

`Backend/app/core/dynamodb.py` holds the table definitions, and `Backend/tests/test_infra.py` checks the template matches them.

## Why it is kept but not wired in

These came from `main` when it was merged into the console/gateway branch (2026-11-07). The merged backend reads
`COGNITO_STAFF_CLIENT_ID` / `COGNITO_AGENT_CLIENT_ID` and the `mandate` table, not `COGNITO_APP_CLIENT_ID` or the
`homefront-*` tables, so pointing `.env` at this stack would not sign anyone in. Running `deploy.sh` creates a second,
unused user pool: don't run it for the demo.
