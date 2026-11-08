# Lane E (chat backend): loop record

Branch `claude/chat-huy-lane-e-fb8e00`. Earlier rounds (sidebar API, silent failures, session
continuation, agent role) are recorded in the lead's `docs/DECISION_LOG.md`.

## Round: live title check and trial verdicts (2026-11-08)

Commits: 5725b43, c57abdf, fbea37c. Backend + packages: 911 passed, 3 skipped (offline,
AWS credentials stripped).

### Live title check

Result: **titles come from Claude Haiku 4.5.** Haiku 5.5 is denied for the event account.

How both calls ran:

- This branch's app on `127.0.0.1:8091`, started with `~/.aws_workshop_env` and
  `Backend/infra/.env.aws` loaded, then stopped.
- DynamoDB was moto (in memory), so no AWS writes. The staff login was stubbed because a real
  Cognito login needs a password. Agent auto-start was off.
- Only Bedrock `converse` was allowed through to AWS. Each run made exactly one call.
- Runner and logs: the lead's shared notes folder, `lane-e-title-check-server.py`,
  `lane-e-title-check.log` and `lane-e-title-check-haiku45.log`.

Call 1, Haiku 5.5 (`us.anthropic.claude-haiku-5-5`, `thinking: disabled`):

```
AccessDeniedException: ... assumed-role/WSParticipantRole/Participant is not authorized to perform:
bedrock:InvokeModel on resource: arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-haiku-5-5
with an explicit deny in an identity-based policy: ...policy/ws-bedrock-model-allowlist-policy-1
```

The API returned the first-words fallback, `"Three constituents emailed about the"`.

Lead's ruling: the default is now `us.anthropic.claude-haiku-4-5-20251001-v1:0`. The `thinking`
field is dropped, because Haiku 4.5 doesn't think unless asked.

Call 2, Haiku 4.5 (fbea37c):

```
converse request modelId=us.anthropic.claude-haiku-4-5-20251001-v1:0 additionalModelRequestFields=None
converse response HTTP 200 stopReason=end_turn usage={'inputTokens': 99, 'outputTokens': 9, ...}
content=[{'text': 'Acknowledge streetlight repair notifications'}]
```

`GET /api/v1/sessions` returned `"title": "Acknowledge streetlight repair"`. The model's 44
characters were cut to fit the 40-character cap.

### Trials (lite, falsifier only)

The earlier trials of 9a662e6/91378e9 and f93f328 never reported, so both were re-run.

| Round | Change | Falsifier | Fix |
|---|---|---|---|
| 1 | Continuation (9a662e6, 91378e9) | FALSIFIED: an offboarded staffer got 403 with no `revoked` code; "last activity across links" had no test | 5725b43 |
| 1 | Agent role (f93f328) | FALSIFIED: the session-policy test survived widened resources; `--apply` on an existing role never tagged it | 5725b43 |
| 2 | 5725b43 | Continuation: every requirement CONFIRMED. Role: an extra `*` statement placed first still passed | c57abdf |
| 3 | c57abdf | Policy: every widening caught. The create-role test matched `36000` as `3600` | fbea37c |

The trial stopped at the 3-round cap. The round-3 finding was a one-character test fix (a
trailing space after `3600`). I confirmed the `36000` mutation now fails the test. No fourth
falsifier was run.

The runtime code passed every round: child environment, fail-closed start, 503, status
endpoint, dry run, and the continuation guards. Every finding was a missing or weak test,
except the `revoked` code and the missing tag, which were code fixes.

### Open

- **ApplyGuardrail profile:** with the cross-region guardrail profile it may also need the
  `guardrail-profile` resource. Only a live check can show that.
- **Untested lines:** the AssumeRole failure line in the terminal and `<AgentAccess />` in
  Console have no test.
- **Old-link messages:** a stale tab can still post into an expired, continued link. This
  predates the change, and the messages stay readable through `earlier`.
- **Lane F:** the console doesn't handle `code: "revoked"` on continue yet.
