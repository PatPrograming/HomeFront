#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
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

export PYTHONPATH="$ROOT/Backend:$ROOT/packages/atlas-enricher/src:$ROOT/packages/mcp-tools/src:$ROOT/packages/identity-gateway/src${PYTHONPATH:+:$PYTHONPATH}"

# Live api.data.gov when a key is present, unless HOMEFRONT_LIVE_DATAGOV is set explicitly.
if [[ -z "${HOMEFRONT_LIVE_DATAGOV:-}" ]]; then
  if [[ -n "${DATA_GOV_API_KEY:-}" ]]; then
    export HOMEFRONT_LIVE_DATAGOV=1
  else
    export HOMEFRONT_LIVE_DATAGOV=0
  fi
fi

# Unified API: Cognito user auth (/api/v1/*) + agent IAM + ATLAS (/, /atlas, /mcp, …)
exec python -m uvicorn app.main:app --app-dir Backend --host 0.0.0.0 --port 8080 --reload --reload-dir Backend --reload-dir packages
