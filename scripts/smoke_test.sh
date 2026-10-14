#!/usr/bin/env sh
set -eu
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
HEALTH_URL="${BASE_URL%/}/health"
echo "Checking ${HEALTH_URL}"
response="$(curl --fail --silent --show-error --max-time 10 "${HEALTH_URL}")"
printf '%s\n' "$response"
printf '%s' "$response" | python -c 'import json,sys; data=json.load(sys.stdin); raise SystemExit(0 if data.get("ok") is True else 1)'
echo "Smoke test passed."
