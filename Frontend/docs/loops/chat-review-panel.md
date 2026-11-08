# Loop: chat-review-panel

Status: DONE
Profile: feature
Round: 1 / 4
Base commit: 9c44fee
Branch: claude/chat-ux-huy-60b162

## Original Request

"Check how claude functions and see what's needed and can be useful if added. Some things I see are: the review, instead of open it as a list on the side, the user can click a button in the chat session and it would open the one the user want to review on the side"

Lead's brief (2026-11-08): inline card per draft in the chat; Review opens that one draft in a right-side panel
(recipient read-only, editable text, Approve / Approve and allow for this session / Reject), full-screen sheet on
mobile; remove the always-on review list from the rail; "N waiting" badge opens the next one; keep every security
rule. Briefly research Claude's chat UX and rank what fits; tonight only cheap, safe items (Stop waiting, copy
message, friendly "Can't reach HomeFront's server. Retrying…" instead of the raw browser error).

## Desired Outcome

A staffer reviews each draft from the conversation that produced it, in a side panel that shows only that draft,
with the recipient locked; the waiting count is always visible; connection failures read in plain words.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Inline review card per draft; clicking it opens that draft; panel shows it with a read-only recipient | `cd Frontend && npx vitest run src/components/ReviewPanel.test.tsx src/components/ReviewCards.test.tsx 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C2 | Console: badge count, panel open for the chosen draft, no review list in the rail | `cd Frontend && npx vitest run src/pages/ConsoleReview.test.tsx 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C3 | Friendly connection message instead of "Failed to fetch" | `cd Frontend && npx vitest run src/lib/errors.test.ts 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C4 | Types, build and the full suite are green | `cd Frontend && npx tsc --noEmit && npm run build && npx vitest run` | exit 0 |

## Not Checkable By Command

- 375 / 1024 / 1280 px in mock mode: inline cards, side panel at ≥1024 px, full-screen sheet below, no sideways
  scroll. Lane F checks with browser screenshots; Huy clicks through.

## Plan

1. `lib/errors.ts`: `connectionMessage(error, retrying)`.
2. `components/ReviewPanel.tsx`: one draft, recipient read-only, editable text, the three actions (replaces ReviewQueue).
3. `components/ReviewCards.tsx`: hook-free inline cards for one turn's drafts; `lib/useOpenDraft.ts` holds which is open.
4. `ChatPanel.tsx`: cards under each reply, Stop waiting, Copy message, friendly errors.
5. `Console.tsx` + CSS: "N waiting" badge, side panel / mobile sheet, rail keeps autonomy only.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Failing tests | lead | the four test files | C1–C3 fail for the right reason | — | done |
| 2 | Build | lead | Frontend/src/** | C1–C4 | 1 | done |

## Rounds

## Decisions

- No jsdom in the project; rather than add a dependency, the click path is tested by calling the hook-free
  `ReviewCards` element's onClick, and Console's open state sits in a stub-able `useOpenDraft` hook.

### Round 1 — 2026-11-08
- Built: errors.ts, ReviewPanel (replaces ReviewQueue), ReviewCards + useOpenDraft, ChatPanel (cards, Stop, Copy,
  friendly errors), Console (badge, side panel / sheet, rail keeps autonomy), CSS. Autonomy switch text "Review"
  renamed "Asks first" / "Always asks" so it doesn't read like the cards' Review button.
- Outcome checks: 4/4 (round-1.json); 90 tests.
- Browser (mock, Maya): 1280 px cards + panel, Approve marks the card Sent and focus returns to the composer,
  Reject marks Rejected, badge opens the next, Stop then a late reply shows; 1024 px side panel; 375 px full-screen
  sheet, Esc closes; scrollWidth equals the viewport at all three.
