#!/usr/bin/env bash
# Read-only checks of a deployed HomeFront through its CloudFront URL. Changes nothing.
#   deploy/aws/smoke.sh https://dxxxx.cloudfront.net/
# Waits up to 10 minutes for the gateway (a new task takes 1-3 minutes to become healthy).
set -euo pipefail
URL="${1:?usage: smoke.sh https://dxxxx.cloudfront.net/}"
URL="${URL%/}"
ok=1

health=""
for _ in $(seq 1 60); do
  health="$(curl -fsS "$URL/api/v1/health" 2>/dev/null || true)"
  [[ "$health" == *'"ok"'* ]] && break
  sleep 10
done
if [[ "$health" == *'"ok"'* ]]; then echo "PASS gateway health: $health"; else echo "FAIL gateway health"; ok=0; fi

code="$(curl -s -o /dev/null -w '%{http_code}' "$URL/")"
if [[ "$code" == 200 ]]; then echo "PASS console index: 200"; else echo "FAIL console index: $code"; ok=0; fi

code="$(curl -s -o /dev/null -w '%{http_code}' "$URL/login")"
if [[ "$code" == 200 ]]; then echo "PASS console route /login served by index.html"; else echo "FAIL console route /login: $code"; ok=0; fi

# Fail closed: no token, no session.
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$URL/api/v1/sessions")"
if [[ "$code" == 401 || "$code" == 403 ]]; then echo "PASS sessions without a token: $code"; else echo "FAIL sessions without a token: $code"; ok=0; fi

code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{}' "$URL/mcp")"
if [[ "$code" == 401 ]]; then echo "PASS MCP without a mandate token: 401"; else echo "FAIL MCP without a mandate token: $code"; ok=0; fi

code="$(curl -s -o /dev/null -w '%{http_code}' "${URL/https:/http:}/")"
if [[ "$code" == 301 ]]; then echo "PASS plain http redirects to https"; else echo "FAIL plain http: $code"; ok=0; fi

(( ok )) && echo "SMOKE OK" || { echo "SMOKE FAILED"; exit 1; }
