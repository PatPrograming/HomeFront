# Backend infra

Scripts that build the AWS resources the backend depends on.

## Cognito

`cognito.sh` builds the HomeFront sign-in system in `us-east-1`. It is safe to run twice: each step looks the resource up first and creates it only if missing. It never deletes anything.

| Resource | Name | Purpose |
|---|---|---|
| User pool | `mandate` | Invite-only: no sign-up, MFA off, no self-service recovery |
| Login domain | `mandate-a10-hack-2026` (or `COGNITO_DOMAIN_PREFIX`) | Hosted login page and `/oauth2/token` |
| Resource server | `mandate` | Defines the scope `mandate/tools` |
| App client | `staff-web` | Browser login for staff |
| App client | `mandate-agent` | The agent's own identity |
| Groups | `staffer`, `admin` | Decide what a staff member may do |
| Users | `maya` (staffer), `chiefofstaff` (admin) | Demo staff |

### Run it

By default the script only prints what it would do. It touches nothing and needs no AWS credentials. Only `--apply` changes AWS.

```bash
bash Backend/infra/cognito.sh
```

Ask Huy before the real run. It needs the `aws` CLI with credentials, and two temporary passwords that meet Cognito's default policy (8+ characters with upper, lower, digit and symbol):

```bash
export MAYA_TEMP_PASSWORD='...' CHIEFOFSTAFF_TEMP_PASSWORD='...'
bash Backend/infra/cognito.sh --apply
```

Set `COGNITO_DOMAIN_PREFIX` to pick another login domain prefix. Pass `--env-file <path>` to write somewhere other than the repo-root `.env`.

### What it prints

The script prints only public values: `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_STAFF_CLIENT_ID`, `COGNITO_AGENT_CLIENT_ID` and `COGNITO_DOMAIN`. It also prints the `VITE_COGNITO_*` values for the frontend.

The agent client secret (`COGNITO_AGENT_CLIENT_SECRET`) goes only into the git-ignored `.env`. The script refuses to run if the env file is not git-ignored, and it never prints the secret.

### The two clients

`staff-web` is a public browser client with no secret. A human logs in on Cognito's managed login page with the authorization code flow, and the browser sends the resulting access token. Cognito does not force PKCE on a public client, so the frontend must send a PKCE code challenge itself (any standard OIDC library does).

`mandate-agent` is a confidential client with a secret. It allows only `client_credentials` (no user sign-in flows), so the agent proves its own workload identity with scope `mandate/tools`. No user is involved.

### What the backend checks

`Backend/app/core/cognito.py` verifies every token locally:

- The signature is RS256, checked against the pool's JWKS keys.
- `iss` is our pool, and `exp` has not passed.
- `token_use` is `access` (ID tokens are rejected).
- `client_id` matches the expected client, so an agent token cannot act as a staff login.
- Agent tokens carry the `mandate/tools` scope.

Every failure returns the same 401, so a caller learns nothing about why. A valid staff token from the wrong group returns 403.

### Revocation

The backend checks a Cognito access token locally, from its signature, without asking Cognito. So a token stays valid until `exp` (60 minutes by default) even if the user is disabled: Cognito's `RevokeToken` cancels a refresh token and the access tokens made from it, but only Cognito's own endpoints see that; a local signature check does not. `client_credentials` tokens have no refresh token to revoke at all.

Revocation is therefore lane B's job: a DynamoDB session and staffer status check (ConsistentRead) on every tool call. Offboarding flips that status, and the next call is denied.

### Fallback login from the CLI

`staff-web` allows `ADMIN_USER_PASSWORD_AUTH`, which needs AWS credentials. Use it only when the browser login is unavailable:

```bash
read -rs PW   # type the password; it stays out of shell history
aws cognito-idp admin-initiate-auth --region us-east-1 --user-pool-id "$COGNITO_USER_POOL_ID" \
  --client-id "$COGNITO_STAFF_CLIENT_ID" --auth-flow ADMIN_USER_PASSWORD_AUTH \
  --auth-parameters "{\"USERNAME\":\"maya\",\"PASSWORD\":\"$PW\"}"
```

### Get an agent token

Load `.env` first so the client id and secret stay out of your shell history:

```bash
set -a; source .env; set +a
curl -s -u "$COGNITO_AGENT_CLIENT_ID:$COGNITO_AGENT_CLIENT_SECRET" \
  -d grant_type=client_credentials -d scope=mandate/tools \
  "$COGNITO_DOMAIN/oauth2/token"
```
