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

## Quick start

```bash
cp .env.example .env   # once: public Cognito ids are filled in; add MANDATE_SIGNING_SECRET, AVP ids, DATA_GOV_API_KEY

chmod +x scripts/*.sh
./scripts/run-gateway.sh    # API + /mcp on :8080
./scripts/run-frontend.sh   # console on :3000 (VITE_MOCK_API=1 for a no-backend demo)
```

- API docs: http://127.0.0.1:8080/docs. Everything except `/mcp` is under `/api/v1`; the frontend proxies `/api` to :8080.
- Console pages: `/console` (staffer: start the assistant, live decisions), `/admin` (Chief of Staff: offboard, audit log), `/account` (`GET /api/v1/me`).
- AWS resources (Cognito pool `us-east-1_MLfQpodxG`, the `mandate` DynamoDB table, the AVP policy store) are created by `Backend/infra/cognito.sh` and `Backend/infra/setup.sh`; see [`Backend/infra/README.md`](Backend/infra/README.md).
- `infra/` holds the teammates' alternative CloudFormation stack (its own pool and `homefront-*` tables). The running app does not use it; see [`infra/README.md`](infra/README.md).

### Env (one file for Backend + Frontend)

Copy [`.env.example`](.env.example) to `.env` at the **repo root**. Both the API and the Vite frontend load it. Only `VITE_*` values reach the browser, and none of them are secrets.

### Chat agent auto-start

Set `HOMEFRONT_AUTOSTART_AGENT=1` in `.env` and restart `./scripts/run-gateway.sh`: the staffer's first chat message then starts the agent (`agent/agent.py --sid <sid> --chat`) for them, so nobody needs a terminal. It needs `agent/.venv` and `agent/.env` on the server; starts and stops print in the gateway's terminal. Off by default. Details in [`agent/README.md`](agent/README.md).

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
