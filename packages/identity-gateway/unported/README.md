# unported: code from `main` that is not wired in

These files came from `main` in the 2026-11-07 merge. They are **not on the import path** (this folder is not a
package and is outside `src/`), so nothing imports or serves them.

| File | What it was | Why it is parked |
|---|---|---|
| `runner.py` | Drives Claude on Bedrock through a task, calling tools via the old gateway's per-tool grants (`/agents/{id}/runs`) | Built on the old agent store (`store.AgentRecord`), per-tool grants and `/auth/token`, which the mandate design replaced. On this branch the agent is `agent/agent.py`, which calls `/mcp` with a mandate token, so every call already goes through `guarded()`. |
| `dynamo_repo.py` | Kept those agent definitions in the `homefront-agents` table | Same agent store; the merged app has no agent store. |

Their tests (`Backend/tests/test_runner.py`, `test_agents.py`, `test_agent_ownership.py`) are skipped with this
reason. To revive the runner, port it to fetch a mandate token from the broker and call tools through `guarded()`
instead of `gateway_tool_call`; `store.py` is in git history at `origin/main` (`f96f53a`).
