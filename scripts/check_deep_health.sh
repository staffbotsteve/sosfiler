#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${SOSFILER_APP_DIR:-/opt/sosfiler/app}"
BASE_URL="${SOSFILER_HEALTH_BASE_URL:-http://127.0.0.1:8017}"
ALERT="${SOSFILER_HEALTH_ALERT:-true}"

cd "${APP_DIR}"
set -a
source .env
set +a

if [[ -z "${ADMIN_TOKEN:-}" ]]; then
  echo "ADMIN_TOKEN is not configured" >&2
  exit 2
fi

response="$(curl -fsS \
  -H "x-admin-token: ${ADMIN_TOKEN}" \
  "${BASE_URL}/api/admin/health/deep?alert=${ALERT}")"

printf '%s\n' "${response}" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({
    "status": payload.get("status"),
    "checked_at": payload.get("checked_at"),
    "failing_components": payload.get("failing_components", []),
    "alert_sent": payload.get("alert_sent"),
}, separators=(",", ":")))
if not payload.get("ok"):
    sys.exit(1)
'
