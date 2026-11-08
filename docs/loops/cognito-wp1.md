# Loop: cognito-wp1

Status: STOPPED (lead)             <!-- PLANNING | AWAITING_APPROVAL | BUILDING | REVIEWING | DONE | STUCK | CAPPED -->
Profile: feature (trial depth: full, because this is auth)
Round: 3 / 4
Base commit: 839c456
Branch: claude/cognito-huy-5d029b

## Original Request

"Replace Supabase auth with Amazon Cognito for Mandate (WP1). Deliver: (1) `Backend/infra/cognito.sh`, idempotent with `--dry-run`, creating in us-east-1 one user pool, a managed-login domain, resource server `mandate` with scope `mandate/tools`, confidential app client `mandate-agent` (client_credentials only; built first), public app client `staff-web` (authorization code + PKCE, callback/logout `http://localhost:3000/` and `http://localhost:5173/`), groups `staffer` and `admin`, users `maya` (staffer) and `chiefofstaff` (admin) with temporary passwords read from env, printing the env values the backend and frontend need; no sign-up, MFA, forgot-password or federation. (2) `Backend/app/core/cognito.py` implementing exactly the `StaffIdentity` / `AgentClientIdentity` / `verify_staff_token` / `verify_agent_client_token` / `require_staff` / `require_admin` / `require_agent_client` interface in `docs/LANES.md`, using PyJWT and the pool JWKS, checking signature, `exp`, `iss`, `token_use`, client id and scope, failing closed (any error → 401; non-admin → 403). (3) `Backend/app/core/auth.py` switched to Cognito for user routes (`/api/v1/me` keeps working for a Cognito user) and Cognito settings in `config.py`. (4) Tests with a locally generated RSA key and patched JWKS proving: valid staff token accepted; tampered, expired, wrong-issuer, wrong-client and ID-token-instead-of-access-token rejected with 401; agent token without `mandate/tools` rejected; `require_admin` returns 403 for maya and passes chiefofstaff; no token → 401. (5) A plain-words `Backend/infra/README.md` Cognito section explaining the two clients and why Cognito tokens can't be revoked (revocation is the DynamoDB check in lane B)."

Constraints from the lead session: write only in lane A's files (`docs/LANES.md`); do not edit
`Backend/app/agent_iam.py` or wire admin into gateway routes; no AWS calls in checks; ask Huy before the
real `cognito.sh` run.

## Desired Outcome

The backend trusts only Cognito tokens from the Mandate pool: staff access tokens from `staff-web` and agent
client_credentials tokens from `mandate-agent` with scope `mandate/tools`, through the exact lane A → B
interface. A dry run of `cognito.sh` shows every AWS command that would build the pool, with the agent client
first, and the README explains the design in plain words.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | New Cognito tests pass | `PYTHONPATH=Backend:packages/mandate-contracts/src .venv/bin/python -m pytest -q Backend/tests/test_cognito.py` | exit 0 |
| C2 | Full suite green | `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests` | exit 0 |
| C3 | /me works for a Cognito user | `PYTHONPATH=Backend:packages/mandate-contracts/src .venv/bin/python -m pytest -q Backend/tests/test_cognito.py -k me_` | stdout matches /\d+ passed/ |
| C4 | Script parses | `bash -n Backend/infra/cognito.sh` | exit 0 |
| C5 | Dry run builds agent client before staff client, in us-east-1 | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PATH=/usr/bin:/bin bash Backend/infra/cognito.sh --dry-run 2>&1` | stdout matches /create-user-pool [\s\S]*--region us-east-1[\s\S]*create-user-pool-domain[\s\S]*create-resource-server[\s\S]*mandate\/tools[\s\S]*create-user-pool-client[\s\S]*mandate-agent[\s\S]*client_credentials[\s\S]*create-user-pool-client[\s\S]*staff-web[\s\S]*http:\/\/localhost:5173\// |
| C6 | Dry run creates groups and users | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PATH=/usr/bin:/bin bash Backend/infra/cognito.sh --dry-run 2>&1` | stdout matches /create-group[\s\S]*staffer[\s\S]*create-group[\s\S]*admin[\s\S]*admin-create-user[\s\S]*maya[\s\S]*admin-add-user-to-group[\s\S]*admin-create-user[\s\S]*chiefofstaff[\s\S]*admin-add-user-to-group/ |
| C7 | Dry run never touches AWS (no aws CLI on PATH) and exits 0 | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PATH=/usr/bin:/bin bash Backend/infra/cognito.sh --dry-run` | exit 0 |
| C8 | Dry run prints the env names the app needs | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PATH=/usr/bin:/bin bash Backend/infra/cognito.sh --dry-run 2>&1` | stdout matches /COGNITO_USER_POOL_ID[\s\S]*COGNITO_STAFF_CLIENT_ID[\s\S]*COGNITO_AGENT_CLIENT_ID[\s\S]*COGNITO_DOMAIN/ |
| C9 | README explains revocation | `grep -iE "revoc\|revok" Backend/infra/README.md \| grep -i dynamo` | exit 0 |
| C10 | Agent client allows only client_credentials (no user sign-in flows) | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PATH=/usr/bin:/bin bash Backend/infra/cognito.sh --dry-run 2>&1 \| grep 'client-name mandate-agent'` | stdout matches /--explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH --allowed-o-auth-flows client_credentials/ |
| C11 | No flag means dry run: prints the create commands, exits 0 without aws | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PATH=/usr/bin:/bin bash Backend/infra/cognito.sh 2>&1` | stdout matches /aws cognito-idp create-user-pool [\s\S]*COGNITO_USER_POOL_ID=<POOL_ID>/ |
| C12 | --apply is the only way to a real run (fails before any change without aws) | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PATH=/usr/bin:/bin bash Backend/infra/cognito.sh --apply 2>&1; echo exit=$?` | stdout matches /aws CLI not found[\s\S]*exit=1/ |

## Not Checkable By Command

- Real AWS run of `cognito.sh` and the WP1 done-checks (curl client_credentials returns an access token with
  scope `mandate/tools`; Maya logs in through managed login): the lead (this session) after Huy approves the
  commands, by hand.
- Idempotency against a real pool (second run creates nothing new): by hand during the real run; the trial
  reviews the lookup-before-create logic by reading the script.
- README is plain words and Huy can explain every line: Huy.

## Plan

### Goal

The backend accepts only Cognito access tokens from the Mandate pool in us-east-1. Staff tokens from
`staff-web` reach user routes and `GET /api/v1/me`; agent client_credentials tokens from `mandate-agent` with
scope `mandate/tools` reach agent routes. Both go through the exact lane A → B interface in `docs/LANES.md`.
Every failure fails closed: 401 for a bad or missing token, 403 for a valid user in the wrong group.
`Backend/infra/cognito.sh` builds the pool idempotently. Its `--dry-run` prints every AWS command without
needing the aws CLI. A plain-words README explains the two clients and why revocation lives in DynamoDB.

### Out of scope

- Real AWS run of `cognito.sh`. Huy approves the printed commands first; the lead runs it.
- Wiring `require_admin` / `require_staff` / `require_agent_client` into gateway routes (lane B).
- Edits to `Backend/app/agent_iam.py`, `Backend/app/main.py`, `Backend/app/core/supabase.py`,
  `Backend/requirements.txt`, `.env.example`, `packages/**`, `Frontend/**`, root `README.md`,
  `docs/ARCHITECTURE.md`.
- Removing the Supabase settings from `config.py`. Lane A may only add Cognito settings there.
- Sign-up, MFA, forgot-password, federation, token revocation (lane B's DynamoDB check).

### Context

- `Backend/app/core/auth.py` today decodes Supabase HS256/JWKS tokens. `agent_iam.py` (read-only for us)
  imports `CurrentUser, bearer, get_current_user` and calls `get_current_user(creds)` as a plain function, so
  all three names must survive with the same call shape. `supabase.py` imports `CurrentUser, get_current_user`
  and reads `user.token`; keep the `CurrentUser` fields `id, email, token, claims`.
- Reuse the existing pattern in `auth.py`: `HTTPBearer(auto_error=False)`, an `@lru_cache` `jwt.PyJWKClient`,
  `get_settings()` read at call time (never at import time, because tests clear the settings cache).
- `pyjwt[crypto]>=2.9` is already in `Backend/requirements.txt`. That pulls in `cryptography`, so tests can
  generate RSA keys. No new dependency.
- `mandate_contracts.SCOPE == "mandate/tools"`. Import it; never retype the string in Python.
- Cognito access tokens have no `aud`. They carry `client_id`, `token_use="access"`, `scope` (a space-separated
  string), `username`, `sub` and `cognito:groups` (a list, absent when the user is in no group). Email is
  **not** in an access token by default, so `StaffIdentity.email` is `None` in practice. That is expected.
- An ID token has `token_use="id"` and `aud=<client id>` with no `client_id` claim. Our checks reject it.
- `Backend/tests/conftest.py` sets only Supabase env, so Cognito settings default to empty in the suite. With
  an empty pool id or client id, every verify returns 401 before any network call. This keeps
  `test_auth.py` (missing / expired / wrong-aud HS256 tokens all get 401) green without edits.
- `Backend/tests/test_routes.py::test_admin_routes_need_user_login_when_enabled` and
  `::test_agent_token_routes_stay_open_to_agents` sign in with a Supabase HS256 token and expect 200. Once
  auth.py drops Supabase they get 401 and C2 goes red. That file is outside lane A (see Open Questions, T6).
- `Backend/infra/` does not exist yet. LANES rule 6 requires a README in a new folder, and T9 covers it.

### Files

| File | Change |
|---|---|
| `Backend/app/core/config.py` | add 5 Cognito settings (empty defaults except region) |
| `Backend/app/core/cognito.py` | **new**: dataclasses, `bearer`, JWKS lookup, two verify functions, three FastAPI deps |
| `Backend/app/core/auth.py` | becomes a thin wrapper: re-export `bearer`, keep `CurrentUser`, `get_current_user` calls `verify_staff_token` |
| `Backend/tests/test_cognito.py` | **new**: all Cognito tests, written first |
| `Backend/tests/test_routes.py` | swap the HS256 user token for a patched staff login (BLOCKED: needs lead approval) |
| `Backend/app/routers/profiles.py` | `GET /me` returns the Cognito identity; drop `PATCH /me` (approved) |
| `Backend/app/schemas/profile.py` | replace `Profile` / `ProfileUpdate` with `Me` (approved) |
| `Backend/app/services/profiles.py` | delete; dead once `/me` stops reading Supabase (approved) |
| `Backend/infra/cognito.sh` | **new**: idempotent pool setup with `--dry-run` and `--env-file` |
| `Backend/infra/README.md` | **new**: `## Cognito` section in plain words |

### Design (decided; implementers follow it exactly)

`cognito.py`, top to bottom, small enough to explain line by line:

```python
bearer = HTTPBearer(auto_error=False)          # moved here; auth.py re-exports it (no circular import)
STAFF_GROUPS = frozenset({"staffer", "admin"})

@dataclass(frozen=True) class StaffIdentity:  username: str; sub: str; email: str | None; groups: frozenset[str]
@dataclass(frozen=True) class AgentClientIdentity:  client_id: str; scopes: frozenset[str]

def _issuer() -> str            # f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
@lru_cache
def _jwks_client(url) -> jwt.PyJWKClient
def _signing_key(token) -> key  # the ONE function tests patch; _jwks_client(f"{_issuer()}/.well-known/jwks.json")
def _decode_access_token(token, client_id) -> dict
    # raise ValueError if pool id or client_id is empty (fail closed, no network)
    # jwt.decode(token, _signing_key(token), algorithms=["RS256"], issuer=_issuer(),
    #            options={"require": ["exp", "iss", "token_use", "client_id"], "verify_aud": False})
    # raise ValueError unless claims["token_use"] == "access" and claims["client_id"] == client_id
def verify_staff_token(token) -> StaffIdentity
    # try: decode with cognito_staff_client_id, build identity (email=claims.get("email"),
    #      groups=frozenset(claims.get("cognito:groups", [])))
    # except Exception: raise HTTPException(401, "Invalid or expired token") from None
def verify_agent_client_token(token) -> AgentClientIdentity
    # same, with cognito_agent_client_id; scopes = frozenset(claims.get("scope", "").split());
    # SCOPE (from mandate_contracts) not in scopes -> 401
def require_staff(creds=Depends(bearer)) -> StaffIdentity        # None -> 401; no STAFF_GROUPS overlap -> 403
def require_admin(identity=Depends(require_staff)) -> StaffIdentity  # "admin" not in groups -> 403
def require_agent_client(creds=Depends(bearer)) -> AgentClientIdentity  # None -> 401
```

The 401 detail is the same generic text for every failure, so it never says *why* a token failed.

`auth.py`: `from app.core.cognito import bearer, verify_staff_token`. `CurrentUser` keeps its four fields.
`get_current_user(creds=Depends(bearer))`: if `creds` is None, return 401. Otherwise
`identity = verify_staff_token(creds.credentials)` and return
`CurrentUser(id=identity.username, email=identity.email, token=creds.credentials, claims=asdict(identity))`.
No group check here, matching today's "any valid login". The Supabase decode path and `_jwks_client` are
deleted.

`config.py` (Cognito block only): `cognito_region: str = "us-east-1"`, `cognito_user_pool_id: str = ""`,
`cognito_staff_client_id: str = ""`, `cognito_agent_client_id: str = ""`, `cognito_domain: str = ""`.

`/me`: `GET /me` depends on `require_staff`. It returns
`Me(username, sub, email, groups=sorted(identity.groups))` with `response_model=Me`. `PATCH /me` is removed.
The router's name and `/me` prefix stay unchanged, so `main.py` is untouched.

`cognito.sh` (bash, `set -euo pipefail`, every AWS call carries `--region us-east-1`):

- **Flags.** `--dry-run`; `--env-file PATH` (default `<repo root>/.env`, resolved from the script's own
  location). Constants at the top: `POOL_NAME=mandate`, `DOMAIN_PREFIX=${COGNITO_DOMAIN_PREFIX:-<see OQ2>}`.
- **Dry run.** Never calls or looks for `aws`. Every lookup counts as "not found", and later steps use
  placeholders (`<POOL_ID>`, `<AGENT_CLIENT_ID>`, `<STAFF_CLIENT_ID>`). Each command is printed as one
  `aws cognito-idp ... --region us-east-1` line. Temporary passwords print as `'****'` and are not
  required. No file is written. Exit 0.
- **Real run.** Exit non-zero before any change if `aws` is missing or `MAYA_TEMP_PASSWORD` /
  `CHIEFOFSTAFF_TEMP_PASSWORD` is unset. Also exit non-zero if the env file is not git-ignored
  (`git check-ignore -q`). Every create uses `--query <id> --output text`, or `>/dev/null`, so no JSON
  (and never `ClientSecret`) reaches stdout.
- **Step headers.** Each step prints a header line first. The resource-server header must contain
  `mandate/tools`, because C5 expects that text before the agent client's create command.
- **Order and lookups.** Each step looks up the resource first and creates it only if missing.
  1. Pool. Lookup: `list-user-pools --max-results 60 --query "UserPools[?Name=='mandate'].Id | [0]"`.
     Create: `create-user-pool --pool-name mandate --admin-create-user-config AllowAdminCreateUserOnly=true`
     `--mfa-configuration OFF --account-recovery-setting 'RecoveryMechanisms=[{Priority=1,Name=admin_only}]'`
     `--username-configuration CaseSensitive=false`.
  2. Domain. Lookup: `describe-user-pool --query UserPool.Domain`. Create:
     `create-user-pool-domain --domain $DOMAIN_PREFIX --user-pool-id <POOL_ID> --managed-login-version 2`.
  3. Resource server. Lookup: `describe-resource-server --identifier mandate`, where a non-zero exit means
     missing. Create: `create-resource-server --identifier mandate --name mandate`
     `--scopes ScopeName=tools,ScopeDescription=...`.
  4. Client `mandate-agent`. Lookup: `list-user-pool-clients` with a query by `ClientName`. Create:
     `create-user-pool-client --client-name mandate-agent --generate-secret`
     `--allowed-o-auth-flows client_credentials --allowed-o-auth-flows-user-pool-client`
     `--allowed-o-auth-scopes mandate/tools --supported-identity-providers COGNITO`.
  5. Client `staff-web`. Create: `--client-name staff-web --no-generate-secret --allowed-o-auth-flows code`
     `--allowed-o-auth-flows-user-pool-client --allowed-o-auth-scopes openid email profile`
     `--supported-identity-providers COGNITO`
     `--callback-urls http://localhost:3000/ http://localhost:5173/`
     `--logout-urls http://localhost:3000/ http://localhost:5173/`
     `--prevent-user-existence-errors ENABLED`
     `--explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH`. Then
     `create-managed-login-branding --client-id <STAFF_CLIENT_ID> --use-cognito-provided-values`, if missing.
     PKCE comes from the client being public (no secret).
  6. Groups `staffer`, then `admin`. Lookup: `get-group`. Create: `create-group`.
  7. Users `maya`, then `chiefofstaff`. Lookup: `admin-get-user`. Create:
     `admin-create-user --username maya --temporary-password <env> --message-action SUPPRESS`. Then always
     run `admin-add-user-to-group` (staffer / admin); it is idempotent by itself.
  8. Output. Print `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_STAFF_CLIENT_ID`,
     `COGNITO_AGENT_CLIENT_ID`, `COGNITO_DOMAIN=https://$DOMAIN_PREFIX.auth.us-east-1.amazoncognito.com`, in
     that order. Real run only: fetch the agent secret with
     `describe-user-pool-client --query UserPoolClient.ClientSecret` into a variable. Rewrite the env file
     (drop old `COGNITO_*` lines, append the new ones plus `COGNITO_AGENT_CLIENT_SECRET`) via a temp file
     and `mv`. Print only "secret written to <path>".

### Tests (T1, test-first; all in `Backend/tests/test_cognito.py`)

**Setup**

- Local RSA key via `cryptography`, and a `make_token(key=..., headers=..., **claims)` helper. Defaults: a
  valid staff access token for `maya`, with `iss`, `exp=now+300`, `token_use=access`,
  `client_id=staff-client`, `username`, `sub`, `cognito:groups=["staffer"]` and `scope="openid email profile"`.
- Autouse fixture: `monkeypatch.setenv` sets `COGNITO_USER_POOL_ID=us-east-1_TEST`,
  `COGNITO_STAFF_CLIENT_ID=staff-client` and `COGNITO_AGENT_CLIENT_ID=agent-client`. It also does
  `monkeypatch.setattr(cognito, "_signing_key", lambda t: public_key)`, and calls `get_settings.cache_clear()`
  before the yield **and after it** so later test files see a clean cache.
- Import modules (`from app.core import cognito, auth`), not names, so the file still collects while
  `require_*` don't exist yet. Use a tiny in-test FastAPI app with one route per `require_*` dependency, and
  `app.main.app` for `/me`.

**Names.** The prefixes map to tasks. No other test name may contain `me_`.

- `test_verify_*` (T3):
  - `test_verify_staff_accepts_valid_token`: username `maya`, groups, `email is None`.
  - Rejected with 401: `test_verify_staff_rejects_tampered_token` (payload edited, and signed with another
    key), `test_verify_staff_rejects_expired_token`, `test_verify_staff_rejects_wrong_issuer`,
    `test_verify_staff_rejects_wrong_client` (agent client id), `test_verify_staff_rejects_id_token`
    (`token_use=id`, `aud=staff-client`, no `client_id`), `test_verify_staff_rejects_hs256_token`,
    `test_verify_rejects_everything_when_pool_unconfigured` (env cleared and cache cleared, valid
    signature, 401).
  - `test_verify_agent_accepts_client_credentials_token`: `client_id=agent-client`,
    `scope="mandate/tools"`, no username.
  - Also rejected with 401: `test_verify_agent_rejects_missing_scope`,
    `test_verify_agent_rejects_staff_client`.
- `test_require_*` (T4): `test_require_staff_no_token_401`, `test_require_staff_no_group_403`,
  `test_require_admin_403_for_maya`, `test_require_admin_allows_chiefofstaff`,
  `test_require_agent_client_no_token_401`, `test_require_agent_client_accepts_agent_token`.
- `test_current_user_*` (T5): `test_current_user_from_cognito_staff_token` (`id == "maya"`,
  `token` is the raw token), `test_current_user_rejects_supabase_hs256_token`.
- `test_me_*` (T7, C3): `test_me_returns_cognito_identity` (200,
  `{"username":"maya","sub":...,"email":null,"groups":["staffer"]}`), `test_me_rejects_agent_token` (401),
  `test_me_requires_token` (401).

### Risks

- **Secret leak.** Without `--query`, `create-user-pool-client --generate-secret` prints `ClientSecret` to
  stdout. The plan requires `--query`/`>/dev/null` on every create, and secrets go only to a git-ignored env
  file. The trial should grep the script for any echo of `SECRET`/`PASSWORD` variables.
- **Fail-open via a broad except.** `verify_*` catch `Exception`, which also catches our own ValueErrors.
  That is intended. `require_*` raise 403 *after* verify, outside the try, so a 403 is never turned into a 401.
- **Algorithm confusion.** `algorithms=["RS256"]` is fixed, so HS256 tokens signed with the public key or
  `alg=none` are rejected. `test_verify_staff_rejects_hs256_token` locks this in.
- **Settings cache.** `get_settings` is `lru_cache`d and `test_routes.py` mutates the cached object. The
  Cognito fixture must clear the cache on teardown, or later tests read Cognito-configured settings.
- **Managed login v2 needs a branding style** on the app client, or the hosted page errors. Step 5 creates it.
  Before the real run, the lead confirms the flag names with `aws cognito-idp create-managed-login-branding help`
  and `create-user-pool-domain help`. Dry-run checks can't catch a wrong flag.
- **PATCH /me removal** breaks `Frontend/src/lib/api.js` `updateMe` and the `/account` page (lane D). Tell
  lane D. The console moves to Cognito login anyway.
- **Stale docs.** The `agent_iam.py` docstring, root `README.md`, `docs/ARCHITECTURE.md` and `.env.example`
  still say Supabase. They are outside the lane, so the lead updates them at merge.
- **Temporary passwords** must meet Cognito's default policy (8+ characters, upper, lower, digit, symbol),
  or `admin-create-user` fails in the real run. The README says so.

### Verification

Run C1–C9 from the Outcome Checks. C2 is only expected green after T5, T6 and T7 all land. Between T5 and T6,
the two `test_routes.py` admin-login tests are known red. C3 is green only after T7. By hand: the real
`cognito.sh` run, approved by Huy (see "Not Checkable By Command").

### Open Questions

1. **`Backend/tests/test_routes.py` is outside lane A, but C2 can't pass without editing it.** Its two
   admin-login tests use a Supabase HS256 token. *Recommendation:* approve editing that one file. Replace
   `_user_token()` with a fixture that does
   `monkeypatch.setattr("app.core.auth.verify_staff_token", ...)` to return a `StaffIdentity` for the token
   `"staff-token"`, so the tests keep checking route gating. Real token verification is covered in
   `test_cognito.py`. Alternative: share the RSA-key fixture through `conftest.py`, which needs approval for
   that file too.
2. **Domain prefix.** It must be globally unique in us-east-1 and is visible in the login URL.
   *Recommendation:* `COGNITO_DOMAIN_PREFIX` env var, default `mandate-a10-hack-2026`. Huy confirms the name.

### Approval Checklist

- [ ] Open questions resolved
- [ ] File list and steps confirmed

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| T1 | Write every test in Plan › Tests (red first) | test-author | `Backend/tests/test_cognito.py` | file has all named tests; C1 fails only with `ModuleNotFoundError`/`AttributeError` for `app.core.cognito` (red for the right reason) | — | done |
| T2 | Add the 5 Cognito settings | implementer | `Backend/app/core/config.py` | `PYTHONPATH=Backend .venv/bin/python -c "from app.core.config import Settings as S; s=S(_env_file=None); assert s.cognito_region=='us-east-1' and s.cognito_user_pool_id=='' and s.cognito_domain==''"` exits 0, and C2 still green | — | done |
| T3 | `cognito.py`: `bearer`, dataclasses, `_issuer`/`_jwks_client`/`_signing_key`/`_decode_access_token`, `verify_staff_token`, `verify_agent_client_token` | implementer | `Backend/app/core/cognito.py` | C1 with `-k verify` passes (all `test_verify_*`) | T1, T2 | done |
| T4 | `cognito.py`: `require_staff`, `require_admin`, `require_agent_client` | implementer | `Backend/app/core/cognito.py` | C1 with `-k require` passes (all `test_require_*`) | T3 | done |
| T5 | Switch `auth.py` to thin wrappers over cognito.py; delete the Supabase decode path | implementer | `Backend/app/core/auth.py` | C1 with `-k current_user` passes, and `pytest -q Backend/tests/test_auth.py` passes | T4 | done |
| T6 | Replace the HS256 user token in the two admin-login tests (OQ1) | test-author | `Backend/tests/test_routes.py` | C2 green (together with T5) | T5 | done |
| T7 | `GET /me` returns `Me` from `require_staff`; drop `PATCH /me`; replace schema; delete `services/profiles.py` | implementer | `Backend/app/routers/profiles.py`, `Backend/app/schemas/profile.py`, `Backend/app/services/profiles.py` (delete) | C3 passes; `pytest -q Backend/tests/test_auth.py` still passes | T4 | done |
| T8 | `cognito.sh` per Plan › Design | implementer | `Backend/infra/cognito.sh` | C4, C5, C6, C7, C8 pass | — | done |
| T9 | README `## Cognito`: what the script makes, dry run first and ask Huy before a real run, env vars, the two clients, password policy, why access tokens can't be revoked (signature checked locally until `exp`, so revocation is lane B's DynamoDB session/staffer check) | implementer | `Backend/infra/README.md` | C9 passes | T8 | done |

Parallel: T1, T2 and T8 can start together. T9 can run alongside T3–T7. T5 and T7 can run in parallel once
T4 is done. T6 runs after T5, because it patches `app.core.auth.verify_staff_token`, which T5 creates.
T3 and T4 share a file, so they run in sequence.

## Rounds

### Round 1 — 2026-11-07
- Built: T1–T9, commits 738e782..6cddde3 (+ 25331f3 conftest offline guard, lead-approved).
- Outcome checks: 9/9 pass; fixed [C1, C3–C9], regressed [] (scoreboard: cognito-wp1.rounds/round-1.json).
- Review: team-reviewer APPROVE, 0 Critical/High, 3 Medium (token_use/exp untested; list lookups swallow errors; real .env would make tests fetch the real JWKS). Security review: security:pass. Trial (full): falsifier FALSIFIED req 4 (tests patched `_signing_key`, not the JWKS; token_use check survives deletion); advocate PARTIAL. Judge skipped: the gap list was already clear and the verdict could not be ACCEPT.
- INCIDENT: the falsifier ran cognito.sh against real AWS (zsh has no `export -f`). Pool us-east-1_MLfQpodxG created exactly as designed; run 2 created nothing. Huy chose keep + reset temp passwords.
- Gap list for next round: (1) patch JWKS fetch, not key lookup; (2) token_use test with valid client_id, missing exp/token_use; (3) unconfigured test proves no fetch; (4) list lookups must not swallow errors; (5) env-file path/grep/symlink; (6) domain create output; (7) README PKCE/RevokeToken/CLI password; (8) conftest offline guard.
- Decision: next round (gaps fixable, 8 checks fixed this round).

### Round 2 — 2026-11-07
- Built: T10 tests (821c39f), T11 script (a906704), certifi CA fix found by the live done-check (20d922c), README (6fa145a). Live: temp passwords reset; real client_credentials token verified by the backend against the real JWKS.
- Outcome checks: 9/9 pass; fixed [], regressed [] (scoreboard: cognito-wp1.rounds/round-2.json).
- Review: team-reviewer APPROVE (0 Critical/High/Medium). Trial: advocate STRONG; falsifier FALSIFIED one point (mandate-agent default auth flows) → fixed in round 3.

### Round 3 — 2026-11-07
- Built: mandate-agent `--explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH` + new check C10 (2bae187); HomeFront rename in prose, reviewer Lows (986068e); live agent client updated with Huy's approval. Huy set permanent user passwords himself.
- Outcome checks: 10/10 pass (scoreboard: cognito-wp1.rounds/round-3.json).
- Trial (full): falsifier INCONCLUSIVE (nothing broken; real-AWS path forbidden to run); advocate STRONG; judge INSUFFICIENT EVIDENCE: every requirement MET except real-run idempotency/creation (1b/1c real half) UNSETTLED, because the live evidence is builder-reported only. No Critical/High finding, no integrity violation, no contradicted claim. Judge's "to fix": with Huy's approval, re-run cognito.sh for real against the kept pool and show only `admin-add-user-to-group` lines (no `create-*`), plus the client_credentials curl.
- Decision: STOPPED by the lead (integration merge planning) before a confirming real run; no code fix needed.

## Decisions

- 2026-11-07: trial depth full (auth). The loop file lives in `docs/loops/`, outside lane A's row; it is
  converge state only and can be dropped at merge.
- 2026-11-07: the lead approved the /me change, relayed by the coordinator. `GET /me` returns
  `{username, sub, email, groups}` from the Cognito identity and `PATCH /me` is dropped. Allowed files:
  `Backend/app/routers/profiles.py`, `Backend/app/services/profiles.py` and `Backend/app/schemas/profile.py`
  (if dead or reshaped), plus existing tests that hit /me. `Backend/app/main.py` stays untouched, and the
  router name and `/me` prefix are unchanged.
- 2026-11-07 (architect): `bearer` moves to `cognito.py` and `auth.py` re-exports it, which avoids a circular
  import. `CurrentUser` keeps `id, email, token, claims`, with `id` = Cognito username (the staffer id per
  LANES) and `claims` = `asdict(StaffIdentity)`. `get_current_user` accepts any valid staff-web access token
  with no group check, as before. `/me` uses `require_staff`.
- 2026-11-07 (architect): the `cognito.sh` dry run treats every lookup as "not found" and prints the full
  create sequence with `<POOL_ID>`-style placeholders, so it needs no aws CLI.
- 2026-11-07: the lead approved editing `Backend/tests/test_routes.py`, limited to
  `test_admin_routes_need_user_login_when_enabled`, `test_agent_token_routes_stay_open_to_agents` and the helper they
  use: replace `_user_token()` with a monkeypatch of `app.core.auth.verify_staff_token` returning a fixed
  StaffIdentity. Keep it minimal; lane B also edits nearby. Open Question 1 resolved.
- 2026-11-07: Huy approved the plan and checks: "Approve, build it." Domain prefix: default
  `mandate-a10-hack-2026` (overridable via `COGNITO_DOMAIN_PREFIX`); Huy did not ask for another name.
- 2026-11-07 (lead instruction after the incident): C5–C8 now also strip AWS credentials (`env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null`) on top of `--dry-run` and the stripped PATH, so a broken fake can never reach AWS. Stricter, not looser; all four still pass.
- 2026-11-07 (round 3): added C10 (stricter, from the round-2 falsifier): the `mandate-agent` create command must set `--explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH`, because Cognito's default otherwise enables user sign-in flows on the agent client. Product renamed to HomeFront in prose (lead instruction); technical names (`mandate/tools`, pool `mandate`, `mandate-agent`, domain) unchanged.
- 2026-11-07 (lead instruction): `cognito.sh` is a dry run by default; only `--apply` changes AWS (`--dry-run` kept as a no-op alias), matching the other lanes' AWS scripts. Added C11 (no flag = dry run) and C12 (`--apply` with no aws on PATH stops with "aws CLI not found" before any change).
