# Loop: connected-accounts

Status: BUILDING
Profile: feature (trial depth: full, because this handles OAuth and provider tokens)
Round: 3 / 4
Base commit: 23fa467
Branch: Connected-Accounts-Huy (worktree `.claude/worktrees/connections`)

## Original Request

"Also the account should have details of different user account like gmail or socials, if they don't have it signed in, have a signin option or connect their social. When the access to post or send something isn't available in the chat assistant tab, it asks the user to sign in"

(Huy attached a screenshot of the Account page: Username maya, Email —, Groups staffer, Cognito sub, Signed in until, Sign out.)

## Desired Outcome

The Account page lists Gmail and the social accounts HomeFront posts to, each connected or not, with a
real OAuth Connect button (or an honest "not set up yet") and Disconnect, and shows the staffer's email.
Provider tokens live only on the server, encrypted with KMS and bound to the staffer. When the assistant
tries to send or post for a staffer with no connected account, nothing is sent and the chat shows a plain
explanation with a Connect button.

## Outcome Checks

`ENV` = credentials stripped plus `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src`, written out in each command. The worktree's `.venv` and `Frontend/node_modules` are symlinks to the main checkout's (gitignored).

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Full Python suite: no failure outside the 66 already failing on origin/main | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q -rf Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests packages/identity-gateway/tests 2>&1 \| grep '^FAILED' \| sed 's/ - .*//' \| sort -u \| comm -23 - docs/loops/connected-accounts.rounds/baseline-failures.txt \| sed 's/^/NEW /'; test -f Backend/tests/test_connections.py && echo SUITE_RAN` | stdout matches /^SUITE_RAN\s*$/ |
| C2 | Connections API + OAuth security tests (isolation, state forged/missing/replayed/other user, no token leaks, disconnect, offboarding, agent can't connect) | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q Backend/tests/test_connections.py` | stdout matches /\b([2-9][0-9]) passed(?!.*failed)/ |
| C3 | Gateway helper: require_connection, KMS context binding, refresh, senders, needs_connection through a guarded()-shaped harness | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q packages/identity-gateway/tests/test_gw_connections.py` | stdout matches /\b([1-9][0-9]) passed(?!.*failed)/ |
| C4 | /me returns the email from Cognito userInfo when the access token lacks it | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src .venv/bin/python -m pytest -q Backend/tests/test_me_email.py` | stdout matches /[1-9][0-9]* passed(?!.*failed)/ |
| C5 | Frontend typecheck and all vitest suites | `cd Frontend && npx tsc --noEmit && npx vitest run 2>&1 \| tail -5` | stdout matches /Tests\s+\d+ passed/ |
| C6 | Account page + ConnectPrompt tests (connected / not connected / not set up / connect / disconnect / callback / chat prompt) | `cd Frontend && npx vitest run src/pages/Account.test.tsx src/components/ConnectPrompt.test.tsx src/pages/ConnectCallback.test.tsx 2>&1 \| tail -5` | stdout matches /Tests\s+([1-9][0-9]) passed/ |
| C7 | Production build | `cd Frontend && npm run -s build 2>&1 \| tail -3` | stdout contains built in |
| C8 | KMS setup script dry-runs and is idempotent-shaped | `bash -n Backend/infra/connections.sh && env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null bash Backend/infra/connections.sh 2>&1` | stdout contains kms create-key |
| C9 | The connected-accounts frontend files never handle provider tokens, secrets or ciphertext | `test -f Frontend/src/lib/connections.ts && test -f Frontend/src/components/ConnectPrompt.tsx && ! grep -nE 'access_token\|refresh_token\|client_secret\|ciphertext' Frontend/src/lib/connections.ts Frontend/src/pages/Account.tsx Frontend/src/components/ConnectPrompt.tsx Frontend/src/pages/ConnectCallback.tsx` | exit 0 |

## Not Checkable By Command

- A real Google / X consent screen and a real Gmail send / X post: needs Huy to register the OAuth apps and run `connections.sh --apply` (steps in the report).
- Cognito userInfo returning Maya's email for real: only if Maya's Cognito user has an `email` attribute; Huy, live.
- 375px and desktop look of the Account page and the chat prompt: team-verifier in review, by screenshot.
- The chat actually showing the prompt end to end depends on the chat renderer mounting `ConnectPrompt` (Lane H / lead) and on Lane H calling the helper for its new tools; covered here at the gateway (`guarded()`) and component level.

## Plan

Design (lead-written; small enough that a team-architect pass would only restate it):

- **Storage** `packages/identity-gateway/src/identity_gateway/connections.py`: items in the existing `mandate` table, `pk=CONNECTION#<staffer>`, `sk=PROVIDER#<provider>` with `handle`, `connected_at`, `scopes`, `ciphertext` (KMS `Encrypt` of the token JSON with EncryptionContext `{staffer, provider}`, so a row copied to another staffer cannot be decrypted). OAuth state rows `pk=OAUTHSTATE#<state>` hold staffer, provider, PKCE verifier, expiry (10 min); deleted on first use (replay fails). `Cipher` protocol: `KmsCipher` (env `CONNECTIONS_KMS_KEY_ID`); tests use moto KMS. No key configured means every provider reads "not set up".
- **Providers**: `gmail` (Google, scopes `openid email https://www.googleapis.com/auth/gmail.send`, `access_type=offline`, PKCE S256 + client secret) and `x` (scopes `tweet.read tweet.write users.read offline.access`, PKCE S256, Basic auth). LinkedIn, Facebook, Bluesky listed as "not supported yet; HomeFront drafts a share link" (posting for others needs app review). Client id/secret from env `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `X_OAUTH_CLIENT_ID/SECRET`; missing means "not set up yet, ask your admin".
- **Helper for Lane H**: `NeedsConnection(provider)`, `require_connection(staffer, provider)`, `needs_connection_result(exc)`, `provider_for_tool(tool, args)`, `send_gmail(staffer, to, subject, body)`, `post_x(staffer, text)`; refresh on expiry; tokens never leave this module. Lane H wires it into `guarded()` and `send_draft()` (lead ruling); this loop tests it through a guarded()-shaped harness.
- **API** `Backend/app/routers/connections.py` (all `require_staff`, staffer = token username, never a path/body id): `GET /connections`, `POST /connections/{provider}/start` -> `{authorizeUrl}`, `(see Interfaces), `DELETE /connections/{provider}` (provider revoke + delete); the callback is `POST /connections/callback` (the provider comes from the stored state, not the URL). Offboard (`staff.py`) calls `revoke_all(staffer)`.
- **/me email**: when the access token has no `email`, call Cognito `oauth2/userInfo` with the same access token (cached per sub; failure means null). `Backend/app/routers/profiles.py`.
- **Frontend**: `lib/connections.ts` (API + mock), `pages/Account.tsx` Connected accounts section, `pages/ConnectCallback.tsx` at `/account/connected` (posts code+state with the staff token), `components/ConnectPrompt.tsx` + `parseNeedsConnection()` for the chat renderer, route in `App.tsx`, one-line hook in `mockApi.ts`.
- **Infra** `Backend/infra/connections.sh`: dry-run by default, `--apply` creates KMS key + alias `alias/homefront-connections` (idempotent), prints the env line.


## Interfaces (tests are written against these)

### `packages/identity-gateway/src/identity_gateway/connections.py`

```python
CONNECTABLE = ("gmail", "x")
PROVIDERS = {  # id -> label, connectable
    "gmail": "Gmail", "x": "X", "linkedin": "LinkedIn", "facebook": "Facebook", "bluesky": "Bluesky"}
SCOPES = {"gmail": "openid email https://www.googleapis.com/auth/gmail.send",
          "x": "tweet.read tweet.write users.read offline.access"}

class NeedsConnection(Exception): provider: str          # str(e) never contains a token
class ProviderError(Exception): ...                      # provider API failed (not auth)
class OAuthStateError(Exception): ...                    # missing / unknown / expired / replayed / other user's state
class NotConfigured(Exception): ...                      # no KMS key or no client id/secret for that provider

@dataclass(frozen=True)
class Connection: staffer: str; provider: str; handle: str; connected_at: str; scopes: str   # NO token field

class Cipher(Protocol):
    def encrypt(self, plaintext: bytes, context: dict[str, str]) -> bytes: ...
    def decrypt(self, ciphertext: bytes, context: dict[str, str]) -> bytes: ...
class KmsCipher:  # KmsCipher(kms_client, key_id); EncryptionContext = {"staffer": .., "provider": ..}

@dataclass(frozen=True)
class OAuthConfig:  # from env: CONNECTIONS_KMS_KEY_ID, GOOGLE_OAUTH_CLIENT_ID/SECRET, X_OAUTH_CLIENT_ID/SECRET,
    ...             # CONNECTIONS_REDIRECT_URI (default http://localhost:3000/account/connected)
    @classmethod
    def from_env(cls) -> "OAuthConfig": ...
    def configured(self, provider: str) -> bool: ...   # connectable AND kms key AND client id+secret set

class ConnectionService:
    def __init__(self, store: MandateStore, cipher: Cipher | None, http: httpx.Client, config: OAuthConfig,
                 clock: Callable[[], float] = time.time): ...
    def list(self, staffer: str) -> list[dict]   # one entry per PROVIDERS key:
        # {"provider","label","supported","configured","connected","handle","connectedAt","scopes"} — never tokens
    def start(self, staffer: str, provider: str) -> str          # authorize URL; PKCE S256; stores state row
    def finish(self, staffer: str, code: str, state: str) -> Connection   # provider comes from the state row
    def disconnect(self, staffer: str, provider: str) -> bool    # revoke at provider (best effort) + delete row
    def revoke_all(self, staffer: str) -> int                    # offboarding
    def require(self, staffer: str, provider: str) -> Connection # raises NeedsConnection (no row, staffer
        # missing/inactive, decrypt fails, refresh fails)
    def send_gmail(self, staffer, to, subject, body, in_reply_to: str | None = None) -> dict  # {"status":"sent","messageId"}
    def post_x(self, staffer, text, reply_to: str | None = None) -> dict                      # {"status":"posted","id"}

def get_connections() -> ConnectionService; def set_connections(svc | None) -> None   # singleton like get_gateway
# module-level wrappers Lane H calls:
def require_connection(staffer_id: str, provider: str) -> Connection
def send_gmail(staffer_id, to, subject, body, in_reply_to=None) -> dict
def post_x(staffer_id, text, reply_to=None) -> dict
def needs_connection_result(e: NeedsConnection) -> dict
    # {"status":"needs_connection","provider":"gmail","label":"Gmail","connectUrl":"/account?connect=gmail",
    #  "note":"NOT sent. <staffer> has not connected Gmail. Ask them to connect it on the Account page, then try again."}
def provider_for_tool(tool: str, args: dict) -> str | None
    # email_send, email_send_list -> "gmail"; social_post, social_reply -> "x" when args.get("platform") in (None, "", "x");
    # any other platform or tool -> None
```

Rules: DynamoDB rows `pk=CONNECTION#<staffer>`, `sk=PROVIDER#<provider>`, attrs `handle`, `connected_at`, `scopes`,
`ciphertext` (Binary: KMS ciphertext of JSON `{access_token, refresh_token, expires_at}`). State rows
`pk=OAUTHSTATE#<state>`, `sk=STATE`, attrs `staffer`, `provider`, `verifier`, `expires` (epoch, +600 s); `finish`
deletes the row atomically (`delete_item(ReturnValues="ALL_OLD")`) before anything else, so a state works once; a
wrong staffer or expired row raises `OAuthStateError` (and is still consumed). The access token is refreshed when
within 60 s of `expires_at`; a refresh failure or a provider 401 deletes the row and raises `NeedsConnection`.
Gmail handle = `email` claim of the token endpoint's `id_token` (payload decode only, per Google's OIDC docs for tokens
received directly from the token endpoint); X handle = `@` + `data.username` from `GET https://api.x.com/2/users/me`.
Endpoints: Google auth `https://accounts.google.com/o/oauth2/v2/auth` (`access_type=offline`, `prompt=consent`),
token `https://oauth2.googleapis.com/token` (client secret + `code_verifier`), revoke
`https://oauth2.googleapis.com/revoke`, send `POST https://gmail.googleapis.com/gmail/v1/users/me/messages/send`
`{"raw": base64url(RFC 2822)}`. X auth `https://x.com/i/oauth2/authorize`, token `https://api.x.com/2/oauth2/token`
(HTTP Basic client auth), revoke `https://api.x.com/2/oauth2/revoke`, post `POST https://api.x.com/2/tweets`
`{"text", "reply": {"in_reply_to_tweet_id"}}`. Nothing in this module logs a token, code, verifier or ciphertext.
Tests use moto (DynamoDB + KMS) and `httpx.MockTransport`; no network.

### HTTP API (`Backend/app/routers/connections.py`, prefix `/api/v1`, every route `require_staff`; the staffer is the token's username, never a path/body field)

| Method + path | Body | Response |
|---|---|---|
| `GET /connections` | — | `{"connections": [list() entries]}` |
| `POST /connections/{provider}/start` | `{}` | `{"authorizeUrl"}`; 404 unknown provider; 409 `{"detail": "not_set_up"}` when not connectable or not configured |
| `POST /connections/callback` | `{"code","state"}` | the provider's list() entry; 400 `{"detail":"invalid_state"}` for any state problem; 502 when the token exchange fails |
| `DELETE /connections/{provider}` | — | `{"provider","connected": false}` (idempotent) |

Offboarding (`POST /staff/{id}/offboard`) also calls `get_connections().revoke_all(id)` and returns `revokedConnections`.
`GET /me` fills `email` from `https://<COGNITO_DOMAIN>/oauth2/userInfo` (bearer = the same access token) when the token
has none; cached per `sub`; any failure means `email: null`.

### Frontend

- `Frontend/src/lib/connections.ts`: `ConnectionView` type (the list() entry), `connectionsApi.list()`, `.start(provider)`
  (returns `authorizeUrl`), `.finish(code, state)`, `.disconnect(provider)` via `request()` from `lib/api.ts`;
  `beginConnect(provider)` = start + `window.location.assign(authorizeUrl)`.
- `Frontend/src/lib/mockConnections.ts`: mock handler for `/connections*` (mock mode simulates connect: start returns
  `/account/connected?code=mock&state=<s>`); `mockApi.ts` gets one hook line.
- `Frontend/src/pages/Account.tsx`: existing facts (email now shows `me.email`) plus a "Connected accounts" list: per
  provider label, status (`Connected as <handle>` + connected date / `Not connected` / `Not set up yet — ask your admin` /
  `Not supported yet`), and a `Connect <Label>` button (calls `beginConnect`) or `Disconnect` button. `?connect=gmail`
  highlights and scrolls to that row but never starts OAuth by itself (a link an agent wrote can't start a sign-in).
- `Frontend/src/pages/ConnectCallback.tsx` at route `/account/connected` (inside `RequireAuth`): reads `code`/`state`
  (or `error`), POSTs `finish` once, then shows success and a link back to Account (and to the chat); on `error` or a 400
  it shows plain words and POSTs nothing / nothing more.
- `Frontend/src/components/ConnectPrompt.tsx`: `parseNeedsConnection(x: unknown)` → `{provider,label,note} | null`
  (accepts the result object, a JSON string, or text containing that JSON); `<ConnectPrompt provider label? note? />`
  renders "Nothing was sent. To let your assistant send this, connect your <Label>." with a `Connect <Label>` button
  (`beginConnect`) and a "Manage connected accounts" link to `/account`. For Lane H / the lead to mount in the chat.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Backend tests first (API security + helper + /me email) | team-test-author | `Backend/tests/test_connections.py`, `Backend/tests/test_me_email.py`, `packages/identity-gateway/tests/test_gw_connections.py` | tests fail for missing module | — | pending |
| 2 | connections.py + router + offboard hook + /me email + guarded wiring | team-implementer | `packages/identity-gateway/src/identity_gateway/connections.py`, `Backend/app/routers/connections.py`, `Backend/app/main.py`, `Backend/app/routers/staff.py`, `Backend/app/routers/profiles.py`, `Backend/app/core/config.py`, `packages/identity-gateway/README.md` | C1 C2 C3 C4 | 1 | pending |
| 3 | Infra script | team-implementer | `Backend/infra/connections.sh`, `Backend/infra/README.md` | C8 | — | pending |
| 4 | Frontend tests first | team-test-author | `Frontend/src/pages/Account.test.tsx`, `Frontend/src/components/ConnectPrompt.test.tsx`, `Frontend/src/pages/ConnectCallback.test.tsx` | fail for missing code | — | pending |
| 6 | Round-2 tests for G1–G9 + M4 | team-test-author | the three connections test files | fail for the gaps | 2, 5 | in progress |
| 7 | Round-2 fixes G1–G9 | team-implementer | connections.py, staff.py, profiles.py, ConnectPrompt.tsx, connections.ts | C2 C3 C6 + new tests | 6 | pending |
| 5 | Frontend build | team-implementer | `Frontend/src/lib/connections.ts`, `Frontend/src/pages/Account.tsx`, `Frontend/src/pages/ConnectCallback.tsx`, `Frontend/src/components/ConnectPrompt.tsx`, `Frontend/src/pages/Account.css`, `Frontend/src/App.tsx` (route only), `Frontend/src/lib/mockApi.ts` (one hook) | C5 C6 C7 C9 | 4, 2 (API shape) | pending |

## Rounds

### Round 1 — 2026-11-08
- Built: tasks 1–5, commits 489ae28 5716eee 3d0c73a 085ff81 18731fe
- Outcome checks: 9/9 pass; fixed [C1, C2, C3, C4, C6, C8, C9], regressed [] (scoreboard: docs/loops/connected-accounts.rounds/round-1.json)
- Verifier: PASS (Python 799 passed / 66 baseline failures / 0 new; vitest 94; build; mock UI at 375px and ~800px: list, connect, disconnect, ?connect=x highlight, no console errors). Gaps: no numeric scrollWidth, no >800px.
- Review: CHANGES REQUESTED — M1 refresh update_item resurrects a disconnected row / parallel refresh deletes the winner's row; M2 disconnect/offboard abort on non-ClientError (offboard skips stop_staffer); M3 granted scopes not checked, missing token fields → 500. Lows: process-wide logger level, base64 ciphertext undocumented, grants left open on failure paths, ConnectPrompt trusts agent label/provider, cache TTL, type nits.
- Trial (full): falsifier FALSIFIED requirement 3 — ConnectPrompt is mounted nowhere and guarded() doesn't call the helper yet (Lane H wiring + chat mount pending); mutation probes 5/6 killed, M4 (parser ignores status) survived. Advocate hit its turn limit; judge not run (verdict would be REJECT on requirement 3).
- Gap list for next round: G1–G9 from review + M4 (tests in round 2, task 6); G10 requirement 3 end-to-end — needs lead ruling on the chat message field and the ChatPanel/ReviewQueue mount (asked 2026-11-08).
- Decision: next round because 7 checks were fixed this round and gaps remain.


### Round 2 — 2026-11-08
- Built: tasks 6–7 plus the chat/review mount, commits 9f7ab83 8987162 088649c 8d88b5f 2a9b3bd 22861b4
- Outcome checks: 9/9 pass; fixed [], regressed [] (scoreboard: round-2.json)
- Review: round-1 G1–G10 confirmed fixed; CHANGES REQUESTED for R1 (X refresh race, delete-before-save order) and R2 (missing_permission shown as "link expired") plus Lows R3–R8.
- Trial (full): falsifier INCONCLUSIVE — requirements 1, 2 CONFIRMED (mock UI at 375px), 6/6 mutation probes killed, nothing FALSIFIED; requirement 3 this branch's half CONFIRMED, end to end UNVERIFIED (producer is Lane H). Advocate PARTIAL (probes blocked by guard). Judge not run yet: waiting for the merged end-to-end evidence.
- Merge probe (Lane H b240500 + 22861b4, throwaway detached worktree): 46 tests that pass on Lane H alone fail because the gate ran before the hold. Lead ruled option 1 (gate only at send; email Gmail → office SES → needs_connection). Lane H renamed its test file (0bd611c) and implements option 1.
- Gap list for round 3: R1–R8 (task 8 tests, task 9 fixes); G11 re-run the merge probe after Lane H's option-1 commit, full suite + an end-to-end needs_connection test through the real modules, then the judge.
- Decision: next round — gaps remain and round 2 fixed G1–G10.

## Decisions

- 2026-11-08: Ciphertext stored as a base64 string (not DynamoDB Binary): same KMS ciphertext and context binding; avoids Binary serialisation quirks. Disconnect revokes the refresh token (ends the whole grant at Google and X).

- 2026-11-08: Lead approved the contract with edits: Lane H owns gateway.py wiring; scopes exactly gmail.send (+openid email) and tweet.write tweet.read users.read offline.access; refresh tokens encrypted like access tokens and a failed refresh = needs_connection; KMS/script dry-run unless --apply; out-of-row edits approved, minimal.
- 2026-11-08: provider_for_tool returns None for bluesky/linkedin/facebook so Lane H keeps the share-link handler there (agreed with Lane H).

- 2026-11-08: Lead (this session) wrote the plan instead of team-architect: the design was already settled while agreeing the interface with Lane H.
- 2026-11-08: Baseline origin/main 23fa467 has 66 failing Python tests (NameError `_scrape_domain_problem` in `agents_api.py`); C1 checks for *new* failures only. Not fixed here (out of scope), reported to the lead.
- 2026-11-08: KMS Encrypt with an EncryptionContext per staffer+provider (not Secrets Manager): one key, no per-user secret objects, and the context makes a ciphertext useless under another staffer.
- 2026-11-08: Only Gmail and X are connectable. LinkedIn/Facebook posting for other members or Pages needs app review; Bluesky has no OAuth client flow we can ship today. They show "not supported yet" honestly.
