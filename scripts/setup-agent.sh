#!/usr/bin/env bash
# Create agent/.venv and install the agent's requirements (needed for chat agent auto-start).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib/find-python.sh
source "$ROOT/scripts/lib/find-python.sh"

if [[ ! -d "$ROOT/agent/.venv" ]]; then
  PYTHON="$(find_python)"
  echo "Creating agent/.venv with $PYTHON"
  "$PYTHON" -m venv "$ROOT/agent/.venv"
fi
"$ROOT/agent/.venv/bin/pip" install -q -r "$ROOT/agent/requirements.txt"
echo "agent/.venv is ready."
if [[ ! -f "$ROOT/agent/.env" ]]; then
  echo "Next: cp agent/.env.example agent/.env, then paste COGNITO_AGENT_CLIENT_SECRET into agent/.env."
fi
