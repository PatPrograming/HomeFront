# HomeFront

AWS × A10 Hackathon — **Prompt 1: Agentic Identity**

Government-office AI agents that read the inbox and cases, query **api.data.gov**, scrape the web, run procurement market research on **USAspending.gov** and **SAM.gov**, send email and post to social, each acting **for one staffer** under a short-lived **mandate token**. Staff sign in with **Amazon Cognito**; every MCP tool call is checked live against **DynamoDB** (session and staffer still active) and **Amazon Verified Permissions** (Cedar), scanned by Bedrock Guardrails, and logged with **MITRE ATLAS** labels. Fail closed everywhere.

## How the pieces fit

Three processes run on your machine. The console talks only to the gateway, and the gateway starts one agent per conversation.

```
 Browser                      your Mac                                         AWS (us-east-1)
┌──────────────┐  /api   ┌───────────────────────────────────────────┐   ┌──────────────────────┐
│ Console      │ ──────► │ Gateway  (FastAPI, 127.0.0.1:8080)         │──►│ Cognito user pool    │
│ Vite, :3000  │         │  - checks the Cognito login                │   │ DynamoDB: mandate,   │
│ chat, review │         │  - session + mandate-token broker          │   │   homefront-*        │
│ Agents, Run a│         │  - /mcp tool server, guarded() per call:   │   │ Verified Permissions │
│ task, ...    │         │    DynamoDB check, Cedar decision,         │   │   (Cedar)            │
└──────────────┘         │    Guardrail scan, drafts held for review, │   │ Bedrock Guardrail    │
                         │    decision events                         │   │ Bedrock (Claude)     │
                         │  - starts the agent (autostart)            │   └──────────────────────┘
                         └──────────────┬────────────────▲────────────┘              ▲
                                spawns  │                │ /mcp + /api/v1/agent      │ Claude Sonnet 5
                                        ▼                │ (mandate token)           │
                         ┌───────────────────────────────┴────────────┐              │
                         │ Agent  agent/agent.py --sid <sid> --chat    │──────────────┘
                         │ Strands on Bedrock us.anthropic.claude-sonnet-5 │
                         └─────────────────────────────────────────────┘
```

- **Console** (`Frontend/`, Vite on `http://localhost:3000`): sign-in, the chat, the review queue, and the teammates' tabs (Agents, Run a task, Approvals, Activity, Monitoring). It proxies `/api` to the gateway.
- **Gateway** (`Backend/` + `packages/identity-gateway/`, FastAPI on `127.0.0.1:8080`): checks the Cognito login, creates sessions and issues mandate tokens (the broker), serves the tools at `/mcp`, and runs `guarded()` on every call: DynamoDB check, Verified Permissions (Cedar) decision, Bedrock Guardrail scan, drafts held for review, decision events.
- **Agent** (`agent/`): `agent/agent.py --sid <sid> --chat`, a Strands agent on Bedrock (`us.anthropic.claude-sonnet-5`). With `HOMEFRONT_AUTOSTART_AGENT=1`, the gateway starts one per conversation on the staffer's first message.
- The teammates' tabs use the `homefront-*` DynamoDB tables behind the same Cognito login.

## What the demo shows

1. **Authenticate an agent:** Maya signs in (Cognito) and sends her first chat message, which starts a session (`POST /api/v1/sessions`); the agent uses its own Cognito client credentials to get a 15-minute mandate token for that session (`POST /api/v1/sessions/{sid}/mandate`).
2. **Allow selected MCP tools:** the agent calls the tools at `/mcp` with that token; Cedar allows routine work and denies bulk export, unknown recipients and campaign posts.
3. **Revoke mid-task:** the Chief of Staff presses **Offboard** (`POST /api/v1/staff/{id}/offboard`).
4. **Block subsequent requests:** the agent's very next call is denied (`session revoked / delegator inactive`) and shows up in the live feed (`GET /api/v1/events`).

There is no route that issues an agent token on its own. How the gateway decides is in [`packages/identity-gateway/README.md`](packages/identity-gateway/README.md); lane setup in [`docs/LANES.md`](docs/LANES.md).

ATLAS data is **vendored** from [mitre-atlas/atlas-data](https://github.com/mitre-atlas/atlas-data) (Agentic AI subset). Runtime does not call MITRE's authoring API.

## Who can run it, who can use it

- **Run it (team members only).** Running the gateway needs the team's AWS workshop credentials, the agent's Cognito client secret and your own signing secret. Only team members have these.
- **Use it (everyone else: judges, testers).** Open the running console and sign in with an account an admin created for you. There is no sign-up. Each account gets only its role's access: `staffer` chats with the assistant and reviews its drafts; `admin` also offboards staff and sees the audit log and technical details.

## Run it on your machine

This runs the gateway, the agent and the console on your Mac against the team's AWS account. No Docker needed. Run every command from the repo root.

### Prerequisites

- Python 3.12 or newer. Check with `python3.12 --version`; install with `brew install python@3.12` or from [python.org](https://www.python.org/downloads/).
- Node 18 or newer. Check with `node --version` (tested on Node 20).
- Access to the team's AWS workshop event page (to get temporary AWS credentials).
- From Huy, privately (never through git or a group chat): the `maya` password (the shared test account for now) and the value of `COGNITO_AGENT_CLIENT_SECRET`.

### 1. Get the latest code

```bash
git checkout main && git pull
```

This gets the current gateway, agent and console.

### 2. Create your env file

```bash
cp .env.example .env
```

The public Cognito ids, the guardrail id and the API key signup links are already filled in. If you are reusing an old `.env` instead, delete every line that contains `x2DFWiwCM` or `3jrik179f910ridan71eb7pmhg`: those point at the retired Cognito pool.

### 3. Generate your signing secret

```bash
python3.12 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the output after `MANDATE_SIGNING_SECRET=` in `.env`. Use your own value; it signs the mandate tokens your gateway issues.

### 4. Check the policy store id

```bash
grep AVP_POLICY_STORE_ID .env
```

It works when it prints `AVP_POLICY_STORE_ID=Fmx8fLovCMpXgABUM1bHhb`.

### 5. Turn on the agent and the teammates' tabs

```bash
cat >> .env <<'EOF'
HOMEFRONT_AUTOSTART_AGENT=1
DYNAMODB_TABLE_PREFIX=homefront-
AGENT_STORE=dynamodb
BEDROCK_REGION=us-east-1
EOF
```

`HOMEFRONT_AUTOSTART_AGENT=1` makes the gateway start the chat agent on your first message. The other three point the Agents, Run a task, Approvals, Activity and Monitoring tabs at the shared `homefront-*` tables and at Bedrock.

### 6. Optional: add government API keys

Paste a free key after `DATA_GOV_API_KEY=` in `.env` (signup links for every key are listed in `.env.example`, under "Government API keys"). Without keys, those tools return stub results. See [api.data.gov key](#apidatagov-key).

### 7. Set up the agent's Python environment

```bash
scripts/setup-agent.sh
```

It works when it prints `agent/.venv is ready.` This creates `agent/.venv` with Python 3.12+ and installs `agent/requirements.txt`.

### 8. Create the agent's env file

```bash
cp agent/.env.example agent/.env
```

Then paste the secret from Huy after `COGNITO_AGENT_CLIENT_SECRET=` in `agent/.env`. This is the agent's own Cognito identity. Never commit it.

### 9. Load AWS credentials in the gateway's terminal

1. Open the team's AWS workshop event page and click **Get AWS CLI credentials**.
2. Copy the **macOS/Linux** block (the `export AWS_ACCESS_KEY_ID=...` lines).
3. Open a new terminal, `cd` to the repo root, and paste the block.
4. Check it:

   ```bash
   aws sts get-caller-identity
   ```

   It works when it prints the team account's `Account` and `Arn`.

> **These are the team account's credentials and expire after a few hours.** Keep them in this terminal only; never put them in `.env` or `agent/.env`. When they expire, paste a fresh block and restart the gateway (step 10).

### 10. Start the gateway

In the terminal from step 9:

```bash
./scripts/run-gateway.sh
```

It works when it prints `Uvicorn running on http://127.0.0.1:8080`. The first run creates `.venv` and installs `Backend/requirements.txt`. The agent it starts inherits these AWS credentials.

### 11. Start the console

In a second terminal:

```bash
./scripts/run-frontend.sh
```

It works when it prints `Local:   http://localhost:3000/`. The first run installs `Frontend/node_modules`.

### 12. Sign in

Open http://localhost:3000, click **Sign in with Cognito**, and sign in as `maya` with the password from Huy.

It works when the console shows **Your assistant**.

### 13. Talk to the assistant

Type `Check my email` in the chat and press Enter.

It works when the gateway terminal prints `agent started sid=...`, the assistant replies within about a minute, and drafts appear under **Needs your review**.

## Use the chat assistant

- **Ask in plain words.** For example: `Check my email, answer what you can and show me what I need to review.` or `Post on the official account: Town hall this Tuesday, 6 pm, at the Central Library.` The two suggestions on an empty chat send these for you.
- **Your first message starts a conversation.** It creates a session that lasts one hour; the agent's mandate tokens inside it last 15 minutes and renew themselves.
- **Emails and posts wait for you.** The agent's emails and posts land in **Needs your review** as drafts and are not sent. Edit the text if you want, then click **Approve and send** (or **Approve and post**) or **Reject**. You can't change the recipient or the account: those are what Cedar checked.
- **Let it send a kind of message alone.** **Approve and allow for this session** sends the draft and lets the agent send that kind of message alone until the conversation ends. After a few drafts of one kind are approved unchanged, **What it can send without asking** offers **Let it send these** for good. Sensitive mail, staff memos, suspicious mail and new conversations always need your review.
- **Denied actions did not happen.** When the gateway denies a call, the reply says which action and which policy (for example `forbid-bulk-export`). The agent does not retry it.
- **Revoked conversation.** If an admin offboards you or the session is revoked, the console shows "This conversation's session was revoked" and the agent stops. Click **Start a new conversation** to begin again (the broker still refuses an offboarded staffer).

## Troubleshooting the chat assistant

Messages under the chat box come from the console; everything else prints in the gateway's terminal (the agent's output goes there too).

| What you see | Where | Cause and fix |
|---|---|---|
| `The assistant could not start: agent/.venv is not set up on the server` | Under the chat box | Run `scripts/setup-agent.sh`, then send your message again. No restart needed. |
| `The assistant could not start: agent/.env is missing on the server, so the agent has no Cognito identity` | Under the chat box | Do step 8, then send again. |
| `No reply yet: the assistant isn't answering this session.` and no `agent started` line in the gateway terminal | Chat | Autostart is off. Add `HOMEFRONT_AUTOSTART_AGENT=1` to `.env`, restart the gateway, send again. Or [run the agent by hand](#run-the-agent-by-hand). |
| `No reply yet...` and `KeyError: 'COGNITO_AGENT_CLIENT_SECRET'` | Chat, gateway terminal | `COGNITO_AGENT_CLIENT_SECRET=` in `agent/.env` is empty. Paste the secret, then send again. |
| `Stopping: Cognito refused the agent's client credentials (HTTP ...)` | Gateway terminal | The agent secret is wrong (or `agent/.env` still has old-pool values). Copy `agent/.env.example` again, paste the secret Huy gave you, then send again. |
| `The assistant could not start: it stopped 3 times in this session, so it is not restarted again` | Under the chat box | The agent crashed three times. Fix the error printed in the gateway terminal, restart the gateway (that resets the count), then send again. |
| `HTTP 500` under the chat box, `NoCredentialsError: Unable to locate credentials` in the terminal | Both | The gateway's terminal has no AWS credentials. Do step 9 in that terminal, then restart the gateway. |
| `ExpiredTokenException` or "The security token included in the request is expired", or tool calls suddenly come back `DENIED` with `guarded(...) failed; denying` in the terminal | Gateway terminal | The workshop credentials expired. Paste a fresh block (step 9), restart the gateway, send again. |
| `AccessDeniedException` from Bedrock in the agent's traceback, then `No reply yet...` | Gateway terminal, chat | The credentials are not the team account's, or Bedrock in `us-east-1` refuses the model. Run `aws sts get-caller-identity` to check the account, then ask Huy. |
| `guardrail scan failed` | Gateway terminal | Tool results still arrive, marked `SCAN_ERROR`. Check `.env` has `GUARDRAIL_ID=l74ju5pcrcdg` and `GUARDRAIL_VERSION=2`, and that the credentials are fresh. |
| `RuntimeError: MANDATE_SIGNING_SECRET must be set to at least 32 characters` (console shows `HTTP 500`) | Gateway terminal | Do step 3, then restart the gateway. |
| `AVP_POLICY_STORE_ID is not set (run Backend/infra/setup.sh)` | Gateway terminal | Do step 4: set `AVP_POLICY_STORE_ID=Fmx8fLovCMpXgABUM1bHhb` in `.env`, restart the gateway. |
| `Stopping: the console refused the agent's token (HTTP 401)` after you changed `MANDATE_SIGNING_SECRET` | Gateway terminal | Tokens signed with the old secret no longer verify. Restart the gateway, then click **Start a new conversation** (or open the console in a new tab). |
| `Session revoked or staffer inactive` | Under the chat box | The session was revoked or you were offboarded. Click **Start a new conversation**; if it says `Staffer is not active`, ask an admin to reinstate you. |
| `Authentication failed` | Under the chat box | Your Cognito login is stale (for example from the old pool). Sign out from your account page, then sign in again. |
| `Cognito is not configured` | Sign-in page | `VITE_COGNITO_DOMAIN` or `VITE_COGNITO_CLIENT_ID` is missing from `.env`. Copy them from `.env.example`, then restart `./scripts/run-frontend.sh` (Vite reads `.env` only at start). |
| `redirect_mismatch` on the Cognito page | Browser | Open the console at exactly `http://localhost:3000`, not `127.0.0.1:3000` or another port. Cognito accepts only that address. |
| `Port 3000 is already in use` | Console terminal | Run `lsof -iTCP:3000 -sTCP:LISTEN`, stop that process, then restart `./scripts/run-frontend.sh`. |
| `error while attempting to bind on address ('127.0.0.1', 8080)` ... `address already in use` | Gateway terminal | Another gateway holds port 8080. Run `lsof -iTCP:8080 -sTCP:LISTEN`, stop it, then restart `./scripts/run-gateway.sh`. |
| `error: HomeFront needs Python 3.12 or newer` | Any terminal | Install Python 3.12, open a new terminal, delete any half-made `.venv` or `agent/.venv`, then rerun the script. |

## Run the agent by hand

Use this when autostart is off (`HOMEFRONT_AUTOSTART_AGENT` unset) or to watch the agent's output on its own.

1. Send a message in the console to start a conversation.
2. Find its session id. Sign in as an admin: **Technical details** at the bottom of the console shows the session card with a copy button, and the empty chat shows the full command. As a staffer, open the browser's developer tools and read `mandate.card` under **Session Storage** for `http://localhost:3000`; the `sid` field is the id.
3. In a terminal with the AWS credentials from step 9, from the repo root:

   ```bash
   agent/.venv/bin/python agent/agent.py --sid <sid> --chat
   ```

   It works when it prints `Chat ready: answering http://127.0.0.1:8080's console for this session. Ctrl-C to quit.` It answers the chat until the session is revoked, then exits with code 3.

More agent options (the stub gateway, the staged attacker, the guardrail) are in [`agent/README.md`](agent/README.md).

## Reference

Other useful pages: API docs at http://127.0.0.1:8080/docs (everything except `/mcp` is under `/api/v1`; the console proxies `/api` to :8080), `/console` (staffer), `/admin` (Chief of Staff: offboard, audit log), `/account` (`GET /api/v1/me`). Set `VITE_MOCK_API=1` in `.env` for a no-backend demo.

- AWS resources (Cognito pool `us-east-1_MLfQpodxG`, the `mandate` DynamoDB table, the AVP policy store) are created by `Backend/infra/cognito.sh` and `Backend/infra/setup.sh`; see [`Backend/infra/README.md`](Backend/infra/README.md).
- `infra/` holds the teammates' CloudFormation stack for the `homefront-*` tables; see [`infra/README.md`](infra/README.md).

### Env (one file for Backend + Frontend)

Copy [`.env.example`](.env.example) to `.env` at the **repo root**. Both the API and the Vite frontend load it. Only `VITE_*` values reach the browser, and none of them are secrets. The agent reads its own `agent/.env`; the gateway passes it only AWS credentials, region, `PATH` and the gateway URLs, never `MANDATE_SIGNING_SECRET`.

### Chat agent auto-start

With `HOMEFRONT_AUTOSTART_AGENT=1`, the staffer's first chat message starts `agent/agent.py --sid <sid> --chat` as a child of the gateway: one process per session, stopped on revoke, offboarding, session end or gateway shutdown, at most 3 starts per session. Starts and stops print in the gateway's terminal. Details in [`agent/README.md`](agent/README.md) and `Backend/app/services/agent_supervisor.py`.

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
Backend/                   # FastAPI: Cognito auth, broker (/sessions), chat + review, offboard, events, agent supervisor; infra/ scripts
Frontend/                  # React + TypeScript: public site, staff console, admin page
agent/                     # the AI agent (Claude on Bedrock) that calls /mcp with a mandate token
packages/atlas-pack/       # vendored ATLAS Agentic pack + build script
packages/atlas-enricher/   # local ATLAS lookup + annotate
packages/guardrail-scanner/ # Bedrock Guardrail scan of untrusted tool output
packages/identity-gateway/ # /mcp endpoint and guarded(): token, DynamoDB, Cedar, guardrail, events
packages/mandate-contracts/ # shared tool names, token claims, events, DynamoDB keys, Cedar
packages/mcp-tools/        # email / social / scrape / data.gov / USAspending / SAM.gov / report tools
scripts/                   # run-gateway.sh, run-frontend.sh, setup-agent.sh, DynamoDB Local helpers
infra/                     # teammates' CloudFormation stack (homefront-* tables)
references/                # design/reference assets
docs/ARCHITECTURE.md       # system design
```

## Rebuild ATLAS pack

```bash
source .venv/bin/activate
pip install -r packages/atlas-pack/requirements.txt
python packages/atlas-pack/scripts/build_agentic_pack.py
```
