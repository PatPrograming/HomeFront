# Loop: stop-task

Status: REVIEWING
Profile: feature (auth-relevant: owner-only revoke route)
Round: 1 / 4
Base commit: 0457fb7
Branch: Stop-Task-Huy (from claude/chat-huy-lane-e-fb8e00)

## Original Request

Run `/converge`. Goal: a staffer can revoke their own running task from the Your assistant chat, mid-task.

Today only an admin can cut access (offboard in `Frontend/src/pages/Admin.tsx` → `Backend/app/routers/staff.py`, which revokes every session of that staffer). There is no staffer-side revoke.

Build:
1. An owner-only `POST /api/v1/sessions/{sid}/revoke` (staff Cognito token; owner check; idempotent; audited as an event with ATLAS labels). It marks the session revoked in the `mandate` DynamoDB table, so the next `guarded()` check and the next mandate mint fail closed (same path as offboarding), and the auto-start supervisor stops that session's agent.
2. In the chat: a visible **Stop this task** control while the agent is working, and typing a stop phrase ("stop", "cancel", "revoke this task") is intercepted by the CONSOLE, which calls the revoke route itself. Never route the stop request through the agent: a compromised agent could ignore it. Confirm in the chat: "Stopped. The assistant can do nothing more in this conversation." Offer Start a new conversation.
3. Tests that fail if the guard is removed: another staffer can't revoke someone else's session; the agent's mandate token can't call the route; after revoke the next tool call is DENY within one call; an agent message containing "stop" does not trigger anything (only the staffer's own typed message does).

## Desired Outcome

While the assistant is working, the staffer clicks Stop this task or types "stop", and the very next tool call or mandate mint for that conversation is denied, its agent process ends, the stop is audited, and the chat says so with a way to start over.

## Outcome Checks

Run from the worktree root with AWS credentials stripped (`env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN`).

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Revoke route behaviour and guards | `env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src ../../../.venv/bin/python -m pytest -q Backend/tests/test_stop_task.py` | stdout contains 10 passed |
| C2 | Full backend + packages suite | `env -u AWS_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src ../../../.venv/bin/python -m pytest -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests packages/identity-gateway/tests` | exit 0 |
| C3 | Console intercepts stop phrases, agent text never triggers | `npm --prefix Frontend exec -- vitest run src/pages/ConsoleStop.test.tsx src/components/ChatPanel.test.tsx` | exit 0 |
| C4 | Frontend types | `npm --prefix Frontend exec -- tsc --noEmit -p Frontend` | exit 0 |
| C5 | Frontend suite | `npm --prefix Frontend exec -- vitest run --root Frontend` | exit 0 |

Round 0 (before the change): C1 and C3 fail (no route, no `stopTask.ts`, no `ConsoleStop.test.tsx`).

Guard mutations run by hand in round 1, each restored afterwards: dropping the owner check fails 2 tests in C1; replacing the staff login dependency fails all 10; skipping the revoked write fails 3 (next call DENY, no new mandate, audited once); deleting the console's stop-phrase intercept fails 5 tests in C3.

## Not Checkable By Command

- Real Cognito + real DynamoDB + real auto-started agent: a live click of Stop this task during a real agent run. Huy, on the integration stack.
- Mock-mode browser check (done by the lane, round 1): Stop this task shows while working at 375px; typed "stop" never appears as a message; the chat shows the Stopped confirmation with Start a new conversation; header reads "Revoked: you stopped this task", also after a reload; no horizontal scroll.

## Plan

1. Route `POST /sessions/{sid}/revoke` in `Backend/app/routers/sessions.py`: reuse `_own_conversation` (owner-only, 404 otherwise, admins included), set status revoked once, write one `DecisionEvent` (tool `session_revoke`, DENY, reason "session revoked / delegator inactive", ATLAS from the enricher's `revoke` event), call `supervisor.stop(sid)`. Done when C1 passes.
2. Console: `lib/stopTask.ts` (whole-message stop phrases), `api.revokeSession`, Console `send()` intercepts phrases before `chat.send`, ChatPanel shows Stop this task and the confirmation; mock API route. Done when C3–C5 pass.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Revoke route + tests | lead | `Backend/app/routers/sessions.py`, `Backend/tests/test_stop_task.py` | C1, C2 | – | done |
| 2 | Chat stop control + intercept + tests | lead | `Frontend/src/lib/{stopTask,api,mockApi}.ts`, `Frontend/src/pages/Console.{tsx,css}`, `Frontend/src/pages/ConsoleStop.test.tsx`, `Frontend/src/components/{ChatPanel,ChatPanel.test,IdentityLine,IdentityCard}.tsx` | C3–C5 | 1 | done |

## Rounds

### Round 1 — 2026-11-08
- Built: tasks 1–2.
- Outcome checks: C1 10 passed; C2 832 passed, 3 skipped; C3–C5 118 frontend tests pass, tsc clean.
- Review: lite /trial (see Decisions / report).

## Decisions

- Approval gate skipped: the lead session's brief listed the exact route, UI copy and the four guard tests; the lane treats that brief as the approved outcome contract.
- The lane built tasks itself instead of spawning team agents: two small tasks, test-first, mutation-checked.
- Audit event reuses the existing `DecisionEvent` shape (decision DENY, reason "session revoked / delegator inactive", tool `session_revoke`) instead of adding a new reason, because Lane E is editing `mandate_contracts/events.py` now.
- Admins get 404 on this route too: it is the staffer's own stop; admins already have Offboard.
- Stop phrases match only a whole message ("stop", "cancel", "revoke this task", "stop this task", "cancel this task", case and trailing ./! ignored), so "stop emailing Jane" is still a task for the agent.
- The revoked header names who stopped it ("you stopped this task") instead of always blaming the Chief of Staff; the stopped sid is kept in sessionStorage so this survives a reload.
