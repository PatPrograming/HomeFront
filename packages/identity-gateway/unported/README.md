# unported: code from `main` that is not wired in

These files came from `main` in the 2026-11-07 merge. They are **not on the import path** (this folder is not a
package and is outside `src/`), so nothing imports or serves them.

| File | What it was | Why it is parked |
|---|---|---|
| `runner.py` | Drives Claude on Bedrock through a task, calling tools via the old gateway's per-tool grants (`/agents/{id}/runs`) | Built on the old agent store (`store.AgentRecord`), per-tool grants and `/auth/token`, which the mandate design replaced. On this branch the agent is `agent/agent.py`, which calls `/mcp` with a mandate token, so every call already goes through `guarded()`. |
| `dynamo_repo.py` | Kept those agent definitions in the `homefront-agents` table | Same agent store; the merged app has no agent store. |
| `audit.py` | Activity log (`<prefix>audit` table) behind main's `/audit` route | Added in the second merge (main `4b13df7`). Written by the agent store and served by `/audit`, both removed. On this branch every decision is a `DecisionEvent` in the `mandate` table, streamed by `/events`. |
| `approvals.py` | Human approval queue (`<prefix>approvals` table) for `requires_hitl` tools, behind `/approvals` | Added in the second merge. Hangs off the open `/auth/token` workload token and the agent store. On this branch `email_send` is allowed or denied per call by Cedar inside `guarded()`. |
| `agent_types.py` | Agent kinds (email, social, research) with a tool ceiling enforced on grants | Added in the second merge. A ceiling on per-tool grants in the removed agent store; on this branch Cedar policies in `mandate_contracts.cedar` decide every call. |

Their tests (`Backend/tests/test_runner.py`, `test_agents.py`, `test_agent_ownership.py`, and from the second merge `test_activity_log.py`, `test_approvals.py`, `test_agent_types.py`, `test_mailing_list.py`) are skipped with this
reason. To revive the runner, port it to fetch a mandate token from the broker and call tools through `guarded()`
instead of `gateway_tool_call`; `store.py` is in git history at `origin/main` (`f96f53a`).
