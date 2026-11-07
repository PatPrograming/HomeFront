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
cp .env.example .env   # once — fill SUPABASE_* and DATA_GOV_API_KEY

chmod +x scripts/*.sh
./scripts/run-gateway.sh    # API on :8080
./scripts/run-frontend.sh   # main app on :3000
```

### Demo (inside the product UI)

1. Open http://localhost:3000 → **Sign in** (or sign up)
2. Nav → **Monitoring** (http://localhost:3000/monitoring)
3. **Authenticate agent** → **2. datagov.search** (live Catalog data) → **3. email.send**
4. **4. Revoke email.send** → **5. Retry** → blocked as **`AML.T0053`** / **`AML.M0028`**

- API docs: http://127.0.0.1:8080/docs  
- Everything is under `/api/v1`; Frontend proxies `/api` to :8080  
- User API: `/api/v1/home`, `/api/v1/health`, `/api/v1/me` (Supabase JWT)  
- Agent IAM: `/api/v1/auth/token`, `/api/v1/mcp/call`, `/api/v1/atlas/*`, `/api/v1/agents/...`  
- Set `AGENT_ADMIN_REQUIRES_LOGIN=true` in `.env` to require a Supabase login for `/agents*`, `/auth/token` and `/audit`  

### Env (one file for Backend + Frontend)

Copy [`.env.example`](.env.example) → `.env` at the **repo root**. Both the API and Vite frontend load it (`SUPABASE_*` for the server; Vite maps the same URL/anon key into the browser — service role and `DATA_GOV_API_KEY` never ship to the client).

### api.data.gov key

1. Set in `.env`: `DATA_GOV_API_KEY=your_key_here`
2. Restart `./scripts/run-gateway.sh` — live Catalog search enables automatically. The key stays in the gateway (`AML.M0026`); agents never see it.

## Layout

```
Backend/                   # FastAPI — Supabase auth + mounted agent IAM/ATLAS
Frontend/                  # Main product UI — Monitoring = agent IAM demo
packages/atlas-pack/       # vendored ATLAS Agentic pack + build script
packages/atlas-enricher/   # local ATLAS lookup + annotate
packages/identity-gateway/ # agent workload identity, grants, MCP authZ
packages/mcp-tools/        # email / social / scrape / data.gov tools
references/                # design/reference assets
supabase/migrations/       # profiles + RLS
docs/ARCHITECTURE.md       # system design
```

## Rebuild ATLAS pack

```bash
source .venv/bin/activate
pip install -r packages/atlas-pack/requirements.txt
python packages/atlas-pack/scripts/build_agentic_pack.py
```
