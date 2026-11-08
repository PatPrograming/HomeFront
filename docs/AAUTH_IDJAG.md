# AAuth + ID-JAG agent authentication (HomeFront)

HomeFront keeps **Cedar / Verified Permissions** as the authorization truth on the mandate/`/mcp` path. On the **Agents / Monitoring** pathway, AAuth binds each workload token to an Ed25519 agent key (proof of possession) while opaque `gr_*` tokens remain the mid-task revoke handle.

## Demo mapping (A10 Prompt 1) — Agents / Monitoring

| Demo step | How it works |
| --- | --- |
| Authenticate an agent | `POST /api/v1/auth/token` (staff Cognito) issues opaque `gr_*` **plus** `aa-agent+jwt` and PoP-bound `aa-auth+jwt` when AAuth is enabled |
| Allow selected MCP tools | Unchanged: per-agent tool grants + ATLAS; each call re-proves AAuth PoP server-side |
| Revoke mid-task / block next call | Unchanged: grant revoke bumps `token_version` → next tool call 401/403 |

## Mandate / Console path (still dual-accept)

| Demo step | How it works |
| --- | --- |
| Authenticate an agent | Cognito `client_credentials` + AAuth `aa-agent+jwt` |
| Delegation (acts for staff) | ID-JAG from `POST /api/v1/oauth/token`, then `aa-auth+jwt` with `cnf.jkt` |
| Allow MCP tools | Cedar `IsAuthorized` on mapped `MandateClaims` |
| Revoke | Session / staffer inactive → DENY on next `guarded()` |

## Protocols

Set `HOMEFRONT_AUTH_PROTOCOLS` (comma list):

- `legacy` — HS256 mandate from `POST /api/v1/sessions/{sid}/mandate` (mandate path)
- `idjag` — token-exchange → `oauth-id-jag+jwt`
- `aauth` — jwt-bearer / workload PoP → `aa-auth+jwt` + request signatures

Default: `legacy,aauth,idjag` (dual-accept).

Requires `AAUTH_SIGNING_KEY` or `MANDATE_SIGNING_SECRET` (≥32 chars). Without a secret, `/auth/token` falls back to `legacy_workload`.

## Agents / Monitoring endpoints

- `POST /api/v1/auth/token` — workload identity; response includes `auth_protocol`, `agent_jwt`, `aa_auth_jwt`, `jkt` when AAuth is on
- `POST /api/v1/agents/tool-call` — Monitoring tool call; audit events carry `auth_protocol`
- Bedrock runner — same `issue_token` + `gateway_tool_call` path (AAuth stamped on run authenticate + tool calls)

Private key material never leaves the gateway process; Monitoring does not need browser Ed25519.

## Mandate endpoints

- `GET /api/v1/.well-known/aauth-agent.json` — discovery
- `POST /api/v1/oauth/token` — ID-JAG exchange and aa-auth mint
- `POST /api/v1/sessions/{sid}/mandate` — legacy mandate (still available)
- `POST /mcp` — accepts legacy Bearer **or** Bearer `aa-auth+jwt` + `Signature-Key` / `Signature`

## Audit

- Agents/Monitoring: `auth_protocol` is `aauth` or `legacy_workload`
- Mandate decisions: `legacy_mandate` or `idjag+aauth`
