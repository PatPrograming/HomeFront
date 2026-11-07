# HomeFront

AWS × A10 Hackathon — **Prompt 1: Agentic Identity**

Secure and manage AI agents with workload identity, fine-grained MCP tool grants, mid-task revocation, and **MITRE ATLAS** enrichment on every allow/deny.

## What this demo shows

1. Authenticate an agency agent (workload token)
2. Allow selected MCP tools (`web.scrape`, `datagov.search`, `email.send`, …)
3. Revoke a grant mid-task
4. Block the next call — response tagged **`AML.T0053`** / **`AML.M0028`**

ATLAS data is **vendored** from [mitre-atlas/atlas-data](https://github.com/mitre-atlas/atlas-data) (Agentic AI subset). There is no dependency on MITRE’s authoring API at runtime.

## Quick start

```bash
# Terminal 1 — identity gateway + ATLAS enricher
chmod +x scripts/*.sh
./scripts/run-gateway.sh

# Terminal 2 — demo console
./scripts/run-demo.sh
```

- API: http://127.0.0.1:8080/docs  
- Console: http://127.0.0.1:5173  

Optional: set `DATA_GOV_API_KEY` and `HOMEFRONT_LIVE_DATAGOV=1` for live api.data.gov calls (key never returned to the agent).

## Layout

```
packages/atlas-pack/       # build script + atlas-agentic-pack.json
packages/atlas-enricher/   # local ATLAS lookup + annotate
packages/identity-gateway/ # authZ + revoke + MCP call proxy
packages/mcp-tools/        # email / social / scrape / data.gov tools
apps/demo-console/         # operator UI
```

## Rebuild ATLAS pack

```bash
source .venv/bin/activate
pip install -r packages/atlas-pack/requirements.txt
python packages/atlas-pack/scripts/build_agentic_pack.py
```
