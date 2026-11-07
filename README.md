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

# other terminals
./scripts/run-frontend.sh  # main app on :3000 (copy Frontend/.env.example → Frontend/.env first)
./scripts/run-demo.sh      # agent IAM console on :5173
```

- API docs: http://127.0.0.1:8080/docs  
- Everything is under `/api/v1`; both frontends proxy `/api` to :8080  
- User API: `/api/v1/home`, `/api/v1/health`, `/api/v1/me` (Supabase JWT)  
- Agent IAM: `/api/v1/auth/token`, `/api/v1/mcp/call`, `/api/v1/atlas/*`, `/api/v1/agents/...`  
- Set `AGENT_ADMIN_REQUIRES_LOGIN=true` in `Backend/.env` to require a Supabase login for `/agents*`, `/auth/token` and `/audit`  
- Set `AGENT_STORE=supabase` to keep agents in `public.agents` instead of memory (apply `supabase/migrations/` first; needs `SUPABASE_SERVICE_ROLE_KEY`)  

### api.data.gov key

1. Copy [`Backend/.env.example`](Backend/.env.example) → `Backend/.env`
2. Paste your key:

```bash
DATA_GOV_API_KEY=your_key_here
```

3. Restart `./scripts/run-gateway.sh` — live Catalog search enables automatically. The key stays in the gateway (`AML.M0026`); agents never see it.

## Layout

```
Backend/                   # FastAPI — Supabase auth + mounted agent IAM/ATLAS
Frontend/                  # User-facing React app (supabase-js) — see Frontend/README
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
