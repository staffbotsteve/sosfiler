#!/usr/bin/env bash
set -euo pipefail

HOST="${SOSFILER_DEPLOY_HOST:-root@146.190.140.164}"
APP_DIR="${SOSFILER_DEPLOY_APP_DIR:-/opt/sosfiler/app}"
PUBLIC_BASE_URL="${SOSFILER_PUBLIC_BASE_URL:-https://ops.sosfiler.com}"
REMOTE_OWNER="${SOSFILER_DEPLOY_OWNER:-sosfiler:sosfiler}"
REMOTE_TEST_PYTHON="${SOSFILER_REMOTE_TEST_PYTHON:-.venv312/bin/python}"

FILES=(
  backend/server.py
  backend/corpnet_client.py
  backend/execution_platform.py
  backend/notifier.py
  backend/ca_bizfile_worker.py
  backend/filing_status_listener.py
  backend/tx_sosdirect_document_worker.py
  backend/filing_adapters.py
  backend/execution_repository.py
  backend/launch_readiness.py
  backend/state_automation_profiles.py
  backend/state_routing.py
  data/filing_actions.generated.json
  data/filing_actions.json
  data/portal_maps/state_portals_research.json
  data/regulatory/launch_readiness_report.json
  data/regulatory/state_adapter_manifest.json
  data/regulatory/state_automation_matrix.json
  data/regulatory/state_certification_gates.json
  data/regulatory/sosfiler_service_catalog.json
  data/state_fees.json
  frontend/chat-widget.css
  frontend/chat-widget.js
  frontend/sw.js
  frontend/index.html
  frontend/partners.html
  frontend/app.html
  frontend/operator.html
  frontend/dashboard.html
  frontend/licenses.html
  frontend/ra-rescue.html
  frontend/privacy.html
  frontend/terms.html
  qa/test_admin_security.py
  qa/test_annual_report_readiness.py
  qa/test_ca_bizfile_worker.py
  qa/test_ein_queue_hardening.py
  qa/test_email_delivery.py
  qa/test_engineering_queue.py
  qa/test_filing_adapters.py
  qa/test_health_monitoring.py
  qa/test_launch_readiness.py
  qa/test_operator_playwright_e2e.py
  qa/test_operator_fulfillment_packet.py
  qa/test_partner_api.py
  qa/test_platform_safety_and_metadata.py
  qa/test_slack_interactions.py
  qa/test_state_routing.py
  scripts/deploy_ops.sh
  scripts/check_deep_health.sh
  tools/check_state_routes.py
  deploy/systemd/sosfiler-health-check.service
  deploy/systemd/sosfiler-health-check.timer
  docs/execution_persistence_cutover.md
  docs/claude_ui_ux_redesign_brief.md
  docs/launch_readiness.md
  docs/ca_llc_no_playwright_automation.md
  docs/cron_jobs.md
  docs/operator_deploy_runbook.md
)

echo "==> Syncing SOSFiler ops files to ${HOST}:${APP_DIR}"
rsync -azR "${FILES[@]}" "${HOST}:${APP_DIR}/"

echo "==> Restarting service and running droplet smoke tests"
ssh "${HOST}" "cd '${APP_DIR}' && \
  chown -R '${REMOTE_OWNER}' backend/server.py backend/corpnet_client.py backend/execution_platform.py backend/notifier.py backend/ca_bizfile_worker.py backend/filing_status_listener.py backend/tx_sosdirect_document_worker.py backend/filing_adapters.py backend/execution_repository.py backend/launch_readiness.py backend/state_automation_profiles.py backend/state_routing.py data/filing_actions.generated.json data/filing_actions.json data/portal_maps/state_portals_research.json data/regulatory/launch_readiness_report.json data/regulatory/state_adapter_manifest.json data/regulatory/state_automation_matrix.json data/regulatory/state_certification_gates.json data/regulatory/sosfiler_service_catalog.json data/state_fees.json frontend/chat-widget.css frontend/chat-widget.js frontend/sw.js frontend/index.html frontend/partners.html frontend/app.html frontend/operator.html frontend/dashboard.html frontend/licenses.html frontend/ra-rescue.html frontend/privacy.html frontend/terms.html qa/test_admin_security.py qa/test_annual_report_readiness.py qa/test_ca_bizfile_worker.py qa/test_ein_queue_hardening.py qa/test_email_delivery.py qa/test_engineering_queue.py qa/test_filing_adapters.py qa/test_health_monitoring.py qa/test_launch_readiness.py qa/test_operator_playwright_e2e.py qa/test_operator_fulfillment_packet.py qa/test_partner_api.py qa/test_platform_safety_and_metadata.py qa/test_slack_interactions.py qa/test_state_routing.py scripts/deploy_ops.sh scripts/check_deep_health.sh tools/check_state_routes.py deploy/systemd/sosfiler-health-check.service deploy/systemd/sosfiler-health-check.timer docs/execution_persistence_cutover.md docs/claude_ui_ux_redesign_brief.md docs/launch_readiness.md docs/ca_llc_no_playwright_automation.md docs/cron_jobs.md docs/operator_deploy_runbook.md && \
  mkdir -p data/runtime && \
  chown -R '${REMOTE_OWNER}' data/runtime && \
  chmod +x scripts/deploy_ops.sh scripts/check_deep_health.sh && \
  python3 -m py_compile backend/server.py backend/corpnet_client.py backend/execution_platform.py backend/notifier.py backend/ca_bizfile_worker.py backend/filing_status_listener.py backend/tx_sosdirect_document_worker.py backend/filing_adapters.py backend/execution_repository.py backend/launch_readiness.py backend/state_automation_profiles.py backend/state_routing.py qa/test_admin_security.py qa/test_ca_bizfile_worker.py qa/test_ein_queue_hardening.py qa/test_email_delivery.py qa/test_health_monitoring.py qa/test_launch_readiness.py qa/test_operator_playwright_e2e.py qa/test_operator_fulfillment_packet.py qa/test_partner_api.py qa/test_platform_safety_and_metadata.py qa/test_state_routing.py && \
  systemctl restart sosfiler && \
  systemctl is-active sosfiler && \
  set +e; set -a; [ -f .env ] && source .env; set +a; set -e; \
  EMAIL_DELIVERY_MODE=noop '${REMOTE_TEST_PYTHON}' -m unittest qa/test_admin_security.py qa/test_engineering_queue.py qa/test_ca_bizfile_worker.py qa/test_ein_queue_hardening.py qa/test_email_delivery.py qa/test_filing_adapters.py qa/test_health_monitoring.py qa/test_launch_readiness.py qa/test_operator_fulfillment_packet.py qa/test_partner_api.py qa/test_annual_report_readiness.py qa/test_platform_safety_and_metadata.py qa/test_slack_interactions.py qa/test_state_routing.py && \
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
