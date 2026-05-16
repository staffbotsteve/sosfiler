# Claude Handoff: California Filing Automation

Date: 2026-05-15

## Current State

- Production deploy completed to `https://ops.sosfiler.com`.
- Remote smoke suite passed: 125 tests OK.
- Public health passed:
  - `https://ops.sosfiler.com/api/health`
  - `https://ops.sosfiler.com/api/health/deep`
- Branch at handoff: `reconciliation-local-20260511`.

## NotebookLM

Notebook: `SOSFiler CA LLC No-Playwright Filing Paths`

Notebook ID: `93ca4155-c74e-4b03-8957-c1f39a889627`

Important finding: the notebook includes community scraping sources about `curl_cffi`, TLS/JA3/JA4 impersonation, `nodriver`, `zendriver`, `patchright`, SeleniumBase UC mode, and residential proxies. Those are bypass-oriented sources, not California SOS authorization. The notebook answer also found no source granting explicit CA SOS/BizFile permission to bypass Imperva/Okta or automate direct endpoint access. The official CA BizFile terms source says automated robots/spiders/page-scrape/monitoring methods are prohibited unless the information is purposely made available.

Do not implement stealth/proxy/TLS-fingerprint bypass for BizFile. Keep any protocol manifest use as diagnostic/legal-review material unless SOSFiler has written permission or a permitted API/partner lane.

## Implemented

- Added California BizFile worker:
  - `backend/ca_bizfile_worker.py`
  - supports `status`, `submit`, and read-only `discover`.
- Added read-only protocol discovery:
  - captures redacted BizFile endpoint candidates.
  - writes `data/portal_maps/ca_bizfile_protocol_manifest.json`.
  - treats access checkpoints as discovery blockers instead of crashing.
- Updated California automation profile:
  - `status_check_method`: `ca_bizfile_protocol_manifest_then_worker_my_work_queue`
  - `protocol_discovery_method`: `ca_bizfile_worker_discover_operation`
- Added documentation:
  - `docs/ca_llc_no_playwright_automation.md`
  - `docs/cron_jobs.md`
- Added/updated tests:
  - `qa/test_ca_bizfile_worker.py`
  - `qa/test_state_routing.py`
  - `qa/test_filing_adapters.py`
  - `qa/test_platform_safety_and_metadata.py`

## Verification

Local targeted tests:

```bash
.venv312/bin/python -m unittest qa/test_ca_bizfile_worker.py qa/test_state_routing.py qa/test_filing_adapters.py qa/test_platform_safety_and_metadata.py qa/test_launch_readiness.py
```

Result: 64 tests OK.

Deploy command:

```bash
scripts/deploy_ops.sh
```

Result: remote 125 tests OK and public health green.

## Next Best Work

1. Connect the Gmail connector to `admin@sosfiler.com` or forward BizFile/partner messages into a mailbox the connector can read.
2. Build Gmail evidence ingestion for:
   - receipt emails,
   - approval notices,
   - correction/rejection notices,
   - attached certificates or stamped articles.
3. Move EIN collection behind confirmed state approval evidence only.
4. Evaluate partner filing APIs for California and 50-state scale. FileForms and CorpNet are already in the notebook as candidate sources.
5. Build a 50-state account bootstrap inventory only for official/authorized portals or partner channels.

## Payment Guardrail

Do not put raw card values in `.env`. The CA worker deliberately blocks raw card environment variables. Use a saved BizFile portal payment method or a PCI-compliant tokenized payment integration.
