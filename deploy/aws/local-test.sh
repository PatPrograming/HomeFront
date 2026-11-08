#!/usr/bin/env bash
# Build the gateway image and check it on this machine with NO AWS credentials:
#   - the image holds no .env file and no secret in its config,
#   - the container answers GET /api/v1/health,
#   - agent/.env is written at start with mode 600, and the gateway process does not keep the
#     agent's client secret in its environment,
#   - the agent's virtualenv can import its SDK (so the supervisor can start agents).
# Prints HEALTH OK when every check passes. Calls no AWS service.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="homefront-gateway:local"
NAME="homefront-local-test"
PORT="${PORT:-18080}"
DUMMY="dummy-agent-secret-for-local-test"

cd "$ROOT"
docker build -q -f deploy/aws/Dockerfile -t "$IMAGE" . >/dev/null
echo "built $IMAGE"

fail() { echo "FAIL: $*"; docker logs "$NAME" 2>&1 | tail -30 || true; exit 1; }
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

# 1. Nothing secret baked into the image.
if docker run --rm --entrypoint sh "$IMAGE" -c 'find /app -name ".env" -o -name ".env.*" | grep -v "/.venv/" | grep .'; then
  fail "the image contains an .env file"
fi
if docker image inspect "$IMAGE" --format '{{json .Config.Env}}' | grep -qiE 'SECRET|API_KEY|AWS_ACCESS|AWS_SESSION'; then
  fail "the image config has a secret-looking variable"
fi

# 2. Run it like ECS would, minus AWS: no credentials, no ~/.aws, metadata lookups off.
docker run -d --init --name "$NAME" -p "127.0.0.1:${PORT}:8080" \
  -e AWS_REGION=us-east-1 -e AWS_EC2_METADATA_DISABLED=true \
  -e AGENT_STORE=dynamodb -e HOMEFRONT_AUTOSTART_AGENT=1 \
  -e ALLOWED_ORIGINS=https://example.cloudfront.net,http://localhost:3000 \
  -e COGNITO_AGENT_CLIENT_SECRET="$DUMMY" \
  "$IMAGE" >/dev/null

status=""
for _ in $(seq 1 60); do
  status="$(curl -fsS "http://127.0.0.1:${PORT}/api/v1/health" 2>/dev/null || true)"
  [[ "$status" == *'"ok"'* ]] && break
  sleep 1
done
[[ "$status" == *'"ok"'* ]] || fail "no healthy answer from /api/v1/health"

# Same command the ECS health check runs inside the container.
docker exec "$NAME" /app/.venv/bin/python -c \
  "import urllib.request,sys; sys.exit(0 if b'ok' in urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=4).read() else 1)" \
  || fail "the in-container health check command failed"

# 3. The agent's settings file: present, private, and the gateway does not hold the secret.
mode="$(docker exec "$NAME" stat -c '%a' /app/agent/.env)"
[[ "$mode" == "600" ]] || fail "agent/.env has mode $mode, want 600"
docker exec "$NAME" grep -q "^COGNITO_AGENT_CLIENT_SECRET=${DUMMY}$" /app/agent/.env || fail "agent/.env lacks the agent secret"
# The slim image has no pgrep: find uvicorn by scanning /proc.
gateway_pid="$(docker exec "$NAME" sh -c 'for p in /proc/[0-9]*; do tr "\0" " " < $p/cmdline 2>/dev/null | grep -q "^/app/.venv/bin/python -m uvicorn" && { echo ${p#/proc/}; break; }; done')"
[[ "$gateway_pid" =~ ^[0-9]+$ ]] || fail "could not find the uvicorn process"
if docker exec "$NAME" sh -c "tr '\0' '\n' < /proc/${gateway_pid}/environ" | grep -q "COGNITO_AGENT_CLIENT_SECRET"; then
  fail "the gateway process still has COGNITO_AGENT_CLIENT_SECRET in its environment"
fi
if docker logs "$NAME" 2>&1 | grep -q "$DUMMY"; then
  fail "the container log printed the agent secret"
fi

# 4. The supervisor's agent interpreter works.
docker exec "$NAME" /app/agent/.venv/bin/python -c "import strands, mcp, boto3" || fail "agent venv cannot import its SDK"

echo "HEALTH OK ($status)"
