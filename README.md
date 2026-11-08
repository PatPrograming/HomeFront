# HomeFront

AWS × A10 Hackathon — **Prompt 1: Agentic Identity**

Government-office AI agents that read the inbox and cases, query **api.data.gov**, scrape the web, run procurement market research on **USAspending.gov** and **SAM.gov**, send email and post to social, each acting **for one staffer** under a short-lived **mandate token**. Staff sign in with **Amazon Cognito**; every MCP tool call is checked live against **DynamoDB** (session and staffer still active) and **Amazon Verified Permissions** (Cedar), scanned by Bedrock Guardrails, and logged with **MITRE ATLAS** labels. Fail closed everywhere.

## What the demo shows

1. **Authenticate an agent:** Maya signs in (Cognito), presses **Start assistant** (`POST /api/v1/sessions`); the agent uses its own Cognito client credentials to get a 15-minute mandate token for that session (`POST /api/v1/sessions/{sid}/mandate`).
2. **Allow selected MCP tools:** the agent calls the 7 tools at `/mcp` with that token; Cedar allows routine work and denies bulk export, unknown recipients and campaign posts.
3. **Revoke mid-task:** the Chief of Staff presses **Offboard** (`POST /api/v1/staff/{id}/offboard`).
4. **Block subsequent requests:** the agent's very next call is denied (`session revoked / delegator inactive`) and shows up in the live feed (`GET /api/v1/events`).

There is no route that issues an agent token on its own. How the gateway decides is in [`packages/identity-gateway/README.md`](packages/identity-gateway/README.md); lane setup in [`docs/LANES.md`](docs/LANES.md).

ATLAS data is **vendored** from [mitre-atlas/atlas-data](https://github.com/mitre-atlas/atlas-data) (Agentic AI subset). Runtime does not call MITRE's authoring API.

## Run it on your machine

This runs the gateway and the console on your Mac against the team's AWS account. No Docker needed.

### Prerequisites

- Python 3.12 or newer (`python3.12 --version`). Install it from [python.org](https://www.python.org/downloads/) or with `brew install python@3.12`.
- Node 18 or newer (`node --version`; tested on Node 20). The console uses Vite 5.
- AWS credentials for the team account, region `us-east-1`, in your shell. Run `aws configure` or export `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (plus `AWS_SESSION_TOKEN` for temporary keys). The gateway reads them from the shell; never put them in `.env`.
- A console account. An admin creates it for you; there is no sign-up.

### Steps

Run every command from the repo root.

1. Copy the env file.

   ```bash
   cp .env.example .env
   ```

   The public Cognito ids, the AVP policy store id and the guardrail id are already filled in.

2. Generate a signing secret.

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Paste the output after `MANDATE_SIGNING_SECRET=` in `.env`.

3. Optional: get a free key at [api.data.gov/signup](https://api.data.gov/signup/) and paste it after `DATA_GOV_API_KEY=` in `.env`. Without it, data.gov search returns stub results.

4. Set up the agent's Python environment.

   ```bash
   scripts/setup-agent.sh
   ```

   This creates `agent/.venv` with Python 3.12+ and installs `agent/requirements.txt`.

5. Copy the agent's env file.

   ```bash
   cp agent/.env.example agent/.env
   ```

6. Ask Huy privately for the agent client secret and paste it after `COGNITO_AGENT_CLIENT_SECRET=` in `agent/.env`. Never commit it or paste it in a chat channel.

7. Optional: add `HOMEFRONT_AUTOSTART_AGENT=1` to `.env` so the console starts the agent on your first chat message.

8. Start the gateway in one terminal.

   ```bash
   scripts/run-gateway.sh
   ```

   The first run creates `.venv` and installs `Backend/requirements.txt`, then serves the API and `/mcp` on `127.0.0.1:8080`.

9. Start the console in a second terminal.

   ```bash
   scripts/run-frontend.sh
   ```

   The first run installs `Frontend/node_modules`, then serves the console on port 3000.

10. Open http://localhost:3000 and sign in with the account the admin gave you.

### How to tell it worked

- The gateway terminal shows `Uvicorn running on http://127.0.0.1:8080`, and http://127.0.0.1:8080/docs lists the API.
- After sign-in, http://localhost:3000/account shows your account, and `/console` loads without an error.

### Troubleshooting

- **Gateway returns 500 with `NoCredentialsError`:** the shell that started `scripts/run-gateway.sh` has no AWS credentials. Run `aws sts get-caller-identity` in that shell, fix the credentials, then restart the gateway.
- **Cognito shows a redirect or `redirect_mismatch` error:** open the console at exactly `http://localhost:3000`, not `127.0.0.1:3000` or another port. Cognito only accepts that address.
- **`Port 3000 is already in use`:** the console only runs on 3000, because Cognito accepts no other port. Find the process with `lsof -iTCP:3000 -sTCP:LISTEN`, stop it, then restart `scripts/run-frontend.sh`.
- **`error: HomeFront needs Python 3.12 or newer`:** install Python 3.12, open a new terminal, delete any half-made `.venv` or `agent/.venv`, then rerun the script.

Other useful pages: API docs at http://127.0.0.1:8080/docs (everything except `/mcp` is under `/api/v1`; the console proxies `/api` to :8080), `/console` (staffer), `/admin` (Chief of Staff: offboard, audit log), `/account` (`GET /api/v1/me`). Set `VITE_MOCK_API=1` in `.env` for a no-backend demo.

- AWS resources (Cognito pool `us-east-1_MLfQpodxG`, the `mandate` DynamoDB table, the AVP policy store) are created by `Backend/infra/cognito.sh` and `Backend/infra/setup.sh`; see [`Backend/infra/README.md`](Backend/infra/README.md).
- `infra/` holds the teammates' alternative CloudFormation stack (its own pool and `homefront-*` tables). The running app does not use it; see [`infra/README.md`](infra/README.md).

### Env (one file for Backend + Frontend)

Copy [`.env.example`](.env.example) to `.env` at the **repo root**. Both the API and the Vite frontend load it. Only `VITE_*` values reach the browser, and none of them are secrets.

### Chat agent auto-start

Set `HOMEFRONT_AUTOSTART_AGENT=1` in `.env` and restart `scripts/run-gateway.sh`: the staffer's first chat message then starts the agent (`agent/agent.py --sid <sid> --chat`) for them, so nobody needs a terminal. It needs `agent/.venv` and `agent/.env` on the server; starts and stops print in the gateway's terminal. Off by default. Details in [`agent/README.md`](agent/README.md).

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
Backend/                   # FastAPI: Cognito auth, broker (/sessions), offboard, events; infra/ scripts
Frontend/                  # React + TypeScript: public site, staff console, admin page
agent/                     # the AI agent (Claude on Bedrock) that calls /mcp with a mandate token
packages/atlas-pack/       # vendored ATLAS Agentic pack + build script
packages/atlas-enricher/   # local ATLAS lookup + annotate
packages/identity-gateway/ # /mcp endpoint and guarded(): token, DynamoDB, Cedar, guardrail, events
packages/mandate-contracts/ # shared tool names, token claims, events, DynamoDB keys, Cedar
packages/mcp-tools/        # email / social / scrape / data.gov / USAspending / SAM.gov / report tools
infra/                     # teammates' alternative CloudFormation stack (not used by the app)
references/                # design/reference assets
docs/ARCHITECTURE.md       # system design
```

## Rebuild ATLAS pack

```bash
source .venv/bin/activate
pip install -r packages/atlas-pack/requirements.txt
python packages/atlas-pack/scripts/build_agentic_pack.py
```
