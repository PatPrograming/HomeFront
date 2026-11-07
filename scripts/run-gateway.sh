#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

export PYTHONPATH="$ROOT/packages/atlas-enricher/src:$ROOT/packages/mcp-tools/src:$ROOT/packages/identity-gateway/src${PYTHONPATH:+:$PYTHONPATH}"
export HOMEFRONT_LIVE_DATAGOV="${HOMEFRONT_LIVE_DATAGOV:-0}"

exec python -m uvicorn identity_gateway.app:app --host 0.0.0.0 --port 8080
