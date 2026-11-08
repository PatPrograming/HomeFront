# Loop: deploy-aws

Status: DONE (trial ACCEPT, 2026-11-08, on the merge with Lane E)
Profile: feature (infrastructure; lead-written plan, no app logic to test first)
Round: 4 / 4
Base commit: 3d7d0e1
Branch: Deploy-AWS-Huy

## Original Request

Run `/converge`. Goal: HomeFront runs 24/7 on AWS so judges and testers use it from their own browser with an admin-created account, and nobody needs a laptop running.

Deliver, as infrastructure-as-code in the repo plus a numbered runbook for Huy (no AWS writes by you; every script dry-run unless `--apply`, Huy runs it):
- Gateway (FastAPI, `scripts/run-gateway.sh`, uvicorn on 8080) and the agent processes it auto-starts (`agent/agent.py --sid … --chat`) in one container on App Runner or ECS Fargate (pick and justify; the supervisor spawns child processes, so the container must allow that). Region us-east-1.
- An IAM task/instance role with least-privilege access instead of access keys: DynamoDB `mandate` and `homefront-*` tables, `verifiedpermissions:IsAuthorized` on store `Fmx8fLovCMpXgABUM1bHhb`, `bedrock:InvokeModel*` on the Sonnet 5 / Haiku 5.5 inference profiles, `bedrock:ApplyGuardrail` on `l74ju5pcrcdg`, the Cognito admin calls the routes use, SES send if configured.
- Secrets (`MANDATE_SIGNING_SECRET`, `COGNITO_AGENT_CLIENT_SECRET`, gov API keys) in Secrets Manager, injected at runtime; none in images, env files or logs.
- Console built with Vite and served from S3 + CloudFront (or Amplify Hosting) over HTTPS, `/api` and `/mcp` routed to the gateway; `ALLOWED_ORIGINS` and the Cognito `staff-web` client's callback/logout URLs extended with the HTTPS URL (dry-run script), keeping `http://localhost:3000/`.
- Health check, logs to CloudWatch (no secrets or tokens), cost estimate per day, and a teardown script.
- Note the risk that the hackathon's Workshop Studio account may be wiped after the event; say what's lost and how to redeploy elsewhere.

Work in your own worktree (…). Tests: build the container locally and run its health check with AWS credentials stripped; template lint (cfn-lint or `cdk synth`). Follow `HomeFront/docs/LANES.md`; never touch `.env` files or the main checkout; don't push; log decisions in `/Users/tranquanghuy/Projects/aws-a10-hack-2026/docs/DECISION_LOG.md`; lite `/trial`; report the runbook to the lead.

## Desired Outcome

The repo holds CloudFormation templates and dry-run-by-default scripts that, when Huy runs them with `--apply`, put the gateway and its auto-started agents in one ECS Fargate container behind CloudFront (HTTPS) with the console on S3, using a least-privilege task role and Secrets Manager; the container image passes its health check locally with no AWS credentials.

## Outcome Checks

| ID | Check | Command | Pass when |
|----|-------|---------|-----------|
| C1 | Templates lint clean | `/private/tmp/claude-501/-Users-tranquanghuy-Projects-aws-a10-hack-2026/84011187-45c8-4436-868e-23188cf55860/scratchpad/cfn/bin/cfn-lint deploy/aws/foundation.yaml deploy/aws/app.yaml` | exit 0 |
| C2 | Policy, secrets and dry-run tests pass (fake `aws` CLI records every call; no write call without `--apply`) | `.venv/bin/python -m pytest -q deploy/aws/tests` | exit 0 |
| C3 | Container builds and answers its health check with AWS credentials stripped | `deploy/aws/local-test.sh` | stdout contains HEALTH OK |
| C4 | Agent children never inherit the gateway task role (lead, 2026-11-08: separate identities; agents get only homefront-agent credentials via AssumeRole). Run on the merge with Lane E (round 4: the old grep for `AWS_CONTAINER` counted Lane E's own warning comment) | `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/identity-gateway/src:packages/guardrail-scanner/src:packages/mcp-tools/src:packages/atlas-enricher/src .venv/bin/python -m pytest -q Backend/tests/test_agent_role.py deploy/aws/tests/test_deploy.py -rs` | exit 0, no SKIPPED line |
| C5 | Backend suite still green (except `test_agent_requests.py`: 11 failures already on origin/main 3d7d0e1, router not mounted; reported as a follow-up) | `PYTHONPATH=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src:packages/guardrail-scanner/src .venv/bin/python -m pytest -q Backend/tests --ignore=Backend/tests/test_agent_requests.py` | exit 0 |

## Not Checkable By Command

- A real deploy in the Workshop Studio account (Huy runs the runbook with `--apply`).
- Sign-in through Cognito on the CloudFront URL, a chat that starts an agent, and an offboard that stops it (Huy, in a browser, after deploy).
- That CloudWatch logs contain no tokens during a real session (Huy skims the log group after a chat; the code paths are reviewed by the trial).
- Exact Bedrock spend (depends on testers' usage).

## Plan

1. Container: `deploy/aws/Dockerfile` (+ `Dockerfile.dockerignore`) builds two venvs (gateway, agent) on python:3.12-slim, ARM64. `deploy/aws/entrypoint.sh` writes `agent/.env` (mode 600) from injected variables at start, removes the agent's secret from the gateway's environment, then execs uvicorn on 0.0.0.0:8080. Done when C3.
2. Supervisor: add the ECS container-credential variables to `CHILD_ENV_KEYS` in `Backend/app/services/agent_supervisor.py`, with a test. Done when C4, C5.
3. Templates: `deploy/aws/foundation.yaml` (ECR repo, secret with generated signing key) and `deploy/aws/app.yaml` (task role, execution role, log group, SGs, internal ALB, ECS cluster/service, S3 bucket + OAC, CloudFront VPC origin + distribution + SPA rewrite function). Done when C1, C2.
4. Scripts: `deploy.sh`, `put-secrets.py`, `cognito-urls.py`, `smoke.sh`, `teardown.sh`, all dry-run unless `--apply`. Done when C2.
5. Runbook + cost + wipe risk in `deploy/aws/README.md`. Reviewed by trial.

## Task Ledger

| # | Task | Role | Files it may touch | Done when | Depends on | Status |
|---|------|------|--------------------|-----------|------------|--------|
| 1 | Container + entrypoint + local test | lead | `deploy/aws/Dockerfile*`, `deploy/aws/entrypoint.sh`, `deploy/aws/local-test.sh` | C3 | - | done |
| 2 | Child env allowlist | lead | `Backend/app/services/agent_supervisor.py`, `Backend/tests/test_agent_autostart.py` | C4, C5 | - | done |
| 3 | CloudFormation templates | lead | `deploy/aws/*.yaml` | C1 | - | done |
| 4 | Scripts + tests | lead | `deploy/aws/*.sh`, `deploy/aws/*.py`, `deploy/aws/tests/**` | C2 | 3 | done |
| 5 | Runbook | lead | `deploy/aws/README.md` | trial | 1-4 | done |

## Rounds

### Round 1-3 — 2026-11-08
- Built: tasks 1-5, then the lead's split-identity requirement and Haiku-titles ruling. Commits b4d4a4f, f649ea6, 153e5c5 (revert of 6243691), 2966b0f, 4fb5388, then the trial fixes.
- Outcome checks: 5/5 pass (round 3 scoreboard). C3 first failed on a pass-rule typo (backticks), not on the code.
- Trial (lite): falsifier FALSIFIED on one item, the lead's identity split. Nothing creates `homefront-agent` or calls AssumeRole yet; both are Lane E's assigned work. Everything else was CONFIRMED or is UNVERIFIED pending a real deploy. Fixed from its risks: external /mcp Host check (forward the viewer Host), SES per-agent senders (dropped the FromAddress condition), teardown's silent secret-delete failure.
- Judge skipped: the one FALSIFIED item is a cross-lane dependency this lane may not build; the falsifier's evidence was re-checked by hand (`ls Backend/infra` has no agent-role.sh).
- Decision: STUCK, not DONE. Done when Lane E merges `HOMEFRONT_AGENT_ROLE_ARN` handling and `Backend/infra/agent-role.sh`; re-run the trial then.

### Round 4 — 2026-11-08
- Merged with Lane E f93f328 in a throwaway detached worktree `../lane-l-probe` (no conflicts; removed afterwards). Confirmed: the stack sets `HOMEFRONT_AGENT_ROLE_ARN` = `role/homefront-agent`; the gateway task role's only STS grant is `sts:AssumeRole` on that role (the supervisor passes no Tags or SourceIdentity, so no `TagSession`); names match `Backend/infra/agent-role.sh`.
- Suites on the merge (credentials stripped): Backend 671 passed / 3 skipped (+ the 11 known `test_agent_requests.py` failures from main), packages 236, agent 92, deploy 37, vitest 106, tsc and cfn-lint clean.
- Fixed in 90f4fd0: `add_tester.py --office-role` (default caseworker; without it Lane G's role forbids deny a new judge every tool, validated before any Cognito call); a test pinning stack names to `agent-role.sh` (skips until Lane E merges); `local-test.sh` runs with the role ARN set; `deploy.sh` hint; runbook `--apply` order G, H, I, E, deploy.sh. C4 rewritten: the old grep counted Lane E's own warning comment.
- Outcome checks: 5/5 on the merge (C3 HEALTH OK with the ARN; removing it fails the check).
- Trial (lite): falsifier INCONCLUSIVE (no Docker, probes cut short, nothing failed) plus a scan flag (the deliberate skip), so the judge ran: ACCEPT. Judge re-ran C1-C5 itself incl. Docker, and 8 mutations each failed a test. Claim "HEALTH OK on Deploy-AWS-Huy alone" UNBACKED (not reproduced by the judge; irrelevant to the merged deploy). Known test gap: an `iam:PassRole` in a new, separately named inline policy would not be caught.
- Decision: DONE.

## Decisions

- 2026-11-08: Scope change from the lead (verbatim): "The gateway and the agent processes must not share an identity. Gateway task role: DynamoDB, Verified Permissions, Cognito admin calls, SES, Secrets Manager, and kms:Decrypt/Encrypt on Lane I's connected-accounts key (with the EncryptionContext condition). Agent role: Bedrock only (`bedrock:InvokeModel*` on the Sonnet 5 / Haiku 5.5 profiles, `bedrock:ApplyGuardrail` on `l74ju5pcrcdg`), assumed by the gateway per agent via `sts:AssumeRole` (env `HOMEFRONT_AGENT_ROLE_ARN`)." C4 rewritten to match (it previously required the opposite); commit 6243691 reverted.

- 2026-11-08: Approval gate skipped. The lead's brief already fixed the deliverables and the tests (local container health check with credentials stripped, template lint); these checks restate them. The lead can stop the loop.
- 2026-11-08: Lead (this session) builds directly instead of spawning team-implementer: the work is new IaC files with one small app edit, and the brief asks for a lite trial only.
