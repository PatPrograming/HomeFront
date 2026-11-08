# Loop: console-round-6

Status: REVIEWING
Profile: feature
Round: 1 / 4
Base commit: 13659a0
Branch: claude/chat-ux-huy-60b162

## Original Request

Huy (relayed by the lead, 2026-11-08, after his click-through):
1. "When I write a new prompt, make sure that it doesn't delete or remove the old previous prompt"
2. "later it should run a research agent to do research and then it gives a draft, which allows the user to directly edit on it or prompt for edit, until the user is satisfied, in the session will show what social media platform the user want to post. The workflow like this should work for sending email as well."
3. "instead of naming agent 1,2,3, name the agent descriptively with its task or just research/social media/email agent"
4. "remove the access ends at"
5. "for the can send without asking, make a drop down or a button that opens up or a setting and make the main console chat session the main thing"

Lead's brief: root cause first; drafts under the reply that made them, never a later one; item 2 is the UI half
(editable draft card: text, platform chips or to/subject/body, Save edits, "make it shorter" → Draft N; approve stays
the review path), API agreed with Lane H; staff see descriptive agent names, raw ids admin-only; expiry kept but
not shown to staff; sending rules in a button/popover on every size, chat full width. 375 px, keyboard, screen
readers kept. Tests that fail if reverted. Lite /trial. Don't push.

## Root cause (item 1)

Sessions last one hour (`SESSION_TTL_SECONDS`). After that the console's next message silently starts a new
session (`send()` → `needsNewSession` → `sendInSession`) and the chat shows only the new sid's messages. At
4b63e5e (Huy's click-through) draft cards were also not filtered by session, so the old session's pending drafts
had no reply of their own on screen and fell back to the only one: Huy's second symptom. Server timestamps parse
fine (checked V8 with 6-digit fractions). Client fix: keep the old session's messages (and its drafts' session) in
this tab's history and show them above a "session renewed" divider. Server-side continuation proposed to Lane E.

## Desired Outcome

Earlier prompts never leave the conversation; drafts sit under the reply that made them; drafts can be edited in
place or revised by prompt; staff see "Research / Social media / Email agent" names, no expiry clock and a chat
that takes the full width with sending rules one click away.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Earlier prompts stay after a session renews; drafts stay under their own reply | `cd Frontend && npx vitest run src/pages/ConsoleRollover.test.tsx src/components/ReviewCards.test.tsx 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C2 | Editable draft card: edit in place, platforms/recipient shown, Save, revise → Draft N | `cd Frontend && npx vitest run src/components/DraftCard.test.tsx 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C3 | Descriptive agent names for staff, raw id and expiry admin-only | `cd Frontend && npx vitest run src/pages/ConsoleRoles.test.tsx src/components/AgentActivity.test.tsx src/lib/agentActivity.test.ts src/pages/Console.test.tsx 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C4 | Sending rules behind a button; chat full width | `cd Frontend && npx vitest run src/pages/ConsoleSendingRules.test.tsx 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C5 | Frontend types, build, full suite | `cd Frontend && npx tsc --noEmit && npm run build && npx vitest run` | exit 0 |

## Not Checkable By Command

- 375 / 1280 px in mock mode, keyboard and screen-reader labels: Lane F with browser screenshots; Huy clicks
  through. The real edit/revise routes (Lane H) and session continuation (Lane E).

## Plan

1. Rollover: Console keeps `mandate.history.<sid>` (earlier sessions' messages + sids) in sessionStorage;
   ChatPanel gets `renewedAfter`; drafts from the chain's sids; `draftsByReply` never places a draft from a
   running turn under an earlier reply.
2. Item 2 after Lane H's answer: `DraftCard` (editable in place, Save, revise, Draft N, platforms / recipient).
3. `agentKind()` names in the disclosure; IdentityLine without expiry or raw id.
4. Sending rules: `useToggle` + popover/sheet; rail removed; chat full width.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Failing tests | lead | test files | C1–C4 fail for the right reason | — | done |
| 2 | Rollover + placement | lead | Console.tsx, ChatPanel.tsx, ReviewCards.tsx | C1 | 1 | done |
| 3 | Names + identity line | lead | agentActivity.ts, AgentActivity.tsx, IdentityLine.tsx | C3 | 1 | done |
| 4 | Sending rules popover | lead | Console.tsx, Console.css, lib/useToggle.ts | C4 | 1 | done |
| 5 | Editable draft card | lead | DraftCard.tsx, api.ts, mockAssistant.ts, ChatPanel.tsx | C2 | Lane H API | done (proposed API, no answer yet) |

## Rounds

## Decisions
- The old ConsoleReview check "only the opened draft's body on the page" now looks inside the side panel only:
  draft cards in the chat show every draft's text on purpose (item 2). Same claim, narrower place.
- Lane H hasn't answered the API proposal yet; the card follows the proposal and the server's `editable` list.

### Round 1 — 2026-11-08
- Built: rollover history (Console, ChatPanel divider), placement for a running turn (ReviewCards), agentKind
  names + IdentityLine without expiry/raw id, Sending rules popover (useToggle) and no right rail, DraftCard +
  DraftCardView (edit in place, platform chips, Save edits, revise → Draft N), api + mock routes, CSS.
- Outcome checks: 5/5 (round-1.json); 166 frontend tests.
- Browser (mock, Maya, port 3227): aged the session card, sent a post: earlier prompt + reply stay above "Your
  assistant's access was renewed…", 4 email cards under the first reply and the post under the second; Edit +
  Bluesky + Save → "Draft 2 · Post on X, Bluesky"; "make it shorter" → "Revising…" → "Draft 3" shorter; disclosure
  "Email agent: Read email email-3"; identity "Your assistant acts for you, office rep-x"; Sending rules popover at
  1280 and full-screen sheet at 375; chat column 860 px; no sideways scroll at 375.
- Open: the renewed session still lists as its own conversation in the sidebar until Lane E chains sessions.
