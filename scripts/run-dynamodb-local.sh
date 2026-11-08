#!/usr/bin/env bash
# Start DynamoDB Local on :8000 and create the HomeFront tables.
# Then set DYNAMODB_ENDPOINT_URL=http://localhost:8000 in Backend/.env.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! docker ps --format '{{.Names}}' | grep -qx homefront-dynamodb; then
  docker run -d --rm --name homefront-dynamodb -p 8000:8000 \
    amazon/dynamodb-local:latest -jar DynamoDBLocal.jar -sharedDb -inMemory >/dev/null
  echo "DynamoDB Local started on http://localhost:8000 (in-memory: data is lost when it stops)"
fi

# DynamoDB Local accepts any credentials, but boto3 needs some.
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-local}" AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-local}"
export AWS_REGION="${AWS_REGION:-us-east-1}" DYNAMODB_ENDPOINT_URL=http://localhost:8000
"$ROOT/.venv/bin/python" "$ROOT/scripts/create_tables.py"
