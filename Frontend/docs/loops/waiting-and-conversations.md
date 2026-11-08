# Loop: waiting-and-conversations

Status: DONE
Profile: feature
Round: 1 / 4
Base commit: 4b63e5e
Branch: claude/chat-ux-huy-60b162

## Original Request

"The 4 waiting now can only open one at a time, it should show different ones awaiting and the user can select which one to review, also something create something that rank the awaiting based on the urgency and importance of the message (the user should still have the option to view all awaiting). Also ensure user can create mutiple tabs/session"

Lead's brief (2026-11-08): (1) "N waiting" opens a pickable list (popover on desktop, sheet on mobile) with
recipient/subject, category, age and an urgency badge; "next" inside the panel; "View all waiting". (2) Ranked by
default with a by-time toggle; a deterministic server scorer in Backend/app/routers (kind, age, urgency keywords
in the original email, repeat contact) returning urgency/score/reason; the agent's draft text never raises its
rank. (3) A conversation list (left rail on desktop, menu on mobile) with New conversation, title, status and
waiting count; switching changes chat, review list and identity line; build against Lane E's `GET /sessions` shape
in mock mode; two browser tabs on different conversations work independently.

## Desired Outcome

Staff see every waiting draft ranked by an explainable urgency, pick any one to review, and keep several
conversations side by side, each in its own tab if they like.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Server scorer and ranked /drafts | `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src /Users/tranquanghuy/Projects/aws-a10-hack-2026/HomeFront/.venv/bin/python -m pytest -q Backend/tests/test_draft_rank.py` | exit 0 |
| C2 | Waiting list: pick any, ranked/time order, urgency badge, this/all scope, Next in panel | `cd Frontend && npx vitest run src/components/WaitingList.test.tsx src/lib/waiting.test.ts src/components/ReviewPanel.test.tsx 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C3 | Conversations: list, new, switch; mock lists several open sessions | `cd Frontend && npx vitest run src/components/ConversationList.test.tsx src/pages/ConsoleConversations.test.tsx src/lib/mockApi.test.ts 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C4 | Backend suite | `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src /Users/tranquanghuy/Projects/aws-a10-hack-2026/HomeFront/.venv/bin/python -m pytest -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests` | exit 0 |
| C5 | Frontend types, build, suite | `cd Frontend && npx tsc --noEmit && npm run build && npx vitest run` | exit 0 |

## Not Checkable By Command

- 375 / 1280 px in mock mode: popover vs sheet, conversation rail vs menu, two tabs on two conversations. Lane F
  checks with browser screenshots; Huy clicks through. The real `GET /sessions` comes from Lane E.

## Plan

1. `Backend/app/routers/draft_rank.py` + `/drafts` ranked (done first, tests in `test_draft_rank.py`).
2. `lib/waiting.ts` (order, age), `components/WaitingList.tsx` (hook-free), ReviewPanel "Next".
3. `api.listSessions`, mock `GET /sessions` (several open, cap 5, titles, waiting), `lib/useSessions.ts`,
   `components/ConversationList.tsx` (hook-free), Console wiring (left rail ≥1280 px, menu below).

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Scorer | lead | Backend/app/routers/{assistant,draft_rank}.py, Backend/tests/test_draft_rank.py | C1, C4 | — | done |
| 2 | Waiting list | lead | Frontend/src/** | C2 | 1 | done |
| 3 | Conversations | lead | Frontend/src/** | C3 | — | done |

## Rounds

## Decisions

- Repeat contact is read from the original email's words ("third time", "again", "no reply"…): the store has no
  "emails by sender" query and the gateway/contracts are out of scope.
- Constituents can write "urgent" to move up the list; that only changes order, never what the agent may do.

### Round 1 — 2026-11-08
- Built: scorer test-first before this file existed (C1 therefore passed at round 0; it failed with
  ModuleNotFoundError before the scorer), waiting list + Next, conversations (mock GET /sessions, cap 5), CSS.
- Outcome checks: 5/5 (round-1.json). Frontend 109 tests, backend 407 passed / 3 skipped (+7 scorer tests).
- Browser (mock): 1280 px popover, "All conversations (5)" ranked, row 2 opens "2 of 5", Next → "3 of 5"; two tabs
  on two conversations stay independent (1 card vs 4); 375 px waiting sheet, conversation drawer, header row;
  scrollWidth = viewport.
- Known (pre-existing, mock only): the scripted mock run loops forever per open session and the console keeps the
  newest 100 events, so an older turn's disclosure can read "no actions" after a few minutes.
- Trial (lite) on 407dfe3: FALSIFIED on test proof. Survivors: switching conversations a no-op (F4), Next reopening
  the same draft (F5), the mock scorer without its to==sender check (F6). Also: urgent words English-only.

### Round 2 — 2026-11-08
- Built: Console tests that press the captured ConversationList onSelect and ReviewPanel onNext (child components
  mocked to capture props; no DOM needed); mock rankDraft tests; Vietnamese/Spanish urgent words on server + mock.
- Hand-run mutations: F4, F5 and F6 now each fail 1 test; files restored and diff-checked.
- Outcome checks: 5/5 (round-2.json). One earlier C4 run hit the 300 s limit; a rerun took 30.7 s with no slow test
  (408 passed, 3 skipped), so it was machine load, not the change.
- Decision: DONE. No second trial (cost); trial ACCEPT not claimed.
