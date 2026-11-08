# Loop: conversation-sidebar

Status: DONE
Profile: feature
Round: 1 / 4
Base commit: 34d53c6
Branch: claude/chat-ux-huy-60b162

## Original Request

"I want a button for new session, search, in left side bar can open and collapse, also each session it automatically name something short suits to the user main goal in the session and similar to claude, they can pin, rename, group, archive, delete, etc"

Lead's brief (2026-11-08): UI only (`Frontend/**`); Lane E builds auto titles, `PATCH /api/v1/sessions/{sid}`
(rename/pin/group/archive), soft `DELETE`, `GET /api/v1/sessions?q=&archived=1` pinned-first with groups; build
against that in mock mode. Claude-style left sidebar: New conversation at the top; search (as you type, `?q=`);
collapse/expand remembered per browser (localStorage in try/catch), slide-over drawer on mobile; sections Pinned,
groups (collapsible), Recent; each item: auto title, waiting count, status; "⋯" menu: Rename (inline), Pin/Unpin,
Move to group (pick or type new), Archive, Delete (confirm: hidden, office audit record kept); Archived view to
restore; Cmd/Ctrl+K focuses search; keyboard-reachable menu; accessible labels.

## Desired Outcome

Staff manage many conversations the way they do in Claude: find, name, pin, group, archive and delete them from a
sidebar that gets out of the way when collapsed, without losing the office's audit trail.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Sidebar renders sections, menu, rename, delete confirmation, collapsed and archived views; its wrapper sends actions to the API, saves collapse, leaves a removed open conversation; search sends q | `cd Frontend && npx vitest run src/components/Sidebar.test.tsx src/components/SidebarView.test.tsx src/lib/useSessions.test.tsx 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C2 | Sections, menu actions call the right API, collapse persists (and survives blocked storage) | `cd Frontend && npx vitest run src/lib/sidebar.test.ts 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C3 | Mock API: search, rename, pin, group, archive, restore, soft delete, short auto titles | `cd Frontend && npx vitest run src/lib/mockApi.test.ts -t "sidebar" 2>&1` | stdout matches /Tests\s+\d+ passed/ |
| C4 | Frontend types, build, full suite | `cd Frontend && npx tsc --noEmit && npm run build && npx vitest run` | exit 0 |

## Not Checkable By Command

- 375 / 1280 px in mock mode (expanded, collapsed, drawer, menu, rename, confirm dialog), keyboard: Lane F with
  browser screenshots; Huy clicks through. The real routes come from Lane E.

## Plan

1. `api.ts`: listSessions(q, archived), updateSession(sid, patch), deleteSession(sid); SessionSummary gains
   pinned, group, archived.
2. Mock: `?q=`, `?archived=1`, `PATCH`, soft `DELETE`, pinned-first, short auto titles.
3. `lib/sidebar.ts`: sections, runAction, loadCollapsed/saveCollapsed.
4. `components/Sidebar.tsx`: `SidebarView` (hook-free) + `Sidebar` (state, Cmd/Ctrl+K); replaces ConversationList.
5. Console wiring + CSS (expanded 260 px, collapsed 56 px strip ≥1280 px, drawer below).

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Failing tests | lead | the three test files | C1–C3 fail for the right reason | — | done |
| 2 | Build | lead | Frontend/src/** | C1–C4 | 1 | done |

## Rounds

## Decisions
- Hook-free `SidebarView` + stateful `Sidebar`, so tests press the real buttons and inputs without a DOM.
- Search requests can overlap while typing; only the newest answer is shown.
- Rename starts with the whole title selected (typing replaces it).

### Round 1 — 2026-11-08
- Built: api (listSessions q/archived, updateSession, deleteSession, DELETE method), mock routes + short auto titles,
  lib/sidebar.ts, Sidebar.tsx (replaces ConversationList), Console wiring, CSS.
- Outcome checks: 4/4 (round-1.json); 139 frontend tests.
- Browser (mock, Maya), real keys where it matters: Ctrl+K focuses search; typing "case" leaves "Look up case 7";
  menu opens on Rename, ArrowDown → Pin, Enter pins (Pinned section); Move to group + typed "Roads" + Enter makes a
  "Roads (1)" group; Rename typed over the selected title; Delete shows the confirmation (hidden, audit kept) and
  removes it; Archive hides it (and closes it if open), Archived lists it, Restore brings it back; collapse
  survives reload; 375 px drawer + menu, Escape closes only the menu; drawer stays full when desktop is collapsed.
- Found, for Lane E: a deleted conversation's pending drafts still count as waiting.
- Trial (lite) on 128a88b: FALSIFIED on test proof. Survivors: wrapper skipping runAction (M3), no onRemoved (M4),
  no saveCollapsed on toggle (M5), useSessions dropping q (M6), api using the wrong param name (M7), the menu's
  Delete deleting without asking (M8). Cosmetic: the "⋯" menu clipped at the bottom of a short sidebar.

### Round 2 — 2026-11-08
- Built: `SidebarView.tsx` split out so `Sidebar.test.tsx` renders the real wrapper with the view's props captured
  (actions → api, onRemoved, collapse saved, drawer forces expanded); `useSessions.test.tsx` (reload asks with q and
  archived); api tests for `?q=&archived=1`, PATCH and DELETE; a view test that the menu's Delete only asks.
  Sidebar column gets bottom room so the menu isn't clipped (menu bottom 534 px < rail bottom 614 px).
- Hand-run mutations M3–M8: each now fails 1–2 tests; files restored.
- C1 widened to include the wrapper, view and hook tests (stricter, not looser).
- Outcome checks: 4/4 (round-2.json); 149 frontend tests.
- Decision: DONE. No second trial (cost); trial ACCEPT not claimed.
