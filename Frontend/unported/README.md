# unported: frontend pages from `main` that are not wired in

These files came from `main` in the second merge into `Merge-Main-Huy` (main `4b13df7`). They sit outside `src/`, so
`tsc`, Vite and Vitest never see them, and no route points at them.

| File | What it was | Why it is parked |
|---|---|---|
| `pages/Agents.tsx`, `pages/NewAgent.tsx`, `pages/Agents.css` | List and create agents, edit an agent's mailing list | Calls `/agents`, `/agents/{id}/recipients`, `/agents/{id}/grants` and `/agent-types`, which are main's open agent store. That store is removed on this branch: an agent gets a mandate token from the broker (`POST /sessions`) and Cedar decides each call. |
| `pages/Approvals.tsx`, `pages/Approvals.css` | Approve or reject queued high-risk tool calls | Calls `/approvals`, which hangs off the open `/auth/token` workload token and the agent store (see `packages/identity-gateway/unported/approvals.py`). |
| `pages/Activity.tsx`, `pages/Activity.css` | Activity log with filters | Calls `/audit` and `/agents`. On this branch the decision log is the `/events` feed, already shown live on `/console`. |
| `components/StorageStatus/` | Shows where agents and the log are stored | Calls `/status`, a route of main's gateway app that this branch does not serve. |
| `lib/agents.ts` | Shared types for the pages above | Only used by them. |

TODO: to revive a page, port it to the mandate API in `src/lib/api.ts` (`/sessions`, `/events`, `/staff/{id}/offboard`)
and add backend routes on the mandate design first. Approvals would need a pending-approval step inside `guarded()`
after the Cedar ALLOW, never a separate token path.

Third merge (main `130c7e1`): main's new `RunTask`, `EmailSetup`, `EmailDelivery` pages, the edited `Monitoring`
page and `lib/api.js` (which sends the old pool's ID token) are parked here as well. The next commit on
`Merge-Main-Huy-2` restores them in `src/` on our `lib/api.ts` login (Huy's decision: keep the teammates' tabs).
