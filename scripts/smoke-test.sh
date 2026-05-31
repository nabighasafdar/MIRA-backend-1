#!/usr/bin/env bash
# Smoke test for deployed MIRA Agent API.
# Usage: AGENT_API_URL=https://... AGENT_API_SECRET=... ./scripts/smoke-test.sh

set -euo pipefail

URL="${AGENT_API_URL:-http://localhost:8000}"
URL="${URL%/}"
SECRET="${AGENT_API_SECRET:?Set AGENT_API_SECRET}"

echo "==> Health"
curl -sf "${URL}/health" | grep -q '"ok"' && echo "OK" || { echo "FAIL"; exit 1; }

echo "==> Start job"
RESP=$(curl -sf -X POST "${URL}/agent/run" \
  -H "Authorization: Bearer ${SECRET}" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"smoke-test","chat_id":null,"task":"Return done immediately with summary smoke test ok"}')
echo "$RESP"
echo "$RESP" | grep -q 'job_id' && echo "Job started OK" || { echo "FAIL"; exit 1; }

echo "Smoke test passed."
