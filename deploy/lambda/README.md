# Deploy HomeFront to AWS Lambda

```
browser ──► Lambda function URL (https://….lambda-url.us-east-1.on.aws)
              │  homefront-api: console, /api/v1, /mcp
              │  ├─ "Run a task": invokes itself asynchronously, the run executes in its own invocation
              │  └─ first chat message of a session: ecs:RunTask
              ▼
            ECS Fargate task per chat session (homefront-agent: agent/agent.py --sid <sid> --chat)
              └─ calls the function URL's /mcp with its mandate token, like any outside agent
```

It uses the existing Cognito pool, `mandate` table, `homefront-*` tables, Verified Permissions policy store and guardrail. It adds one table, `homefront-runtime`.

## Why the code changed for Lambda

Locally the gateway is one long-lived process. On Lambda each request can land on a different, short-lived process, and a process is frozen once it has answered. So:

| Was | On Lambda |
|---|---|
| Taint marks (`tainted_session`) and workload tokens in memory | `homefront-runtime` table (`runtime_repo.py`). A taint written by one process blocks outbound tools in every other. If the table can't be reached, the app won't start; it never falls back to memory. |
| "Run a task" ran after the response (`BackgroundTasks`), and runs were kept in memory | The run is stored, then executed by an async self-invoke (`homefront_job: run`, up to 15 min). Progress is saved every 2 s, so polling from any process shows it. Each run executes once and is never retried. |
| Chat agent as a child process of the server | One Fargate task per session (`EcsAgentSupervisor`). The session-to-task mapping is in the runtime table, and a conditional write stops two messages from starting two tasks. Offboarding stops the staffer's tasks. A Lambda process ending stops nobody. |
| Console served by Vite | Served by the API itself (`FRONTEND_DIST`), so the console, `/api/v1` and `/mcp` share one origin |

Locally nothing changes: without `HOMEFRONT_RUNTIME_TABLE` / `HOMEFRONT_AGENT_LAUNCHER=ecs`, everything works as before.

Secrets are split the same way as locally. `homefront/config` holds the API's settings and keys and is loaded by the function at cold start. `homefront/agent` holds only the agent's Cognito client secret and is readable only by the agent tasks.

## Deploy

Needs AWS credentials (`aws sts get-caller-identity` works) and Docker running. Run each line on its own; zsh doesn't treat `#` as a comment when you paste.

```bash
.venv/bin/python deploy/lambda/deploy.py
```

That's a dry run: it lists the settings and stops on anything missing. Then, for the first deploy (about 10 minutes, mostly building images):

```bash
.venv/bin/python deploy/lambda/deploy.py --apply --update-cognito
```

Each later deploy uses the same URL:

```bash
.venv/bin/python deploy/lambda/deploy.py --apply
```

`--update-cognito` adds the function URL to the `staff-web` client's callback and logout URLs. It keeps the existing ones (`localhost:3000` still works). You only need it once.

The script:
1. builds both images for arm64 and pushes them to the ECR repo `homefront`
2. writes the two secrets
3. deploys the CloudFormation stack `homefront` (`stack.yaml`) in the default VPC
4. waits for `/api/v1/health`

## Check it

- `https://<url>/console`: sign in as Maya and send a message. The first reply takes about 30–60 s while the agent task starts.
- Logs: CloudWatch `/aws/lambda/homefront-api` (API and runs) and `/homefront/agent` (chat agents).
- Running agents: ECS console › cluster `homefront` › Tasks.

## Things to know

- **Cost:** Lambda is billed per request (close to $0 at demo traffic). An agent task costs about $0.025/hour while a session is open. The secrets cost $0.80/month.
- **First request after idle** has a cold start of a few seconds.
- **Sessions last an hour.** The agent task exits when its session ends or is revoked. Offboarding stops it immediately.
- **Remove everything:**

  ```bash
  aws cloudformation delete-stack --stack-name homefront
  ```

  Then delete the ECR repo `homefront` and the secrets `homefront/config` and `homefront/agent`.
