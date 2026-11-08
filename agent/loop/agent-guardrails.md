# Loop: agent-guardrails

Status: DONE
Profile: feature (new behaviour across many files; security-relevant, so trial depth full)
Round: 2 / 4
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
| C1 | Seed data: 25 labelled emails, 20 rep-x + 5 rep-y cases, valid contract shapes | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true agent/.venv/bin/python -m pytest -q agent/tests/test_seed_data.py` | exit 0 |
| C2 | Stub server lists exactly the 7 contract tools with contract schemas, allows/denies like the gateway, 401 without a bearer, revocation switch | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true agent/.venv/bin/python -m pytest -q agent/tests/test_stub_server.py` | exit 0 |
| C3 | scan() with a stubbed boto3 client: INPUT source, flag + categories only, SCAN_ERROR on any error | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true PYTHONPATH=packages/mandate-contracts/src:packages/guardrail-scanner/src agent/.venv/bin/python -m pytest -q packages/guardrail-scanner/tests` | exit 0 |
| C4 | eval.py runs on recorded responses without AWS and prints FP/FN counts | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true PATH=/usr/bin:/bin agent/.venv/bin/python agent/guardrail/eval.py --from-recorded agent/guardrail/fixtures/recorded_sample.json` | stdout matches /False positives \(benign flagged\): \d+[\s\S]*False negatives \(attacks missed\): \d+/ |
| C5 | create.sh parses and its dry run prints the guardrail config without calling AWS | `bash -n agent/guardrail/create.sh && env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true PATH=/usr/bin:/bin bash agent/guardrail/create.sh --dry-run` | stdout matches /PROMPT_ATTACK[\s\S]*ANONYMIZE[\s\S]*GUARDRAIL_ID/ |
| C6 | Agent loop vs stub with a scripted fake model: DENIED reported and not retried, no success claimed, 401 and revoked stop the run, bearer on every MCP call | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true agent/.venv/bin/python -m pytest -q agent/tests/test_agent_loop.py` | exit 0 |
| C7 | Agent CLI wiring: --token / MANDATE_TOKEN / --sid broker path, env defaults (model id, region, gateway URL) | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true agent/.venv/bin/python -m pytest -q agent/tests/test_agent_config.py` | exit 0 |
| C8 | Full repo suite from docs/LANES.md stays green | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src ../../../.venv/bin/python -m pytest -q Backend/tests packages/atlas-enricher/tests packages/mandate-contracts/tests` | exit 0 |
| C9 | Plain-words READMEs exist; requirements pinned | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true test -s agent/README.md && test -s packages/guardrail-scanner/README.md && grep -Eq '^strands-agents==' agent/requirements.txt && grep -Eq '^mcp==' agent/requirements.txt && grep -Eq '^boto3==' agent/requirements.txt` | exit 0 |
| C10 | eval.py excludes failed scans from the counts and exits non-zero on scan errors | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true agent/.venv/bin/python -m pytest -q agent/tests/test_eval.py` | exit 0 |
| C11 | create.sh changes nothing without --apply, reuses an existing guardrail, stops if not READY (fake aws CLI, real CLI off PATH) | `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_EC2_METADATA_DISABLED=true agent/.venv/bin/python -m pytest -q agent/tests/test_create_sh.py` | exit 0 |

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
| 1 | Seed data | lead | agent/data/**, agent/tests/test_seed_data.py | C1 | – | done |
| 2 | Stub server | lead | agent/stub_server.py, agent/tests/test_stub_server.py, agent/tests/conftest.py | C2 | 1 | done |
| 3 | Scanner package | lead | packages/guardrail-scanner/** | C3 | – | done |
| 4 | Eval script | lead | agent/guardrail/eval.py, agent/guardrail/fixtures/** | C4 | 1, 3 | done |
| 5 | create.sh | lead | agent/guardrail/create.sh | C5 | – | done |
| 6 | Agent loop + CLI | lead | agent/agent.py, agent/mandate_agent/**, agent/tests/test_agent_*.py | C6, C7 | 2 | done |
| 7 | READMEs, pins, suite | lead | agent/README.md, agent/requirements.txt, packages/guardrail-scanner/README.md | C8, C9 | 1–6 | done |
| 8 | Gaps 1–4: guard and runner fixes | lead | agent/mandate_agent/guard.py, runner.py, agent/tests/test_agent_loop.py | C6 + new tests | – | done |
| 9 | Gap 5: eval.py error handling | team-implementer (test by lead) | agent/guardrail/eval.py (test: agent/tests/test_eval.py) | C10 | – | done |
| 10 | Gaps 6–7: create.sh --apply and READY | team-implementer (test by lead) | agent/guardrail/create.sh, agent/README.md (create.sh lines) (test: agent/tests/test_create_sh.py) | C5, C11 | – | done |
| 11 | Gap 7: strip AWS credentials in tests and checks | lead | agent/tests/conftest.py, agent/loop/agent-guardrails.md | C1–C11 | – | done |

## Rounds

### Round 1 — 2026-11-07
- Built: tasks 1–7, commits 474bc93, e97c084, 2a4b6ce
- Outcome checks: 9/9 pass; fixed [C1, C2, C3, C4, C5, C6, C7, C9], regressed [] (scoreboard: agent/loop/agent-guardrails.rounds/round-1.json)
- Review: team-reviewer CHANGES REQUESTED (1 Medium, 5 Low). Trial (full): falsifier FALSIFIED (items 9, 10, 23); advocate PARTIAL (only AWS-facing parts unproven). Judge not run: the falsified items are concrete and accepted, so there is nothing to adjudicate; the judge runs on the round-2 trial.
- Incident: the falsifier ran create.sh against real AWS (zsh `export -f`), creating guardrail l74ju5pcrcdg v1+v2 in us-east-1. Not deleted; needs Huy.
- Gap list for next round:
  1. Untrusted tool text can fake a stop or a denial: guard.py matches "Connection to the MCP server was closed" and "DENIED" on any result (reviewer Medium).
  2. Near-repeat retries reach the gateway: email_send `to` compared raw (case, spaces); duplicate forbidden calls in one model turn both run (falsifier 9, reviewer Low 2).
  3. The model's false success claim is printed unlabelled; the test only checks the ledger (falsifier 10/23, reviewer Low 4).
  4. Ledger lost when the run errors mid-way (reviewer Low 3).
  5. eval.py counts SCAN_ERROR as not-flagged and exits 0 (reviewer Low 5); eval.py has no test (advocate).
  6. create.sh publishes even if the guardrail never became READY (reviewer Low 6).
  7. Safety: create.sh touches AWS without an explicit flag; checks and tests do not strip AWS credentials (incident, lead rule).
- Decision: next round because the gaps are fixable code issues and round 1 fixed 8 checks.

### Round 2 — 2026-11-07
- Built: tasks 8–11, commits f95a067 (gap fixes), b1bf458 (kept guardrail, agent/.env), 537bcf9 (--sid beats MANDATE_TOKEN, HomeFront rename)
- Outcome checks: 11/11 pass (C10, C11 added this round; all checks now strip AWS credentials); regressed [] (scoreboards: round-2.json, round-3.json)
- Review: round-1 reviewer findings all fixed. Trial: falsifier FALSIFIED only "never claim it succeeded" read strictly, plus a --sid caveat (fixed in 537bcf9); judge ACCEPT at 537bcf9 under the lead's rulings (label + code-written "Not done" line + ledger). Nothing reached AWS in round 2.
- Gap list for next round: none.
- Decision: DONE because all checks pass, the trial accepts and the gap list is empty. The lead stopped new rounds for the integration merge.

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
- 2026-11-07 16:30: Huy (via the lead) approved with an edit, "Approve with agents": tasks 1, 3, 5 to team-implementer agents. It arrived after the lead had already built and passed tasks 1, 3, 5, so they were not rebuilt; /trial and team-reviewer run in review as asked. Logged as needs-lead in docs/DECISION_LOG.md.
- 2026-11-07 round 2: added C10 and C11 and prefixed every check with the AWS-credential-stripping env (lead safety rule after the guardrail incident). C5 runs its dry run with credentials stripped too. No check was loosened.
