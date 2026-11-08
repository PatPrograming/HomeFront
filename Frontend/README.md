# Frontend: the Mandate console

React 18 + Vite. The staff console and the Chief of Staff's admin page for Mandate (WP6). It shows the demo:
Maya signs in and starts her AI assistant, every tool call shows up as ALLOW or DENY, the Chief of Staff
offboards Maya, and the assistant's next call is denied.

## Run it

Try the whole demo with no backend and no Cognito (mock mode):

```bash
cd Frontend
npm ci
VITE_MOCK_API=1 npm run dev     # http://localhost:3000
```

Sign in as Maya, press **Start assistant**, and watch the feed. Open a second tab, sign in as the Chief of Staff,
and press **Offboard** next to Maya. Both tabs share the fake data, so Maya's tab shows the DENY a second later.
"Reset demo data" on the sign-in page starts over.

Against the real API and Cognito (lane A and lane B):

```bash
./scripts/run-gateway.sh        # FastAPI on 127.0.0.1:8080 (repo root)
cd Frontend && npm run dev      # /api is proxied to :8080
```

Other commands: `npm test` (unit tests, Vitest), `npm run build` (production build into `dist/`).

## Settings (repo-root `.env`, read by `vite.config.js`)

| Variable | What it is |
|---|---|
| `VITE_COGNITO_DOMAIN` | The user pool's managed-login domain, e.g. `mandate-xyz.auth.us-east-1.amazoncognito.com` |
| `VITE_COGNITO_CLIENT_ID` | The `staff-web` app client id (public client, no secret) |
| `VITE_COGNITO_REDIRECT_URI` | Where Cognito sends you back, e.g. `http://localhost:3000/auth/callback`. Must be in the client's callback URLs. Defaults to `<this origin>/auth/callback`. The sign-out URL `<this origin>/` must be in the client's sign-out URLs. |
| `VITE_API_BASE` | Optional. API origin + `/api/v1` when not using the dev proxy |
| `VITE_MOCK_API` | `1` turns on mock mode, dev server only |

None of these are secrets: everything prefixed `VITE_` ends up in the browser.

## How sign-in works (Cognito, authorization code + PKCE)

All of it is in `src/lib/pkce.js` (pure helpers) and `src/lib/auth.js` (about 100 lines, plain `fetch`, no library).

1. **Sign in** sends the browser to Cognito's own login page (`/oauth2/authorize`). Before leaving, the app makes a
   random secret, the *verifier*, keeps it in `sessionStorage`, and sends only its SHA-256 hash, the *challenge*.
2. Cognito sends the browser back to `/auth/callback?code=…&state=…`. The app checks `state` matches, then posts the
   code **and the verifier** to `/oauth2/token`. Cognito hashes the verifier and compares it with the challenge, so a
   stolen code is useless without the secret that never left this tab.
3. The tokens are kept in memory and `sessionStorage` (this tab only, gone when it closes), never `localStorage`.
   There is no refresh token use: when the access token expires the staffer signs in again.
4. The staffer's group comes from the access token's `cognito:groups` claim: `staffer` (Maya) or `admin` (Chief of
   Staff). The browser does not verify the token's signature; it only uses the claims to choose what to show. The API
   verifies every token against Cognito's keys and checks the group again on each admin call.
5. **Sign out** clears the tab and calls Cognito's `/logout`.

## Pages

| Path | Who | What |
|---|---|---|
| `/login` | anyone | Sends you to Cognito (or, in mock mode, lets you pick Maya or the Chief of Staff) |
| `/auth/callback` | anyone | Finishes sign-in |
| `/console` | `staffer` or `admin` | **Start assistant** (`POST /api/v1/sessions`), the identity card, the live feed |
| `/admin` | `admin` only | **Offboard** each staffer (`POST /api/v1/staff/{id}/offboard`, with a confirm step) and the full audit log |
| `/account` | signed in | Your username, email, groups and sign-out |
| `/monitoring` | signed in | The old in-memory gateway demo, kept until lane B replaces those routes |

`src/components/RequireAuth.jsx` guards the routes with `gate()` from `src/lib/roles.js`. The Admin link in the
navbar only appears for admins, and a staffer who types `/admin` gets "Not allowed".

## How it talks to the backend

- Every call goes through `src/lib/api.js`, which adds `Authorization: Bearer <Cognito access token>`.
- The browser never calls AWS and has no AWS SDK. The API does the AWS work.
- **Live feed:** `src/lib/useEventFeed.js` calls `GET /api/v1/events?since=<cursor>` every second. The first call has
  no cursor; each answer's `next` (the last event's sort key, `<ts>#<id>`) becomes the next cursor. An empty answer
  keeps the old cursor. New events are merged by id (no duplicates), newest first (`src/lib/events.js`).
- The identity card counts down to the session's `expiresAt` and shows which tools are allowed and which are never
  allowed (from the contract's tool list).
- Types, tool names and sample events come straight from `packages/mandate-contracts` (`ts/index.ts`,
  `samples/events.json`), so they cannot drift from the backend's contract.

## Mock mode, and why it cannot ship

`VITE_MOCK_API=1` swaps the API for `src/lib/mockApi.js`: the contract's sample events, a scripted agent run
(normal work, a poisoned email flagged by the guardrail, then a bulk export, an email to an outsider and a campaign
post, all denied), fake session and offboard answers, and fake sign-in as Maya or the Chief of Staff. Three things
keep it out of production:

1. `src/lib/mode.js` turns mock mode on only when `import.meta.env.DEV` is true, which is never the case in
   `vite build`, so the bundler removes the mock code.
2. `vite.config.js` stops the build with an error if `VITE_MOCK_API=1` is set.
3. A check greps the production bundle for the mock module's marker.

## Tests

`npm test` runs `src/lib/*.test.js`: the PKCE helpers (including the RFC 7636 test vector), the role gate, the
events cursor logic, and `api.js` (routes and the Bearer header).
