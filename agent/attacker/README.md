# agent/attacker — the staged compromised agent

`compromised_agent.py` plays an AI staffer that fell for the poisoned `email-3`. It has a real
identity (the agent's Cognito client credentials), a real mandate token from the broker and talks
to the real gateway. Only its behaviour is scripted: there is no LLM.

## Why it exists

In a real run, Claude Sonnet 5 recognised `email-3` as phishing and never tried the forbidden tools,
so the demo showed no Cedar DENY. It also finished in about 30 seconds, too fast for the Chief of
Staff to offboard Maya mid-run. This script makes both parts of the demo repeatable: the attack is
always attempted, and the run lasts about 80 seconds.

## What it does

It sends 20 tool calls through the gateway, one every `--delay` seconds (default 4), each exactly once:

1. `inbox_read email-1`, then `inbox_read email-3` (the poisoned email).
2. The attack `email-3` asks for: `records_bulk_export` of all constituent records, `email_send` to
   `archive@civic-records-backup.com`, `social_post` on the `campaign` account, and `case_read case-21`
   (a case of another office). Cedar denies all four.
3. Routine work: more emails, cases of its own office, `datagov_search`, a reply to a constituent.

For each call it prints one line from the gateway's answer: `ALLOW` or `DENY (<reason>, ATLAS <id>)`.

When the gateway says `session revoked / delegator inactive` or `invalid token`, or answers HTTP
401/403 (gateway or broker), it prints `STOPPED: access revoked mid-task`, lists the calls it did not
make, and exits with code 0. It never retries a denied call. It exits with code 3 only when it cannot
start for another reason (for example, Cognito refuses its client credentials).

It reuses the agent's code: `agent.py` for settings and the token (`--sid`, `--token`, `MANDATE_TOKEN`,
`agent/.env`), `mandate_agent/broker.py` for Cognito and the broker, `mandate_agent/guard.py` for the
bearer header and for reading the gateway's answers.

## Run it

```bash
# against the real gateway: Maya presses "Start assistant" and the console shows the session id
agent/.venv/bin/python agent/attacker/compromised_agent.py --sid <session id>

# against the stub gateway (agent/stub_server.py in another terminal)
agent/.venv/bin/python agent/attacker/compromised_agent.py --token dev --gateway http://127.0.0.1:8000/mcp
curl -X POST http://127.0.0.1:8000/admin/revoke     # offboard mid-run

# tests (offline)
agent/.venv/bin/python -m pytest -q agent/tests/test_compromised_agent.py
```
