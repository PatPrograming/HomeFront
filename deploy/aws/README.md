# deploy/aws: HomeFront on AWS, 24/7

This folder runs HomeFront in the cloud, so judges and testers sign in from their own browser and nobody keeps a laptop
running. Every script is a dry run unless you pass `--apply`.

## What runs where

```
browser ──HTTPS──> CloudFront ──┬── /            ──> S3 bucket (private): the Vite console
                                ├── /api/*       ──┐
                                └── /mcp*        ──┴─> CloudFront VPC origin ──> internal ALB ──> ECS Fargate task
                                                                                                  (gateway + agents)
```

- **One ECS Fargate task** (1 vCPU, 2 GB, ARM64) runs the FastAPI gateway on port 8080. The gateway starts each chat
  agent (`agent/agent.py --sid … --chat`) as a child process in the same container, exactly as on a laptop.
- **CloudFront** serves the console and forwards `/api` and `/mcp` to the gateway, so the console calls the API on its
  own origin. The load balancer is internal: the only way in from the internet is CloudFront.
- **The task role** replaces the developer's Workshop Studio keys. It grants only what the code calls (table below).
- **Secrets Manager** holds one secret, `homefront/runtime`. ECS injects it as environment variables when the task
  starts. CloudFormation generates the mandate signing key inside AWS; nobody ever sees it.
- **CloudWatch Logs** keeps the gateway's and the agents' output in `/homefront/gateway` for 14 days.

### Why ECS Fargate and not App Runner

- The gateway keeps running agents, MCP sessions and the agent supervisor in memory, so exactly one task must serve all
  traffic. ECS pins `DesiredCount: 1`; App Runner scales instances on its own.
- Chat and MCP requests stay open for a long time. App Runner cuts every request at 120 seconds; behind ECS the load
  balancer idle timeout is 300 seconds and CloudFront's origin timeout is 60 seconds per response chunk.
- Fargate gives the container a normal process model with an init process (`InitProcessEnabled`), which reaps the
  agent children and forwards the stop signal to them.

### What the task role may do

| Service | Actions | Only on |
|---|---|---|
| DynamoDB | item reads and writes, `Query`, `Scan`, `DescribeTable` (no create or delete table) | `mandate`, `homefront-*` and their indexes |
| Verified Permissions | `IsAuthorized` | policy store `Fmx8fLovCMpXgABUM1bHhb` |
| Bedrock | `InvokeModel`, `InvokeModelWithResponseStream` | `us.anthropic.claude-sonnet-5*` and `us.anthropic.claude-haiku-5-5*` inference profiles and their models |
| Bedrock | `ApplyGuardrail` | guardrail `l74ju5pcrcdg` |
| SES | `SendEmail` from the one configured sender, `GetEmailIdentity`, `ListEmailIdentities` | only when `SES_FROM` is set at deploy |
| Cognito | nothing | The routes call no Cognito admin API: tokens are checked against the pool's public keys, and offboarding writes DynamoDB. |

Not granted, on purpose: the teammates' "Run a task" runner (Opus 5.5 through Bedrock Mantle), and the IAM permission
check on the Agents tab's email setup. Both report an error in the console instead of working.

## Files

| File | What it does |
|---|---|
| `Dockerfile`, `Dockerfile.dockerignore` | The container image. The ignore file is an allow-list and drops every `.env` file. |
| `entrypoint.sh` | At container start, writes `agent/.env` (mode 600) for the agents, then removes the agent's secret from the gateway's own environment and starts uvicorn. |
| `foundation.yaml` | CloudFormation stack `homefront-foundation`: the ECR repository and the `homefront/runtime` secret. |
| `app.yaml` | CloudFormation stack `homefront-app`: roles, ECS, internal ALB, CloudFront, S3, log group. |
| `deploy.sh` | Runs everything in order: stacks, image, secret, console upload, Cognito URLs, smoke test. |
| `put_secrets.py` | Fills the secret: the agent's Cognito client secret and your government API keys. Prints key names only. |
| `cognito_urls.py` | Adds (or with `--remove`, takes out) the HTTPS URL in the `staff-web` client's callback and logout URLs. Keeps `http://localhost:3000/`. |
| `add_tester.py` | Creates a judge's or tester's account. Cognito emails them a temporary password. |
| `smoke.sh` | Read-only checks of the live site. |
| `teardown.sh` | Removes everything `deploy.sh` created. |
| `local-test.sh` | Builds the image and checks it on your Mac with no AWS credentials. |
| `tests/test_deploy.py` | Offline tests: the role's permissions, the secret mapping, and that no script writes without `--apply`. |

## Runbook

### Prerequisites

- Docker Desktop running (`docker info` prints a server version).
- The AWS CLI v2, Node 20 and the repo's `.venv` (created by `scripts/run-gateway.sh`).
- Fresh Workshop Studio credentials for the event account in your shell, region `us-east-1`. They expire after a few
  hours; on `ExpiredToken`, fetch new ones from the event portal and repeat the step.
- This branch merged into the checkout you run from (or run from this worktree).
- Your repo-root `.env` holds the government API keys you want live. Only `put_secrets.py` reads it, and only those keys.

### Deploy

1. Check which account your shell talks to.

   ```bash
   aws sts get-caller-identity --query Account --output text
   ```

   It prints the event account's 12-digit id. Anything else: stop and fix your credentials.

2. Preview the whole deploy.

   ```bash
   deploy/aws/deploy.sh
   ```

   Every step prints `DRY-RUN: …` lines and the last line says `dry run, nothing changed`. Nothing is created.

3. Deploy.

   ```bash
   deploy/aws/deploy.sh --apply
   ```

   This takes 15 to 25 minutes the first time, mostly CloudFront. It worked when it ends with `SMOKE OK` and
   `Console: https://<id>.cloudfront.net/`. Copy that URL.

4. Preview a tester's account.

   ```bash
   .venv/bin/python deploy/aws/add_tester.py judge1 judge1@example.com
   ```

   It prints the user, group and office it would create, then `DRY RUN`.

5. Create the account.

   ```bash
   .venv/bin/python deploy/aws/add_tester.py judge1 judge1@example.com --apply
   ```

   It prints `created.` Cognito emails the temporary password to that address. Add `--admin` for someone who may
   offboard staff. Repeat steps 4 and 5 for each tester.

6. Sign in as a tester in a private browser window at the console URL from step 3.

   It worked when Cognito asks for a new password and the console's home page loads.

7. Start a chat and send one message.

   It worked when the agent answers. Its start shows in the log as `agent started sid=…`.

8. Check the log for tokens.

   ```bash
   aws logs filter-log-events --log-group-name /homefront/gateway --filter-pattern '"eyJ"' --query 'length(events)'
   ```

   It prints `0`: no JSON Web Token appears in the logs.

### Day to day

- Follow the logs: `aws logs tail /homefront/gateway --follow`.
- Ship new code: merge, then run `deploy/aws/deploy.sh --apply` again. The task restarts, so open chats drop for about
  a minute.
- Change an API key: edit your `.env`, then run `deploy/aws/deploy.sh --apply` (it rewrites the secret and restarts the
  task).
- Re-check the live site at any time: `deploy/aws/smoke.sh https://<id>.cloudfront.net/`.

### Tear down

1. Preview.

   ```bash
   deploy/aws/teardown.sh
   ```

   It lists the Cognito URL removal, the bucket, both stacks and the secret, each as `DRY-RUN: …`.

2. Remove everything this folder created.

   ```bash
   deploy/aws/teardown.sh --apply
   ```

   It ends with `Done (removed).` The Cognito pool and its users, the `mandate` and `homefront-*` tables, the policy
   store and the guardrail stay as they were.

## Cost per day (us-east-1, list prices, before taxes)

| Item | Per day |
|---|---|
| Fargate task, ARM64, 1 vCPU + 2 GB, 24 h | about $0.95 |
| Application Load Balancer, 24 h, light traffic | about $0.55 to $0.75 |
| Public IPv4 address of the task | $0.12 |
| CloudFront, S3, Secrets Manager, ECR storage, CloudWatch Logs | under $0.10 at demo traffic |
| **Fixed total** | **about $1.80 to $2.00 (roughly $60 a month)** |
| Bedrock (Claude Sonnet 5 per token, guardrail per text unit) | depends on use; each chat turn is a few thousand tokens |

Bedrock is the only cost that grows with testers. Watch it in Cost Explorer, and tear down when judging ends.

## If the event account is wiped

Workshop Studio accounts are temporary. Expect the event account, and everything in it, to be deleted after the event.

**What is lost:** the Cognito pool and every user, all DynamoDB data (`mandate`: staffers, sessions, decision events;
`homefront-*`: agents, approvals, audit), the Verified Permissions policy store, the guardrail, both stacks, the ECR
image and the secret. The code, the templates and this runbook are in git and survive.

**To redeploy in another AWS account** (your own, with Bedrock access to Claude Sonnet 5 enabled in us-east-1):

1. Point your shell at the new account and confirm it with `aws sts get-caller-identity`.
2. Rebuild the identity, data and policy layer with the existing scripts, dry run first, then `--apply`:
   `Backend/infra/cognito.sh`, `Backend/infra/setup.sh`, `Backend/infra/seed.py`, `agent/guardrail/create.sh`.
   Each prints the new ids.
3. Export the new ids so the deploy uses them instead of this event's:
   `COGNITO_USER_POOL_ID`, `COGNITO_STAFF_CLIENT_ID`, `COGNITO_AGENT_CLIENT_ID`, `COGNITO_DOMAIN`,
   `AVP_POLICY_STORE_ID`, `GUARDRAIL_ID`, `GUARDRAIL_VERSION`.
4. Run the Deploy steps above.

## Known limits

- One task means a deploy or a crash drops open chats for about a minute; ECS replaces a failed task by itself.
- A response that sends nothing for 60 seconds gets a 504 from CloudFront (its origin read timeout).
- The console URL is a `*.cloudfront.net` name. A custom domain needs an ACM certificate and DNS; this deploy has none.
- Workshop Studio accounts can carry service control policies. If a step fails with `AccessDenied` mentioning an
  "explicit deny in a service control policy", the event account forbids that service; redeploy in your own account.
