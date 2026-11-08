# Lane E (chat backend): handoff

- Worktree: `HomeFront/.claude/worktrees/chat-huy-lane-e-fb8e00`
- Branch: `claude/chat-huy-lane-e-fb8e00`, HEAD = the commit that adds this file (on top of b2ad31e).
  Not pushed.
- Full round record: `docs/loops/chat-lane-e.md`. Decision: the lead's `docs/DECISION_LOG.md`,
  entry "Lane E · Live title check; continuation and agent-role trials closed".

## Done

1. **Live title check:** done. Conversation titles use
   `us.anthropic.claude-haiku-4-5-20251001-v1:0` (lead's ruling), with no `thinking` field.
   The model can be overridden with `HOMEFRONT_TITLE_MODEL_ID`.
2. **Trials:** the trials of session continuation (9a662e6, 91378e9) and the agent's
   Bedrock-only role (f93f328) are closed. Every finding is fixed in 5725b43, c57abdf and
   fbea37c.
3. **Tests:** backend + packages 911 passed, 3 skipped (offline, AWS credentials stripped).
   The frontend is untouched.

## Title-check evidence

Both runs used this branch's app on `127.0.0.1:8091`. DynamoDB was moto, so no AWS writes.
The staff login was stubbed, and only Bedrock `converse` was allowed out. Each run made one call.

- **Haiku 5.5:** `AccessDeniedException`, an explicit deny in
  `ws-bedrock-model-allowlist-policy-1`, so the title was the first-words fallback.
- **Haiku 4.5:** HTTP 200, `end_turn`, 9 output tokens. The model's text was "Acknowledge
  streetlight repair notifications", and the API title was "Acknowledge streetlight repair"
  (40-char cap).
- **Logs and runner:** the lead's shared notes folder, files `lane-e-title-check*.log` and
  `lane-e-title-check-server.py`.

## Trial verdicts (lite, falsifier only)

- **Continuation:** round 1 FALSIFIED (an offboarded staffer got 403 without `code: "revoked"`;
  last activity across links had no test). Fixed; round 2 CONFIRMED every requirement.
- **Agent role:** FALSIFIED in rounds 1–3, each time on a weak test or the missing tag, never on
  the runtime code:
  - Round 1: the policy test survived widened resources, and `--apply` on an existing role
    never tagged it.
  - Round 2: an extra `*` statement placed first still passed.
  - Round 3: the create-role test matched `36000` as `3600`.

  All are fixed. The trial stopped at the 3-round cap. The round-3 fix was mutation-checked by
  Lane E, not by a fourth falsifier.

## Left

- **Merge (lead):** this branch is ready to merge. Lane L matches Haiku 4.5 in the gateway task
  role. `agent-role.sh`'s inline policy now names Haiku 4.5.
- **Live check:** ApplyGuardrail with the cross-region guardrail profile may also need the
  `guardrail-profile` resource.
- **Untested:** the AssumeRole failure line in the terminal and `<AgentAccess />` in Console.
- **Lane F:** handle `code: "revoked"` when continuing a conversation.
- **Optional:** a stale tab can still post into an expired, continued link. This predates the
  change, and the messages stay readable through `earlier`.

## Resume prompt

> You are Lane E (chat backend). Work only in the worktree
> `HomeFront/.claude/worktrees/chat-huy-lane-e-fb8e00` (branch `claude/chat-huy-lane-e-fb8e00`).
> Read `docs/loops/chat-lane-e-HANDOFF.md` and `docs/loops/chat-lane-e.md`, and the Lane E
> entries in the lead's `docs/DECISION_LOG.md`. Never push. Never touch the demo checkout or
> its servers (:8080, :3000). Never read or print `.env` files. Run tests offline with AWS
> credentials stripped. Pick up the "Left" items the lead assigns. Add tests that fail on the
> old code, then run a lite `/trial`.
