#!/bin/bash
# Run pytest with every AWS credential stripped, so no test can reach real AWS or Bedrock.
# Usage: scripts/offline-pytest.sh <pytest args>   (uses ./.venv)
cd "$(dirname "$0")/.." || exit 1
PP=Backend:packages/mandate-contracts/src:packages/atlas-enricher/src:packages/mcp-tools/src:packages/identity-gateway/src:packages/guardrail-scanner/src:agent
exec env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE \
  AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null AWS_DEFAULT_REGION=us-east-1 \
  PYTHONPATH="$PP" .venv/bin/python -m pytest "$@"
