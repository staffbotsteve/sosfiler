# SOSFiler Operator Deploy Runbook

This runbook deploys the public operator cockpit at `https://ops.sosfiler.com`.

## One-command deploy

From the repo root:

```bash
scripts/deploy_ops.sh
```

Optional overrides:

```bash
SOSFILER_DEPLOY_HOST=root@146.190.140.164 \
SOSFILER_DEPLOY_APP_DIR=/opt/sosfiler/app \
SOSFILER_PUBLIC_BASE_URL=https://ops.sosfiler.com \
scripts/deploy_ops.sh
```

## What The Script Does

1. Syncs the backend, operator cockpit, QA tests, deploy script, and this runbook.
2. Compiles the changed Python files on the droplet.
3. Restarts `sosfiler` with `systemctl restart sosfiler`.
4. Runs focused droplet tests:
   - `qa/test_admin_security.py`
   - `qa/test_engineering_queue.py`
   - `qa/test_ein_queue_hardening.py`
   - `qa/test_filing_adapters.py`
   - `qa/test_health_monitoring.py`
   - `qa/test_annual_report_readiness.py`
   - `qa/test_platform_safety_and_metadata.py`
   - `qa/test_slack_interactions.py`
5. Installs and enables `sosfiler-health-check.timer`.
6. Calls the public health and deep-health endpoints.

## Manual Verification

```bash
curl -fsS https://ops.sosfiler.com/api/health
```

```bash
curl -fsS https://ops.sosfiler.com/api/health/deep
```

```bash
ssh root@146.190.140.164 'systemctl status sosfiler --no-pager'
```

```bash
ssh root@146.190.140.164 'journalctl -u sosfiler -n 120 --no-pager'
```

## Deep Health Timer

The deploy script installs:

- `sosfiler-health-check.service`
- `sosfiler-health-check.timer`

The timer runs every 5 minutes and calls:

```bash
/opt/sosfiler/app/scripts/check_deep_health.sh
```

That script reads `ADMIN_TOKEN` from `/opt/sosfiler/app/.env`, calls the protected deep-health endpoint, exits nonzero when a component fails, and asks the API to send a Slack alert when health is failing.

Useful commands:

```bash
ssh root@146.190.140.164 'systemctl list-timers --all sosfiler-health-check.timer --no-pager'
```

```bash
ssh root@146.190.140.164 'systemctl status sosfiler-health-check.service --no-pager'
```

```bash
ssh root@146.190.140.164 'journalctl -u sosfiler-health-check.service -n 80 --no-pager'
```

## Browser E2E

Run this after a successful deploy when you want full public cockpit coverage:

```bash
.venv312/bin/python -m unittest qa/test_operator_playwright_e2e.py
```

This creates harmless test tickets and annual-report fixture jobs, so it is intentionally separate from the normal deploy script.

## Rollback

Use the last known-good git commit or local file copy, then rerun:

```bash
scripts/deploy_ops.sh
```

Do not manually edit production files except to recover service availability.
