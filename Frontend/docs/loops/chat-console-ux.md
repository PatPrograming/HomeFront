# Loop: chat-console-ux

Status: DONE
Profile: feature
Round: 3 / 4
Base commit: f7aeb8c
Branch: claude/chat-ux-huy-60b162

## Original Request

"When the agent is working, beside showing working, allow the user to click and expand to check what agents are being created and what permissions are they allowed.(should this be expanded in the chat console or should it open in another tab?)
Also make the interface more user friendly, look at big ones like claude or chatgpt, make the less blocky
All the logs and everything should remain but it should be for user with admin/tech user only, general user don't need to see and understand the logs and how the agent works."

Lead decisions (from the lead session, 2026-11-08): expand inline in the chat; staff see plain words only, admins also
get the feed, Cedar/ATLAS ids, session ids and the raw identity card; chat-first layout like Claude/ChatGPT; keep
all current behaviour (first message starts the session, 20 s not-connected notice, Revoked, review actions, mock mode).

## Desired Outcome

On `/console`, every working/finished assistant turn has an expandable line that tells a staffer, in plain words,
which agent acts for them, what it may do this session and what it did in that turn. Staff never see policy ids,
ATLAS ids, session ids or the decision feed; admins do. The page reads as a chat app, not a grid of boxes.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Role gate: staff never sees Cedar/ATLAS ids, sid or feed; admin does | `cd Frontend && npx vitest run src/pages/ConsoleRoles.test.tsx -t "role gate" 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C2 | Inline disclosure shows plain-word permissions and actions from a fixture | `cd Frontend && npx vitest run src/components/AgentActivity.test.tsx src/lib/agentActivity.test.ts 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C3 | Revoked path visible to staff and admin with "Start a new conversation" | `cd Frontend && npx vitest run src/pages/ConsoleRoles.test.tsx -t "revoked" 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C4 | Types, build and the full suite are green | `cd Frontend && npx tsc --noEmit && npm run build && npx vitest run` | exit 0 |

## Not Checkable By Command

- Screen widths 375 / 768 / 1024 / 1280 px with no sideways scroll, for Maya and the Chief of Staff, in mock mode
  (`VITE_MOCK_API=1`, free port): checked by Lane F with browser-pane screenshots at 375 and 1280 px; Huy clicks
  through before the demo.
- "Looks like Claude/ChatGPT, less blocky": judged by Huy.

## Plan

1. `src/lib/agentActivity.ts`: plain-word labels for tools, one line per decision event, and the events of one turn.
2. `src/components/AgentActivity.tsx`: the `<details>` disclosure ("Working…" / "Done · n actions"): who acts,
   what it may do, what it did; admins get a "Technical details" link to `/admin`.
3. `ChatPanel.tsx`: plain assistant text, soft user bubbles, disclosure per turn, sticky round composer with icon send.
4. `Console.tsx`: role gate with `isAdmin`; staff get `IdentityLine`, admins also `IdentityCard` + Live decisions;
   right rail (review + autonomy) that is a drawer under 1024 px.
5. `Console.css`: chat-first restyle; reduced motion kept.
Tests first: `ConsoleRoles.test.tsx`, `AgentActivity.test.tsx`, `agentActivity.test.ts`.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Failing tests | lead | the three test files | C1–C3 fail for the right reason | — | done |
| 2 | Activity lib + disclosure | lead | agentActivity.ts, AgentActivity.tsx | C2 | 1 | done |
| 3 | Role gate + layout | lead | Console.tsx, ChatPanel.tsx, IdentityLine.tsx, Console.css, Console.test.tsx | C1, C3, C4 | 2 | done |

## Rounds

## Decisions

- No approval stop: the lead session set the checks and decisions in the brief; Huy reviews the lane report.
- Lead builds in-session instead of spawning test-author/implementer agents: a frontend-only change in five files under
  a demo deadline; tests are still written first and the trial runs at the end.

### Round 1 — 2026-11-08
- Built: tasks 1–3, commits 8561c16, 947bb88
- Outcome checks: 4/4 pass; fixed [C1, C2, C3, C4] (scoreboard: chat-console-ux.rounds/round-1.json)
- Trial (lite): falsifier FALSIFIED requirement "allowed tools and any session grants": the disclosure ignored
  "Approve and allow for this session" and still said replies wait for review.
- Gap list: 1. session grants missing from "What it may do this session" (falsifier row 2).
- Decision: next round.

### Round 2 — 2026-11-08
- Built: grants kept per session in the console (sessionStorage, the API has no list route), passed from
  ReviewQueue `onGrant` to the disclosure; `permissionLines` takes grants. Tests added first (failed, then pass).
- Outcome checks: 4/4 pass (round-2.json); 72 tests. Live mock repro of the falsifier's steps now shows
  "Sends case status updates without asking (this session only)".
- Decision: re-trial (lite).
- Re-trial (lite) on ee02325: FALSIFIED again on evidence. The mutation `grants: []` in Console.tsx survived every
  test, and an Enter-to-send stall appeared in a browser run with stale mock localStorage.

### Round 3 — 2026-11-08
- Built: ConsoleRoles test "Console session grants" (this session's grants appear in the disclosure, other sessions'
  don't). Re-ran the surviving mutation by hand: it now fails 1 test; file restored.
- Enter-to-send: reproduced on a clean origin at 375 px with real keystrokes; two messages sent by Enter, both
  answered, scrollWidth 375. The trial's stall came from a stopped mock session in stale localStorage (pre-existing
  mock behaviour: a new session in another tab stops the old one), not from this change.
- Outcome checks: 4/4 pass (round-3.json). 73 tests.
- Decision: DONE. Every FALSIFIED item is fixed with a killing test or traced to stale mock state; no third
  trial was run (cost), so "trial ACCEPT" is not claimed.
