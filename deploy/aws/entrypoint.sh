#!/bin/sh
# Container start. ECS injects the secrets from Secrets Manager as environment variables.
#
# 1. Write agent/.env, the file the auto-started agents read their own Cognito identity from.
#    It lives only inside the running container (mode 600), never in the image or in git.
# 2. Remove the agent's client secret from the gateway's own environment, so the gateway holds
#    only what it needs (as on a laptop, where the secret is only in agent/.env).
# 3. Start uvicorn on all interfaces: the security group lets only the load balancer reach 8080.
# Nothing here prints a value.
set -eu

umask 077
cat > /app/agent/.env <<EOF
MANDATE_GATEWAY_URL=http://127.0.0.1:8080/mcp
AWS_REGION=${AWS_REGION:-us-east-1}
BEDROCK_MODEL_ID=${AGENT_BEDROCK_MODEL_ID:-us.anthropic.claude-sonnet-5}
GUARDRAIL_ID=${GUARDRAIL_ID:-}
GUARDRAIL_VERSION=${GUARDRAIL_VERSION:-}
COGNITO_DOMAIN=${COGNITO_DOMAIN:-}
COGNITO_AGENT_CLIENT_ID=${COGNITO_AGENT_CLIENT_ID:-}
COGNITO_AGENT_CLIENT_SECRET=${COGNITO_AGENT_CLIENT_SECRET:-}
EOF
unset COGNITO_AGENT_CLIENT_SECRET

cd /app
exec /app/.venv/bin/python -m uvicorn app.main:app --app-dir Backend --host 0.0.0.0 --port 8080
