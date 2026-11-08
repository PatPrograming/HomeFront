# Mandate — task board

Source of truth for the plan: `MANAGER_BRIEF.md` (v2), kept in Huy's planning folder outside this repo. Updated after every merge.
Last updated: Sat Nov 7, 2026, 16:10 PT.

Baseline: `main` at `91c9f0a`. 12 tests pass before WP0 (`Backend/tests` 9, `packages/atlas-enricher/tests` 3), run with a
python3.12 venv and `PYTHONPATH=Backend:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src`.

| WP | What | Owner | Status | Blocker | Next step |
|---|---|---|---|---|---|
| WP0 | **Shared contracts**: `packages/mandate-contracts` (tools, token, events, DynamoDB items, Cedar context/entities/policies, TS types) + skill `using-mandate-contracts` | Manager | PR #5 open from branch `Agent-Huy`; 25 tests pass; Cedar policies checked with cedarpy | awaiting review and merge | Gateway, agent and frontend import it in WP2–WP6 |
| WP1 | Cognito: user pool, Maya + ChiefOfStaff, `staff-web` PKCE client, `mandate-agent` client_credentials, backend token verify, admin group check | **Huy** | lane A session running `/converge` on branch `Cognito-Huy` | Huy approves outcome checks; real `cognito.sh` run | Lane A report |
| WP2 | Broker `POST /sessions`, Offboard, `GET /events?since=`, bind 127.0.0.1 | TBD (gateway author?) | lane B session running `/converge` on branch `Gateway-Huy` (with WP3 + WP7 setup) | owner still TBD; Huy approves checks and real `setup.sh` | Name a teammate to own and explain it |
| WP3 | Gateway → DynamoDB + Verified Permissions, real MCP endpoint, `guarded()`, fail closed, `backend/policy/tests.sh` | TBD (gateway author?) | in lane B (see WP2) | see WP2 | see WP2 |
| WP4 | Agent (Strands + Bedrock Sonnet 5, MCP client, stub server) | Huy | lane C session running `/converge` on branch `Agent-Guardrails-Huy` (with WP5) | Huy approves checks; real Bedrock runs | Lane C report |
| WP5 | Guardrails: guardrail + eval set + `scan()` in gateway | Huy | in lane C (see WP4) | see WP4 | see WP4 |
| WP6 | Staff + admin console (in `Frontend/`, replaces Supabase login) | Frontend | lane D session running `/converge` on branch `Console-Huy` | frontend teammate to review and own | Lane D report |
| WP7 | AWS provisioning (`setup.sh`) + Lambda deploy | TBD | `setup.sh` in lane B; Lambda deploy after integration | — | Deploy by 18:00 target (brief §9) |

## Notes
- Lanes run in parallel per `docs/LANES.md` (scope, files, interfaces, rules). Each lane commits to its own branch and never pushes; the lead proposes merges and humans merge.
- Every sub-agent loads the project skill `using-mandate-contracts` before touching gateway, broker, agent,
  guardrail, dashboard or AWS setup code.
- Branches use the team pattern `Area-Name` (e.g. `Agent-Huy`); commits follow Conventional Commits. Python attributes are snake_case; JSON on the wire is camelCase (`mandate_contracts.wire`).
- `docs/AUTH.md` (in the planning folder) describes Auth.js on Next.js. `Frontend/` is a Vite React SPA, so WP1
  follows the brief instead: `staff-web` public client with code + PKCE, backend verifies the Cognito token with
  PyJWT (already a dependency) against the Cognito JWKS.
