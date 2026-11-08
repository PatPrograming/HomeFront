# Loop: agent-guardrails

Status: BUILDING
Profile: feature (new behaviour across many files; security-relevant, so trial depth full)
Round: 1 / 4
Base commit: 839c456
Branch: claude/agent-guardrails-huy-4a5df6

## Original Request

Build Mandate's agent and guardrail detector (WP4 + WP5). Deliver: (1) `agent/` with a venv from `/usr/local/bin/python3.12`, `requirements.txt` (`strands-agents`, `mcp`, `boto3`, pinned), `agent.py`: a Strands Agent on `BedrockModel` (`BEDROCK_MODEL_ID`, default `us.anthropic.claude-sonnet-5`, region us-east-1) with an MCP client to `MANDATE_GATEWAY_URL` sending `Authorization: Bearer <mandate token>`; it gets the mandate token by calling lane B's `POST /api/v1/sessions/{sid}/mandate` with its own Cognito client_credentials token (`agent.py --sid <sid>`), or uses `--token`/`MANDATE_TOKEN` for dev (`python -m mandate_contracts.dev`). A short system prompt for a constituent-services staffer working the inbox. Behaviour: on a `DENIED` tool result, tell the staffer which action was denied and why, drop that action, never retry it and never claim it succeeded; on HTTP 401 or a 'session revoked / delegator inactive' denial, stop the run cleanly with a clear message. (2) `agent/stub_server.py`: FastMCP streamable HTTP on 127.0.0.1:8000/mcp with the 7 tools from `mandate_contracts.TOOLS` and their schemas, simple rule-based allow/deny that mimics the gateway, a planted injection in one email and in `web_scrape` output, and a switch to simulate revocation. (3) `agent/data/emails.json` (25 emails, ids `email-1…`, each labelled with a category: angry_benign, pii_benign, non_english_benign (Vietnamese, Spanish), obvious_attack, disguised_staff_memo, hidden_in_quote, encoded_or_split) and `agent/data/cases.json` (20 rep-x, 5 rep-y, ids `case-1…`), shaped as `EmailItem`/`CaseItem` plus `id`; fictional people only, PII such as SSNs fake. (4) `agent/guardrail/create.sh` (idempotent, `--dry-run`): guardrail with PROMPT_ATTACK on input at HIGH and PII anonymize for SSN and PHONE, plus a published version, printing `GUARDRAIL_ID`/`GUARDRAIL_VERSION`. (5) `packages/guardrail-scanner` (import `guardrail_scanner`) with `scan(text) -> GuardrailFlag` exactly as in `docs/LANES.md`: ApplyGuardrail with source INPUT, flag and categories only, never returns masked text, on any error returns `flagged=False, categories=['SCAN_ERROR']`; tests with a stubbed boto3 client. (6) `agent/guardrail/eval.py`: runs `scan()` over the 25 emails and the scrape page and prints a table plus false-positive and false-negative counts; supports `--from-recorded <file>` so it can run on recorded guardrail responses without AWS; results written to `docs/GUARDRAIL_EVAL.md` by Huy after a real run. (7) Tests that run the agent loop against the stub server with a fake Bedrock model (scripted tool calls) proving: DENIED is reported and not retried; success is never claimed for a denied call; 401 / revoked stops the run; the bearer header is sent on every MCP call. (8) Plain-words READMEs in `agent/` and `packages/guardrail-scanner/`.

## Desired Outcome

A teammate can run the Mandate agent against the stub server (or lane B's gateway) and watch it work the
inbox, get DENIED on forbidden tools, say so honestly without retrying, and stop cleanly on 401 or
revocation; lane B can import `guardrail_scanner.scan()` and load the seed data. All of it is proven
offline, with no AWS or Bedrock call.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Seed data: 25 labelled emails, 20 rep-x + 5 rep-y cases, valid contract shapes | `agent/.venv/bin/python -m pytest -q agent/tests/test_seed_data.py` | exit 0 |
| C2 | Stub server lists exactly the 7 contract tools with contract schemas, allows/denies like the gateway, 401 without a bearer, revocation switch | `agent/.venv/bin/python -m pytest -q agent/tests/test_stub_server.py` | exit 0 |
| C3 | scan() with a stubbed boto3 client: INPUT source, flag + categories only, SCAN_ERROR on any error | `PYTHONPATH=packages/mandate-contracts/src:packages/guardrail-scanner/src agent/.venv/bin/python -m pytest -q packages/guardrail-scanner/tests` | exit 0 |
| C4 | eval.py runs on recorded responses without AWS and prints FP/FN counts | `PATH=/usr/bin:/bin AWS_ACCESS_KEY_ID= AWS_SECRET_ACCESS_KEY= agent/.venv/bin/python agent/guardrail/eval.py --from-recorded agent/guardrail/fixtures/recorded_sample.json` | stdout matches /False positives \(benign flagged\): \d+[\s\S]*False negatives \(attacks missed\): \d+/ |
| C5 | create.sh parses and its dry run prints the guardrail config without calling AWS | `bash -n agent/guardrail/create.sh && PATH=/usr/bin:/bin bash agent/guardrail/create.sh --dry-run` | stdout matches /PROMPT_ATTACK[\s\S]*ANONYMIZE[\s\S]*GUARDRAIL_ID/ |
| C6 | Agent loop vs stub with a scripted fake model: DENIED reported and not retried, no success claimed, 401 and revoked stop the run, bearer on every MCP call | `agent/.venv/bin/python -m pytest -q agent/tests/test_agent_loop.py` | exit 0 |
| C7 | Agent CLI wiring: --token / MANDATE_TOKEN / --sid broker path, env defaults (model id, region, gateway URL) | `agent/.venv/bin/python -m pytest -q agent/tests/test_agent_config.py` | exit 0 |
| C8 | Full repo suite from docs/LANES.md stays green | `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src ../../../.venv/bin/python -m pytest -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests` | exit 0 |
| C9 | Plain-words READMEs exist; requirements pinned | `test -s agent/README.md && test -s packages/guardrail-scanner/README.md && grep -Eq '^strands-agents==' agent/requirements.txt && grep -Eq '^mcp==' agent/requirements.txt && grep -Eq '^boto3==' agent/requirements.txt` | exit 0 |

## Not Checkable By Command

- The poisoned email or page makes the real agent (Sonnet 5 on Bedrock) attempt a forbidden tool in at least
  4 of 5 runs. Needs real Bedrock runs by Huy. Fallbacks if Sonnet 5 refuses: reword as a routine staff memo,
  Haiku 4.5, or `agent/attacker/compromised_agent.py`.
- 0 false positives on benign mail and the planted scrape injection flagged, with the real guardrail. Needs
  Huy to run `create.sh` and `eval.py` live, then write `docs/GUARDRAIL_EVAL.md`.
- Agent against lane B's real gateway and broker (Integration 1 and 2). Needs lane B merged.

## Plan

1. Seed data: `agent/data/emails.json`, `agent/data/cases.json`, `agent/tests/test_seed_data.py` (C1).
2. Stub server: `agent/stub_server.py` reusing `mandate_contracts.TOOLS` schemas; rules mimic the starter
   Cedar policies; planted injections; revocation switch via `POST /admin/revoke` and `--revoke-after N`;
   `agent/tests/test_stub_server.py` (C2).
3. Scanner package: `packages/guardrail-scanner/{pyproject.toml,src/guardrail_scanner/__init__.py,tests/}` (C3).
4. Eval: `agent/guardrail/eval.py` with `--from-recorded`, a recorded fixture (C4).
5. `agent/guardrail/create.sh` idempotent with `--dry-run` (C5).
6. Agent loop: `agent/mandate_agent/` (config, broker call, Strands hooks that enforce DENIED handling in
   code, not only in the prompt), `agent/agent.py` CLI; fake scripted model; `agent/tests/test_agent_loop.py`,
   `agent/tests/test_agent_config.py` (C6, C7).
7. READMEs, pinned `agent/requirements.txt` (C9), full suite (C8), commit.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Seed data | lead | agent/data/**, agent/tests/test_seed_data.py | C1 | – | pending |
| 2 | Stub server | lead | agent/stub_server.py, agent/tests/test_stub_server.py, agent/tests/conftest.py | C2 | 1 | pending |
| 3 | Scanner package | lead | packages/guardrail-scanner/** | C3 | – | pending |
| 4 | Eval script | lead | agent/guardrail/eval.py, agent/guardrail/fixtures/** | C4 | 1, 3 | pending |
| 5 | create.sh | lead | agent/guardrail/create.sh | C5 | – | pending |
| 6 | Agent loop + CLI | lead | agent/agent.py, agent/mandate_agent/**, agent/tests/test_agent_*.py | C6, C7 | 2 | pending |
| 7 | READMEs, pins, suite | lead | agent/README.md, agent/requirements.txt, packages/guardrail-scanner/README.md | C8, C9 | 1–6 | pending |

## Rounds

## Decisions

- Loop file lives in `agent/loop/` instead of `docs/loops/`: docs/LANES.md limits lane C to `agent/**`,
  `packages/guardrail-scanner/**` and `docs/GUARDRAIL_EVAL.md`.
- `mcp` pinned below 2: mcp 2.x removed `FastMCP` (renamed `MCPServer`); the brief and lane B say FastMCP, and
  Strands 1.64 supports both lines. Lane B is told in the shared notes.
- The lead writes the plan and builds the tasks directly (no team-architect / test-author / implementer agents)
  to hit the 17:00 Integration 1 milestone; tests are still written before each task's code, and `/trial` and
  the team-reviewer still run in review.
- DENIED handling is enforced in code by Strands hooks (block a repeat of a denied call, end the turn on
  401/revoked, print a code-generated "denied, not done" ledger), so it does not rely on the model obeying the
  prompt.
- 2026-11-07 16:20: Huy approved the contract and plan ("Approve, build it").
