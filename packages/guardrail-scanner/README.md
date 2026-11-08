# guardrail-scanner (WP5)

One function: `scan(text) -> GuardrailFlag`. The gateway calls it on the output of tools whose
output is untrusted (`inbox_read`, `web_scrape`, where `TOOLS[name].untrusted_output` is true). The
flag goes into the decision event, so the dashboard shows "guardrail: PROMPT_ATTACK" next to the call.

```python
from guardrail_scanner import scan

flag = scan(tool_output_text)   # GuardrailFlag(flagged=True, categories=["PROMPT_ATTACK"])
event.guardrail = flag          # log it; pass the ORIGINAL text on to the agent
```

## What it does

- Calls Bedrock `ApplyGuardrail` with `source="INPUT"`, using `GUARDRAIL_ID` and `GUARDRAIL_VERSION`
  from the environment and `AWS_REGION` (default `us-east-1`). Create the guardrail with
  `agent/guardrail/create.sh`.
- Returns only `flagged` and `categories`. It never returns the masked text, and it never changes
  what the agent sees.
- `flagged` is true when an attack filter fires (prompt attack, or a content, topic or word filter).
  PII hits are listed as `PII:<TYPE>` but don't set `flagged`, because constituents send their own
  SSNs and phone numbers in normal mail.
- On any error (missing config, expired credentials, throttling, odd response) it logs the error and
  returns `GuardrailFlag(flagged=False, categories=["SCAN_ERROR"])`.

## Why it fails open

This is a detector, not the control. It can miss attacks: base64 text, or a request split across
two emails. If a broken scan blocked tool output, a Bedrock outage would stop the office's work. The
guarantee is Cedar in the gateway: even when a poisoned email fools the agent, the forbidden tool
call is denied. Never attach this guardrail to the model call itself (`guardrailConfig` on Converse).
That would block and replace the model's response, and hide the Cedar deny the demo needs to show.

## For lane B

Add `packages/guardrail-scanner/src` to `PYTHONPATH` (next to `mandate-contracts/src`), import
`scan`, and call it in `guarded()` after the tool runs, for untrusted-output tools only. The package
needs `boto3` and `mandate-contracts`.

## Tests

```bash
PYTHONPATH=packages/mandate-contracts/src:packages/guardrail-scanner/src \
  agent/.venv/bin/python -m pytest -q packages/guardrail-scanner/tests
```

The tests use a fake boto3 client, so they never call AWS.
