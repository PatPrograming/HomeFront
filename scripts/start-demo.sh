#!/usr/bin/env bash
# Start gateway + frontend for the local Prompt 1 demo (memory agent store).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

(lsof -ti:8080 | xargs kill -9) 2>/dev/null || true
(lsof -ti:3000 | xargs kill -9) 2>/dev/null || true
sleep 1

./scripts/run-gateway.sh &
./scripts/run-frontend.sh &

echo "Waiting for http://localhost:8080 and http://localhost:3000 ..."
for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8080/api/v1/health >/dev/null 2>&1 \
     && curl -sf http://127.0.0.1:3000/ >/dev/null 2>&1; then
    echo
    echo "Demo is up (local memory store — no AWS)."
    echo "  Open: http://localhost:3000/monitoring"
    echo "  Steps: Authenticate agent → datagov.search → email.send → Revoke email.send → Retry"
    wait
    exit 0
  fi
  sleep 1
done
echo "Timed out waiting for servers. Check gateway/frontend output above."
wait
exit 1
