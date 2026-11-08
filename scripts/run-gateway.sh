#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  /usr/local/bin/python3.12 -m venv .venv  # the default python3 here is a broken Intel build
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r Backend/requirements.txt

# Load repo-root .env without `source` (angle brackets in URLs break bash redirects).
load_env_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    # strip CR, skip blanks/comments
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      local key="${BASH_REMATCH[1]}"
      local val="${BASH_REMATCH[2]}"
      # strip optional surrounding quotes
      if [[ "$val" =~ ^\"(.*)\"$ ]]; then
        val="${BASH_REMATCH[1]}"
      elif [[ "$val" =~ ^\'(.*)\'$ ]]; then
        val="${BASH_REMATCH[1]}"
      fi
      export "$key=$val"
    fi
  done < "$file"
}

load_env_file "$ROOT/.env"
# AVP policy store id and policy-name map written by Backend/infra/setup.sh (untracked).
load_env_file "$ROOT/Backend/infra/.env.aws"

export PYTHONPATH="$ROOT/Backend:$ROOT/packages/mandate-contracts/src:$ROOT/packages/atlas-enricher/src:$ROOT/packages/mcp-tools/src:$ROOT/packages/identity-gateway/src${PYTHONPATH:+:$PYTHONPATH}"

# Live api.data.gov when a key is present, unless HOMEFRONT_LIVE_DATAGOV is set explicitly.
if [[ -z "${HOMEFRONT_LIVE_DATAGOV:-}" ]]; then
  if [[ -n "${DATA_GOV_API_KEY:-}" ]]; then
    export HOMEFRONT_LIVE_DATAGOV=1
  else
    export HOMEFRONT_LIVE_DATAGOV=0
  fi
fi

# Unified API: broker + admin + events (/api/v1/*), gateway REST (/api/v1/mcp/call), MCP (/mcp).
# Loopback only: nothing on the venue network can reach the gateway.
exec python -m uvicorn app.main:app --app-dir Backend --host 127.0.0.1 --port 8080
