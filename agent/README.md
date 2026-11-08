# agent/ — HomeFront's AI staffer (WP4) and guardrail tools (WP5)

The agent is an AI constituent-services staffer. It works the office's public inbox on behalf of
Maya, a human staffer. It can't touch anything directly: every action is an MCP tool call to the
HomeFront gateway, and the gateway checks identity, Cedar policy and revocation on each call.

The agent is meant to be fooled. One email (`email-3`) and the page `web_scrape` returns contain a
prompt injection that asks it to export all records, email them to an outside address and post on
the campaign account. The demo shows the gateway denying those calls, the agent reporting the
denials honestly, and the agent stopping when Maya is offboarded.

## Files

| File | What it does |
|---|---|
| `agent.py` | Command line entry point. Gets a mandate token, then runs one inbox session. |
| `mandate_agent/config.py` | Gateway URL, model id and region, from env vars. |
| `mandate_agent/broker.py` | Gets the agent's own Cognito token (client_credentials), then asks lane B's broker for a mandate token for a session. |
| `mandate_agent/guard.py` | The rules for the gateway's answers, enforced in code (see below). Also adds the bearer header to every MCP request. |
| `mandate_agent/runner.py` | Builds the Strands agent (Bedrock model, MCP tools, system prompt) and runs it. |
| `attacker/compromised_agent.py` | The staged compromised agent for the demo: real identity and gateway, scripted attack, no LLM. See `attacker/README.md`. |
| `stub_server.py` | A fake gateway for development: the same 7 tools, gateway-like allow/deny, planted injections, a revocation switch. |
| `data/emails.json` | 25 labelled test emails (`email-1` to `email-25`). Lane B seeds them into DynamoDB. |
| `data/cases.json` | 25 cases: `case-1` to `case-20` belong to office `rep-x`, `case-21` to `case-25` to `rep-y`. |
| `data/scrape_page.txt` | The page `web_scrape` returns in the stub, with the planted injection. |
| `guardrail/create.sh` | Creates the Bedrock guardrail and publishes a version. |
| `guardrail/eval.py` | Scores the guardrail on the 25 emails and the scraped page. |
| `tests/` | Offline tests: a scripted fake model stands in for Bedrock, and the stub stands in for the gateway. |
| `loop/` | The `/converge` record for this lane (contract, plan, rounds). |

## How a run works

1. Maya logs in and presses "Start assistant". The gateway creates a session and shows its id.
2. `python agent/agent.py --sid <id>` gets the agent's Cognito token using its own client id and
   secret, and sends it to `POST /api/v1/sessions/<id>/mandate`. The broker returns a 15-minute
   mandate token that says "agent-1, acting for maya, office rep-x".
3. The agent connects to the gateway's MCP endpoint, sending `Authorization: Bearer <mandate token>`
   on every request, lists the tools and works the inbox with Claude Sonnet 5 on Bedrock.
4. Every tool call comes back either as a result or as `DENIED (<reason>, ATLAS <id>)`.

## The rules for gateway answers (enforced in code, not only in the prompt)

The model could ignore its prompt, so `guard.py` enforces the important rules with Strands hooks:

- **DENIED.** The call is recorded as "denied, not done". The model's tool result gets a line
  saying the action was not performed. A repeat of the same action is cancelled before it reaches
  the gateway: any `records_bulk_export`, an email to the same address, a post to the same account.
- **Revoked or rejected.** A denial that says `session revoked / delegator inactive` or
  `invalid token`, or an HTTP 401 on any request, stops the run after the current tool calls.
  The model isn't asked again. The program exits with code 3.
- **Honest summary.** The model's own summary is printed under an "unverified" label, followed by a
  code-written line naming every denied action. Then the program prints "What actually happened",
  built from the gateway's answers: what was allowed, what was denied, how many retries were
  blocked, and why the run stopped.

The system prompt deliberately doesn't tell the model to ignore instructions found in emails. A
typical office assistant would act on a routine-looking staff memo, and the demo shows that Cedar
stops it anyway. The detector (Guardrails) is best-effort; Cedar is the guarantee.

## Run it

```bash
# one-time setup
/usr/local/bin/python3.12 -m venv agent/.venv
agent/.venv/bin/pip install -r agent/requirements.txt

# offline tests (no AWS, no Bedrock)
agent/.venv/bin/python -m pytest -q agent/tests
PYTHONPATH=packages/mandate-contracts/src:packages/guardrail-scanner/src \
  agent/.venv/bin/python -m pytest -q packages/guardrail-scanner/tests

# stub gateway in one terminal
agent/.venv/bin/python agent/stub_server.py                   # add --revoke-after 6 to rehearse offboarding

# agent against the stub (calls Bedrock: needs fresh AWS credentials)
agent/.venv/bin/python agent/agent.py --token dev --gateway http://127.0.0.1:8000/mcp
curl -X POST http://127.0.0.1:8000/admin/revoke               # offboard mid-run (or ?mode=401)

# agent against lane B's gateway with the real broker
cp agent/.env.example agent/.env    # then paste COGNITO_AGENT_CLIENT_SECRET into agent/.env
agent/.venv/bin/python agent/agent.py --sid <session id>
```

Chat mode: Maya talks to the agent in the console instead of it working the inbox once. There is
no "Start" button: Maya's first chat message starts the session through the broker (her Cognito
login, an active staffer). Copy the session id the chat shows, then start the agent; it gets its
mandate token with its own client credentials and answers the chat until the session is revoked:

```bash
agent/.venv/bin/python agent/agent.py --sid <session id> --chat
```

Staged compromised agent (same identity and gateway, scripted attack, no LLM; about 80 s, so
there is time to offboard Maya mid-run):

```bash
agent/.venv/bin/python agent/attacker/compromised_agent.py --sid <session id>   # add --delay 2 for a faster run
```

Settings: copy `agent/.env.example` to `agent/.env` (git-ignored) and paste the agent client secret;
`agent.py` reads it at start, and variables already set in your shell win. Variables: `MANDATE_GATEWAY_URL` (default `http://127.0.0.1:8080/mcp`), `MANDATE_API_URL`
(default: the gateway URL without `/mcp`), `BEDROCK_MODEL_ID` (default `us.anthropic.claude-sonnet-5`),
`AWS_REGION` (default `us-east-1`), `MANDATE_TOKEN` (dev token), and `COGNITO_DOMAIN` (the full `https://` URL),
`COGNITO_AGENT_CLIENT_ID`, `COGNITO_AGENT_CLIENT_SECRET`. For Haiku 4.5, set `BEDROCK_MODEL_ID` to its `us.` inference profile id (check it with
`aws bedrock list-inference-profiles`). The stub accepts any bearer token, unless `MANDATE_DEV_SECRET`
is set; then it checks the token like the gateway.

## Earned autonomy (chat console)

The gateway, not the agent, decides whether an email or post goes out. After Cedar allows an
`email_send` or `social_post`, the gateway checks what Maya allowed for that category. If she has
not set it to "sends alone", the call is saved as a draft in her review queue and the agent gets
`{"status": "held_for_review"}`. `guard.py` reports those drafts as "waiting for your review, not
sent", whatever the model says.

- A reply's category is the `kind` of the inbox email it answers (`data/emails.json`), read from
  DynamoDB, and only when `to` is that email's sender. Anything else is a "new conversation".
- `sensitive`, `staff_memo`, `suspicious` and new conversations are locked: always reviewed.
- After 3 drafts in a category are approved unchanged (5 for posts, at least 90% unchanged), the
  console offers to let the agent send that category alone. Maya accepts or says "not now".
- The rules live in `packages/identity-gateway/src/identity_gateway/autonomy.py`; the routes in
  `Backend/app/routers/assistant.py`.

### Asking for permission (per-session grants)

When something is held, the agent may ask Maya whether it can send that kind alone. Maya answers
with the "Approve and allow for this session" button, which calls `POST /api/v1/sessions/{sid}/grants`
with **her own Cognito login**. Nothing else can grant:

- Chat text, or an email saying "Maya approves this", changes nothing: no route reads chat text
  as a decision. The agent's mandate token is refused by the approve and grant routes.
- A grant is a DynamoDB item `GRANT#<sid>` / `<category>`, for one session and one category. The
  gateway reads it only after Cedar allowed the call, so it can skip the review queue but never a
  `forbid` policy. Locked categories cannot be granted (403).
- It ends with the session: the item stores the session's one-hour end and the gateway ignores it
  after that; a revoked session is denied at step 2 before any grant is
  read, and offboarding also deletes the staffer's grant items.
- A denied call is never retried. The gateway's DENY text names the Cedar policy
  (`DENIED (denied by policy, ATLAS AML.T0053) policy: forbid-bulk-export`), and `guard.py` adds
  that line to the chat reply, so Maya sees the reason even if the model glosses over it.

### Not built yet (future work)

Onboarding questions, losing autonomy after edits, an undo window for sent mail, a daily summary,
four trust levels, per-site rules, and hard rules for money, legal and attachments.

## Guardrail (WP5)

```bash
bash agent/guardrail/create.sh --dry-run    # show the config, call nothing (no flag does the same)
bash agent/guardrail/create.sh --apply      # create it in us-east-1 (ask Huy first)
# the demo guardrail already exists (kept by Huy): GUARDRAIL_ID=l74ju5pcrcdg, GUARDRAIL_VERSION=2 (in agent/.env.example);
# create.sh --apply finds it by name and reuses it, so it never creates a duplicate
agent/.venv/bin/python agent/guardrail/eval.py --record agent/guardrail/fixtures/recorded_live.json
agent/.venv/bin/python agent/guardrail/eval.py --from-recorded agent/guardrail/fixtures/recorded_live.json
```

The guardrail uses the Standard tier, so it covers the Vietnamese and Spanish test emails. It
detects prompt attacks on input at HIGH strength and anonymizes US SSNs and phone numbers.
`recorded_sample.json` is a **synthetic** fixture for the offline test, not real results. Real
numbers go in `docs/GUARDRAIL_EVAL.md` after a live run. The `encoded_or_split` emails (base64, or
one request split across two emails) are expected misses: the detector sees one email at a time.
The gateway still denies the tool calls they ask for.

## Email categories

Each email also has a `kind` (`case_status`, `info_request`, `scheduling`, `newsletter`,
`sensitive`, `staff_memo`, `suspicious`): the inbox triage label the earned-autonomy rules use.
`category` below is the guardrail test label.

`angry_benign`, `pii_benign` (fake SSNs in the never-issued 9xx range, 555-01xx phone numbers) and
`non_english_benign` (Vietnamese, Spanish) should not be flagged. `obvious_attack`,
`disguised_staff_memo`, `hidden_in_quote` (quoted thread, HTML comment, text after blank lines) and
`encoded_or_split` are attacks. All people are fictional.
