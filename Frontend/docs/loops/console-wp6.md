# Loop: console-wp6

Status: DONE
Profile: feature
Round: 2 / 4
Base commit: 839c456
Branch: claude/console-huy-4ee5f8

## Original Request

Build Mandate's staff and admin console in `Frontend/` (WP6). Deliver: (1) Cognito login replacing Supabase: managed-login redirect with authorization code + PKCE against the `staff-web` client (config from `VITE_COGNITO_DOMAIN`, `VITE_COGNITO_CLIENT_ID`, `VITE_COGNITO_REDIRECT_URI`), tokens kept in memory/sessionStorage, logout, `RequireAuth` using the Cognito session, group read from the access token `cognito:groups`; remove `@supabase/supabase-js` and its files. (2) Staff view: 'Start assistant' calls `POST /api/v1/sessions` and shows the identity card (agent id, acting for, office, mode, allowed tools, sid to hand to the agent, expiry countdown); a live feed polling `GET /api/v1/events?since=` every 1 s showing time, tool, resource, ALLOW/DENY, reason, policy ids, ATLAS ids and a guardrail flag badge, newest first. (3) Admin view, visible and routable only for group `admin`: an Offboard button per staffer calling `POST /api/v1/staff/{id}/offboard` with a confirm step, and the audit log (same feed, full history). (4) A mock mode (`VITE_MOCK_API=1`) that serves `packages/mandate-contracts/samples/events.json` plus generated events and fake session/offboard responses, so the whole demo flow can be clicked through before lane B exists; mock mode must be impossible to leave on by accident in a production build. (5) Types from `packages/mandate-contracts/ts/index.ts` (import path or a copied module checked against it), all API calls through `src/lib/api.js` with the Bearer token; no AWS SDK in the browser. (6) Works at 375 px wide with no horizontal scroll; accessible labels on icon buttons; a plain-words `Frontend/README.md` update.

## Desired Outcome

A teammate runs `VITE_MOCK_API=1 npm run dev` in `Frontend/`, signs in as Maya, starts the assistant, watches ALLOW
and DENY events stream in, signs in as the Chief of Staff, offboards Maya, and sees the next call DENIED; with
`VITE_MOCK_API` unset, the same screens log in through Cognito managed login (code + PKCE) and talk to lane B's API.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Clean install and production build | `cd Frontend && npm ci --no-audit --no-fund && npm run build` | exit 0 |
| C2 | No Supabase or AWS SDK in the frontend | `! grep -rniE '@supabase\|aws-sdk\|@aws-sdk' Frontend/src Frontend/package.json Frontend/vite.config.js` | exit 0 |
| C3 | PKCE helpers (verifier, S256 challenge, authorize URL, token exchange body) | `cd Frontend && npx vitest run src/lib/pkce.test.js` | exit 0 |
| C4 | Role gate (admin-only routes, groups from access token) | `cd Frontend && npx vitest run src/lib/roles.test.js` | exit 0 |
| C5 | Events cursor and merge logic (since=, newest first, no duplicates) | `cd Frontend && npx vitest run src/lib/events.test.js` | exit 0 |
| C6 | api.js sends the Bearer token and the lane B routes | `cd Frontend && npx vitest run src/lib/api.test.js` | exit 0 |
| C7 | Mock mode refuses a production build | `cd Frontend && ! VITE_MOCK_API=1 npx vite build --emptyOutDir --outDir /tmp/mandate-mock-build-check` | exit 0 |
| C8 | Production bundle contains no mock code | `cd Frontend && npx vite build --emptyOutDir --outDir /tmp/mandate-prod-build-check >/dev/null && ! grep -rq MANDATE_MOCK_API_MODULE /tmp/mandate-prod-build-check` | exit 0 |
| C9 | README explains Cognito and mock mode | `grep -q VITE_COGNITO_DOMAIN Frontend/README.md && grep -q VITE_MOCK_API Frontend/README.md && grep -q cognito:groups Frontend/README.md` | exit 0 |

## Not Checkable By Command

- Click-through of the 4 demo steps in mock mode at 375 px and at desktop width (sign in as Maya, Start assistant
  shows the identity card, feed shows ALLOW and DENY with policy, ATLAS and guardrail badges, sign in as Chief of
  Staff, Offboard Maya with confirm, next event is DENY "session revoked / delegator inactive"). Checked by the lead
  in the browser pane with screenshots; the trial falsifier may repeat it.
- No horizontal scroll at 375 px: checked in the browser pane (`document.documentElement.scrollWidth <= 375`).
- Real Cognito managed login round trip: needs lane A's user pool and the `staff-web` client. Left for Huy.
- Real lane B API: left for integration 2.

## Plan

1. Config + auth module: `src/lib/pkce.js` (pure helpers), `src/lib/auth.js` (redirect, callback exchange,
   sessionStorage, logout, mock sign-in), `AuthProvider.jsx` rewritten, `/auth/callback` page. Done when C3.
2. Role gate: `src/lib/roles.js` + `RequireAuth` with an optional `role` prop. Done when C4.
3. `api.js` on the Cognito token with the lane B routes, plus `mockApi.js` (dev-only dynamic import, samples +
   scripted generated events, fake session/offboard), vite.config guard. Done when C6, C7, C8.
4. Events cursor logic `src/lib/events.js` + `useEventFeed` hook (1 s poll, no overlapping requests). Done when C5.
5. Staff view `/console`: Start assistant, identity card with countdown, live feed. Admin view `/admin`: staffer
   list with Offboard + confirm, full audit log. Navbar shows Admin only for admins; navbar fits 375 px.
6. Remove Supabase (`supabase.js`, dependency, vite defines, Login form). Done when C1, C2.
7. README. Done when C9.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Vitest + PKCE + auth module | lead | package.json, package-lock.json, vite.config.js, src/lib/{pkce,auth}.js, src/lib/pkce.test.js, src/lib/AuthProvider.jsx, src/pages/{Login,AuthCallback}.jsx | C3 | — | done |
| 2 | Role gate | lead | src/lib/roles.js, src/lib/roles.test.js, src/components/RequireAuth.jsx | C4 | 1 | done |
| 3 | api.js + mock layer + build guard | lead | src/lib/{api,mockApi}.js, src/lib/api.test.js, vite.config.js | C6 C7 C8 | 1 | done |
| 4 | Events cursor + feed hook | lead | src/lib/events.js, src/lib/events.test.js, src/lib/useEventFeed.js | C5 | 3 | done |
| 5 | Staff + admin views, nav | lead | src/pages/{Console,Admin,Account}.jsx, src/pages/Console.css, src/components/{EventFeed,IdentityCard}.jsx, src/components/Navbar/*, src/App.jsx, src/config.js | Not-checkable click-through | 2 3 4 | done |
| 6 | Remove Supabase | lead | src/lib/supabase.js, package.json, package-lock.json, vite.config.js | C1 C2 | 5 | done |
| 7 | README | lead | README.md | C9 | 6 | done |

## Rounds

### Round 1 — 2026-11-07
- Built: tasks 1–7, commits dde200f b2fa981 9d0f206 a2a1ced, then 0d3eacf (vitest 3.2.7 after the security review).
- Outcome checks: 9/9 pass; fixed [C2, C3, C4, C5, C6, C7, C9], regressed [] (scoreboard: console-wp6.rounds/round-1.json).
- Review: team-reviewer APPROVE, 0 Critical/High; Medium: no fetch timeout (a stalled API freezes the feed while it says "Live"), /account unreachable for a user with no group. Security: `security:comment` (dev-only Vitest/tinypool/esbuild advisories). Trial (full): falsifier FALSIFIED 3b, advocate PARTIAL-strong. Judge not run: the falsified item was reproduced from the code (Admin.jsx guesses "Offboarded" from the newest event; the contract sample's newest maya event is a revoked DENY), so a fix round is certain; the re-trial rules on the fixed state.
- Gap list for next round: (1) 3b Offboard button hidden on fresh data [falsifier]; (2) fetch timeout [reviewer M1]; (3) /account for users with no group [reviewer M2]; (4) admin navbar overflows 1201–1330 px [falsifier]; (5) identity card keeps counting after revoke [falsifier]; (6) README claims a bundle-grep script that is only a loop check [falsifier + advocate]; cheap Lows: card lost on reload, mock tabs race / duplicate ids, Cognito login lands admins on /console.
- Decision: next round because the gap list is non-empty and round 1 fixed 7 checks.

### Round 2 — 2026-11-07
- Built: round-1 gap list (commits a7b5d27, 97e025c), plus lead requests: Account reads GET /me (a7b5d27), real Cognito values in .env.example and sign-in callback at the site root, api.home() restored (1062633), product name HomeFront in UI copy (b1a3709).
- Outcome checks: 9/9 pass at HEAD b1a3709 (scoreboards round-2.json and round-3.json), no regressions.
- Review: fresh falsifier INCONCLUSIVE (nothing broke; 6/6 mutations killed; real Cognito and lane A/B APIs UNVERIFIED by the no-AWS rule). Judge: ACCEPT, nothing blocking; claim 10 (security verdict) marked UNBACKED only because the security report was not handed to the judge (verdict.py printed security:comment). Optional follow-ups from the judge: feed grid overflows 1001–1085 px (8-column minimums), clear mandate.card on sign-out, README says DEV is never true in vite build (wrong for --mode development; the config guard still refuses it).
- Gap list: empty (no Critical/High).
- Decision: DONE. Lead stopped new rounds for the integration merge.

## Decisions

- 2026-11-07: Loop file lives in `Frontend/docs/loops/` instead of `docs/loops/`, because lane D may only write in `Frontend/**` (docs/LANES.md).
- 2026-11-07: No approval stop. The lead session's brief prescribed the request, the task split and the outcome checks; milestones (17:00/18:00) leave no room for a round trip. Huy can still edit the checks; the loop records it.
- 2026-11-07: The lead (this session) writes the plan and the code instead of team-architect / team-implementer: one owner, seven small dependent tasks in one folder, full context already loaded. Review stays independent: /trial (full depth, auth) runs with the original request only.
- 2026-11-07: Contracts are imported straight from `packages/mandate-contracts/ts/index.ts` (no copy, so no drift); Vite `server.fs.allow` includes the repo root.
- 2026-11-07: Plain `fetch` + Web Crypto for PKCE, no OIDC library: smallest code a teammate can explain (see ~/.claude/knowledge/cognito-spa-pkce-2026.md).
- 2026-11-07: Round 0 scoreboard: 2/9 pass. C1 (build) and C8 (no mock code in the bundle) pass today by design: they are regression guards that must keep passing once Supabase is gone and the mock module exists. The behavior checks C2–C7 and C9 fail today.
