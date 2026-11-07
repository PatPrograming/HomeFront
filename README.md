# HomeFront

AWS × A10 Hackathon — **Prompt 1: Agentic Identity**

Government-agency agents that email, post/reply on social, scrape the web, and query **api.data.gov** — behind **Supabase user auth** plus **agent workload identity**, fine-grained MCP tool grants, mid-task revoke, and **MITRE ATLAS** labels on every allow/deny.

## What the demo shows

1. Authenticate an agency agent (workload token)
2. Allow selected MCP tools (`web.scrape`, `datagov.search`, `email.send`, …)
3. Revoke a grant mid-task
4. Block the next call — response tagged **`AML.T0053`** / **`AML.M0028`**

ATLAS data is **vendored** from [mitre-atlas/atlas-data](https://github.com/mitre-atlas/atlas-data) (Agentic AI subset). Runtime does not call MITRE’s authoring API.

## Quick start

```bash
# Optional: copy Backend/.env.example → Backend/.env and fill Supabase keys
# (agent IAM demo works with defaults; /api/v1/me needs real Supabase)

chmod +x scripts/*.sh
./scripts/run-gateway.sh   # unified API on :8080

# other terminal
./scripts/run-demo.sh      # agent IAM console on :5173
```

- API docs: http://127.0.0.1:8080/docs  
- User API: `/api/v1/health`, `/api/v1/me` (Supabase JWT)  
- Agent IAM: `/auth/token`, `/mcp/call`, `/atlas/*`, `/agents/...`  

Optional: `DATA_GOV_API_KEY` and `HOMEFRONT_LIVE_DATAGOV=1` for live api.data.gov (key never returned to the agent).

## Layout

```
Backend/                   # FastAPI — Supabase auth + mounted agent IAM/ATLAS
Frontend/                  # User-facing app (supabase-js) — see Frontend/README
packages/atlas-pack/       # vendored ATLAS Agentic pack + build script
packages/atlas-enricher/   # local ATLAS lookup + annotate
packages/identity-gateway/ # agent workload identity, grants, MCP authZ
packages/mcp-tools/        # email / social / scrape / data.gov tools
apps/demo-console/         # operator UI for the agent IAM demo
supabase/migrations/       # profiles + RLS
docs/ARCHITECTURE.md       # system design
```

## Rebuild ATLAS pack

```bash
source .venv/bin/activate
pip install -r packages/atlas-pack/requirements.txt
python packages/atlas-pack/scripts/build_agentic_pack.py
```
