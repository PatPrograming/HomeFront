# HomeFront

AWS × A10 Hackathon — **Prompt 1: Agentic Identity**

Government-agency agents that email, post/reply on social, scrape the web, query **api.data.gov**, and run procurement market research on **USAspending.gov** and **SAM.gov** — behind **Amazon Cognito** user auth (data in **DynamoDB**) plus **agent workload identity**, fine-grained MCP tool grants, mid-task revoke, and **MITRE ATLAS** labels on every allow/deny.

## What the demo shows

1. Authenticate an agency agent (workload token)
2. Allow selected MCP tools (`web.scrape`, `datagov.search`, `email.send`, …)
3. Revoke a grant mid-task
4. Block the next call — response tagged **`AML.T0053`** / **`AML.M0028`**

ATLAS data is **vendored** from [mitre-atlas/atlas-data](https://github.com/mitre-atlas/atlas-data) (Agentic AI subset). Runtime does not call MITRE’s authoring API.

## Quick start

```bash
cp .env.example .env   # once — fill Cognito (from deploy), DATA_GOV_API_KEY, optional AWS/Bedrock

chmod +x scripts/*.sh
./scripts/run-gateway.sh    # API on :8080
./scripts/run-frontend.sh   # main app on :3000
```

### Demo (inside the product UI)

1. Open http://localhost:3000 → **Sign in** (or sign up) when Cognito is configured
2. Nav → **Monitoring** (http://localhost:3000/monitoring)
3. **Authenticate agent** → **1. datagov.search** (live Catalog data) → **2. email.send** (held for approval)
4. **3. web.scrape** → **2. email.send** again → blocked as **`tainted_session`** (**`AML.M0030`**): the session read untrusted content
5. **4. Revoke email.send** → **5. Retry** → blocked as **`AML.T0053`** / **`AML.M0028`**

- API docs: http://127.0.0.1:8080/docs  
- Everything is under `/api/v1`; Frontend proxies `/api` to :8080  
- User API: `/api/v1/home`, `/api/v1/health`, `/api/v1/me` (Cognito ID token)  
- Agent IAM: `/api/v1/auth/token`, `/api/v1/mcp/call`, `/api/v1/atlas/*`, `/api/v1/agents/...`  
- Set `AGENT_ADMIN_REQUIRES_LOGIN=true` in `.env` to require a Cognito login for `/agents*`, `/auth/token` and `/audit`  
- Agents are owner-scoped: a signed-in user sees and manages only their own agents plus ownerless demo agents (like `agent-comms-01`). Without a login, only the demo agents are reachable  
- Set `AGENT_STORE=dynamodb` to keep agents in the `<prefix>agents` DynamoDB table instead of memory  

### Env (one file for Backend + Frontend)

Copy [`.env.example`](.env.example) → `.env` at the **repo root**. Both the API and Vite frontend load it. Vite maps `COGNITO_*` into `VITE_COGNITO_*` — secrets like `DATA_GOV_API_KEY` and AWS keys never ship to the browser.

### AWS setup: Cognito + DynamoDB

```bash
AWS_REGION=us-east-1 ./infra/deploy.sh   # creates the user pool, app client, tables, IAM policy
```

Paste the printed values into the repo-root `.env`. The template is `infra/cloudformation/homefront.yaml`.

**Local data without AWS:** `./scripts/run-dynamodb-local.sh` starts DynamoDB Local in Docker and creates the tables. Then set `DYNAMODB_ENDPOINT_URL=http://localhost:8000` in `.env`. Sign-in still needs a real Cognito pool.

**Tests:** `pytest Backend/tests` uses an in-memory DynamoDB (moto). Against DynamoDB Local: `TEST_DYNAMODB_ENDPOINT_URL=http://localhost:8000 pytest Backend/tests`.

### Running agents (Claude on Amazon Bedrock)

1. In the AWS console, enable Bedrock model access for Claude Opus 5.5 (and Opus 4.8, the refusal fallback) in your region.
2. In `.env`, set `BEDROCK_REGION` (e.g. `us-east-1`) and either `AWS_BEARER_TOKEN_BEDROCK` or standard AWS credentials (`AWS_PROFILE`, access keys, SSO).
3. Restart the gateway, then start a run and poll it:

```bash
curl -X POST localhost:8080/api/v1/agents/agent-comms-01/runs \
  -H 'Content-Type: application/json' -d '{"task": "Find data.gov datasets on flood risk and summarize them"}'
curl localhost:8080/api/v1/agents/agent-comms-01/runs/<run_id>
```

The runner offers the model only the agent's granted tools, and sends every tool call through the same gateway checks as `/mcp/call`. A revoke mid-run blocks the next call. Runs stop after the agent's `max_steps`.

### Agent types and guardrails

Pick a type when creating an agent (`GET /api/v1/agent-types`). Each has a tool ceiling the gateway enforces at grant time and on every call:

| Type | Tools it may hold |
|------|-------------------|
| Official email | `email.send`, `email.send_list`, `datagov.search` |
| Social media | `social.post`, `social.reply` |
| Research | `web.scrape`, `datagov.search` |
| Procurement market research | `web.scrape`, `datagov.search`, `usaspending.search`, `sam.entity_lookup`, `report.save` |
| Custom | anything |

**Procurement market research** (FAR Part 10) finds past contract awards (`usaspending.search`), looks vendors up in SAM.gov (`sam.entity_lookup`, UEI / registration / small-business types) and saves a cited markdown report with `report.save`. Read reports at `GET /api/v1/agents/{id}/reports`. It can never email or post.

Gateway rules on top of grants:
- **SSRF guard:** `web.scrape` only reaches public http(s) hosts, never `169.254.169.254`, localhost or private ranges, and re-checks every redirect.
- **Taint tracking:** once a run (or workload-token session) executes a tool that returns untrusted content (`web.scrape`, `usaspending.search`, `sam.entity_lookup`), outbound tools (`email.*`, `social.*`) are denied for the rest of it with `tainted_session` (`AML.M0030`), even for custom agents. Approvals queued earlier stay approvable.
- **Domain allowlist:** an agent's `scrape_domains` (e.g. `["*.gov"]`, the market research default) limits which hosts `web.scrape` may fetch; others get `domain_not_allowed`.

External tools are simulated unless enabled: `HOMEFRONT_LIVE_SCRAPE=1`, `HOMEFRONT_LIVE_USASPENDING=1`, and `HOMEFRONT_LIVE_SAM=1` (needs `DATA_GOV_API_KEY`). See `.env.example`.

### api.data.gov key

1. Set in `.env`: `DATA_GOV_API_KEY=your_key_here`
2. Restart `./scripts/run-gateway.sh` — live Catalog search enables automatically. The key stays in the gateway (`AML.M0026`); agents never see it.
3. The same key serves `sam.entity_lookup` (api.sam.gov) when `HOMEFRONT_LIVE_SAM=1`.

## Layout

```
Backend/                   # FastAPI — Cognito auth, DynamoDB, agent IAM/ATLAS
Frontend/                  # Main product UI — Monitoring = agent IAM demo
packages/atlas-pack/       # vendored ATLAS Agentic pack + build script
packages/atlas-enricher/   # local ATLAS lookup + annotate
packages/identity-gateway/ # agent workload identity, grants, MCP authZ, Bedrock runner
packages/mcp-tools/        # email / social / scrape / data.gov / USAspending / SAM.gov / report tools
infra/                     # CloudFormation: Cognito, DynamoDB, IAM (deploy.sh)
references/                # design/reference assets
docs/ARCHITECTURE.md       # system design
```

## Rebuild ATLAS pack

```bash
source .venv/bin/activate
pip install -r packages/atlas-pack/requirements.txt
python packages/atlas-pack/scripts/build_agentic_pack.py
```
