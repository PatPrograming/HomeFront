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

## Test accounts (`test-users.sh`)

Self-signup is off: the pool has `AllowAdminCreateUserOnly=true`, so nobody can register on the login page. Every account comes from an admin running `test-users.sh`. Keep self-signup off.

The script creates the hackathon test accounts in the pool `us-east-1_MLfQpodxG` (override with `COGNITO_USER_POOL_ID`). Edit the `TEST_USERS` list at the top of the script to change them.

| Username | Group | Office | Office role (what the assistant may do, see `docs/ROLES.md`) |
|---|---|---|---|
| `admin`, `chiefofstaff` | `admin` | `rep-x` | `chief_of_staff`: every tool |
| `bronco`, `maya` | `staffer` | `rep-x` | `caseworker`: inbox, cases, research, email; no posting |
| `scuacm` | `staffer` | `rep-x` | `communications_director`: inbox, research, email, official posts; no case files |
| `a10` | `staffer` | `rep-x` | `legislative_aide`: inbox and research only |
| `aws` | `staffer` | `rep-x` | `intern`: public research only |

For each account it creates the Cognito user without sending email, sets a permanent password, adds the user to its group, and writes the `STAFFER#<username>` / `META` item (`active=true`, `office`, `officeRole`) to the `mandate` table. The gateway refuses sessions for a user without that item, and Cedar denies every tool to a staffer with no office role. The group decides who sees the admin pages; the office role decides which tools the assistant may use.

### Run it

Print the plan. This calls nothing and needs no credentials:

```bash
bash Backend/infra/test-users.sh
```

Ask Huy before the real run. It needs the `aws` CLI with admin credentials:

```bash
bash Backend/infra/test-users.sh --apply
```

Running it again creates nothing new and keeps existing passwords. Pass `--reset-passwords` with `--apply` to give every listed account a new password. A re-run also sets `active=true` again and rewrites each office role, like `seed.py`.

### Passwords

The script generates a 20-character password per account and never prints it. It writes `username<TAB>password` lines to `Backend/infra/test-users.local.txt`, which is git-ignored and mode 600. Only the usernames and the file path appear on screen.

Share each password through a password manager (a shared vault item or a one-time share link). Never paste passwords into chat, email or a commit. After sharing, delete the file or keep it only on the admin's machine.

### Demo passwords (proof of concept only)

`--demo-passwords` swaps the random password for an easy one made from the username, so the team can log in as each role during the demo: the name with a capital first letter plus `@2026`, for example `bronco` gets `Bronco@2026`. A name with no lowercase letter gets an `x` at the end (`a10` gets `A10@2026x`) so every password meets the pool policy: 8+ characters with upper, lower, digit and symbol. They still go only to `test-users.local.txt` and are never printed.

These passwords are guessable by anyone who reads this README. Use them only for the hackathon demo, and run `--apply --reset-passwords` (without `--demo-passwords`) afterwards. Existing accounts keep their passwords unless you also pass `--reset-passwords`.

## Gateway resources (`setup.sh`, `seed.py`)

`setup.sh` creates, in us-east-1, what the gateway needs. It reuses anything that already exists
and never deletes.

1. DynamoDB table `mandate` (`pk`/`sk` strings, on-demand).
2. A Verified Permissions policy store (description `mandate`, validation off).
3. The starter policies and `forbid-campaign-reply` from `packages/mandate-contracts/policies/*.cedar`; each policy's
   description is its readable name. The six office-role policies (`role-*.cedar`, see
   `docs/ROLES.md`) are added only with `--with-role-policies`, and only after a read of every
   STAFFER item finds a valid `officeRole` on each one. Run `test-users.sh --apply` first. The four
   new permits (`research-tools`, `email-send-list-office`, `social-reply-official`, `report-save`)
   wait for the same flag: without the role forbids, every role would get those tools.
   `--update-role-policies` rewrites a `role-*` policy already in the store when its text differs
   from the file (same policy id). It is needed once for the admin-editable role table.
4. `Backend/infra/.env.aws` (untracked): `AVP_POLICY_STORE_ID` and `AVP_POLICY_NAMES`, the map
   from AVP's generated policy ids to the readable names that decision events show.

`seed.py` loads the staffers (maya: staffer, rep-x, Caseworker; chiefofstaff: admin, rep-x, Chief of Staff) and the cases
and emails from `agent/data/*.json`, or a small built-in fixture. Running it again resets maya to
active after an Offboard.

```bash
bash Backend/infra/setup.sh                    # dry run: print the commands only
bash Backend/infra/setup.sh --apply
bash Backend/infra/setup.sh --apply --with-role-policies   # after test-users.sh --apply wrote every role
bash Backend/infra/setup.sh --update-role-policies         # dry run: which role policies would change
bash Backend/infra/setup.sh --apply --update-role-policies # after the new gateway code is running
set -a; source Backend/infra/.env.aws; set +a
.venv/bin/python Backend/infra/seed.py                  # dry run
.venv/bin/python Backend/infra/seed.py --apply
```

## Connected accounts (KMS)

`connections.sh` makes the encryption key for the Gmail and X tokens that staff connect on the Account page. The backend stores only encrypted tokens, never the plain ones. The script works in `us-east-1`, is safe to run twice, and never deletes anything.

It does three things:

1. Finds the key with alias `alias/homefront-connections`, or creates it (with an alias) if missing.
2. Turns on automatic yearly key rotation.
3. Prints the line for the backend env file, `CONNECTIONS_KMS_KEY_ID=alias/homefront-connections`, plus an example IAM policy.

The example policy lets the backend's identity call `kms:Encrypt` and `kms:Decrypt` on this one key, and only when the call carries a `staffer` and a `provider` encryption context. The script prints it but does not apply it, and it never creates IAM keys.

By default the script only prints the `aws` commands. It touches nothing and needs no AWS credentials. Only `--apply` changes AWS.

```bash
bash Backend/infra/connections.sh            # dry run
bash Backend/infra/connections.sh --apply    # needs the aws CLI with credentials
```

## People & roles (admin tab)

The admin "People & roles" tab (`Backend/app/routers/people.py`) lists the Cognito users with their group
and office role, assigns either one, and edits what each office role may do. The server needs these IAM
permissions on the user pool, in addition to what it already has on the `mandate` table:

- `cognito-idp:ListUsers`
- `cognito-idp:ListUsersInGroup`
- `cognito-idp:AdminAddUserToGroup`
- `cognito-idp:AdminRemoveUserFromGroup`

Without them the tab shows the AWS error and changes nothing. Role edits are stored as `ROLE#<role>` items in
`mandate`; admin changes are logged as `ADMINLOG#<day>` items. See `docs/ROLES.md` for how Cedar enforces them.
