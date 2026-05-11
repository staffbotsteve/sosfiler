#!/usr/bin/env bash
set -euo pipefail

HOST="${SOSFILER_DEPLOY_HOST:-root@146.190.140.164}"
APP_DIR="${SOSFILER_DEPLOY_APP_DIR:-/opt/sosfiler/app}"
PUBLIC_BASE_URL="${SOSFILER_PUBLIC_BASE_URL:-https://ops.sosfiler.com}"

FILES=(
  backend/server.py
  backend/notifier.py
  backend/tx_sosdirect_document_worker.py
  backend/filing_adapters.py
  backend/execution_repository.py
  frontend/chat-widget.css
  frontend/chat-widget.js
  frontend/sw.js
  frontend/index.html
  frontend/app.html
  frontend/operator.html
  frontend/dashboard.html
  frontend/licenses.html
  frontend/ra-rescue.html
  frontend/privacy.html
  frontend/terms.html
  qa/test_admin_security.py
  qa/test_annual_report_readiness.py
  qa/test_ein_queue_hardening.py
  qa/test_email_delivery.py
  qa/test_engineering_queue.py
  qa/test_filing_adapters.py
  qa/test_health_monitoring.py
  qa/test_operator_playwright_e2e.py
  qa/test_platform_safety_and_metadata.py
  qa/test_slack_interactions.py
  scripts/deploy_ops.sh
  scripts/check_deep_health.sh
  deploy/systemd/sosfiler-health-check.service
  deploy/systemd/sosfiler-health-check.timer
  docs/execution_persistence_cutover.md
  docs/operator_deploy_runbook.md
)

echo "==> Syncing SOSFiler ops files to ${HOST}:${APP_DIR}"
rsync -azR "${FILES[@]}" "${HOST}:${APP_DIR}/"

echo "==> Restarting service and running droplet smoke tests"
ssh "${HOST}" "cd '${APP_DIR}' && \
  chown -R sosfiler:sosfiler backend/server.py backend/notifier.py backend/tx_sosdirect_document_worker.py backend/filing_adapters.py backend/execution_repository.py frontend/chat-widget.css frontend/chat-widget.js frontend/sw.js frontend/index.html frontend/app.html frontend/operator.html frontend/dashboard.html frontend/licenses.html frontend/ra-rescue.html frontend/privacy.html frontend/terms.html qa/test_admin_security.py qa/test_annual_report_readiness.py qa/test_ein_queue_hardening.py qa/test_email_delivery.py qa/test_engineering_queue.py qa/test_filing_adapters.py qa/test_health_monitoring.py qa/test_operator_playwright_e2e.py qa/test_platform_safety_and_metadata.py qa/test_slack_interactions.py scripts/deploy_ops.sh scripts/check_deep_health.sh deploy/systemd/sosfiler-health-check.service deploy/systemd/sosfiler-health-check.timer docs/execution_persistence_cutover.md docs/operator_deploy_runbook.md && \
  chmod +x scripts/deploy_ops.sh scripts/check_deep_health.sh && \
  python3 -m py_compile backend/server.py backend/notifier.py backend/tx_sosdirect_document_worker.py backend/filing_adapters.py backend/execution_repository.py qa/test_admin_security.py qa/test_ein_queue_hardening.py qa/test_email_delivery.py qa/test_health_monitoring.py qa/test_operator_playwright_e2e.py qa/test_platform_safety_and_metadata.py && \
  systemctl restart sosfiler && \
  systemctl is-active sosfiler && \
  set -a && source .env && set +a && \
  EMAIL_DELIVERY_MODE=noop .venv312/bin/python -m unittest qa/test_admin_security.py qa/test_engineering_queue.py qa/test_ein_queue_hardening.py qa/test_email_delivery.py qa/test_filing_adapters.py qa/test_health_monitoring.py qa/test_annual_report_readiness.py qa/test_platform_safety_and_metadata.py qa/test_slack_interactions.py && \
  cp deploy/systemd/sosfiler-health-check.service /etc/systemd/system/sosfiler-health-check.service && \
  cp deploy/systemd/sosfiler-health-check.timer /etc/systemd/system/sosfiler-health-check.timer && \
  systemctl daemon-reload && \
  systemctl enable --now sosfiler-health-check.timer && \
  systemctl list-timers --all sosfiler-health-check.timer --no-pager"

echo "==> Checking public health at ${PUBLIC_BASE_URL}/api/health"
curl -fsS "${PUBLIC_BASE_URL}/api/health"
echo
echo "==> Checking public deep health at ${PUBLIC_BASE_URL}/api/health/deep"
curl -fsS "${PUBLIC_BASE_URL}/api/health/deep"
echo
echo "==> Deploy complete"
