# Track B — Live Adapter Audit + Selector Contract Inventory

Date: 2026-05-16
Owner: Steven Swan
Status: AUDIT — supporting plan v2.6 §4.3 (adapter contract) and §4.5 (drift canary)
Sources: explore-agent pass over `backend/ca_bizfile_worker.py`, `backend/silverflume_filer.py`, `backend/tx_sosdirect_document_worker.py` (commit-current as of 2026-05-16)

## Why this exists

Plan v2.6 §4.3 calls for every live adapter to declare a `selectors_contract` on the base `FilingAdapter` ABC. The drift canary (Track B follow-up) reads each adapter's contract and asserts at runtime that the live portal still matches. Before adding the attribute, this doc enumerates what every live adapter actually touches so the contract reflects production reality rather than theory.

The audit also surfaces evidence-capture gaps and error-handling weak points that PRs 1–7 did not address.

## 1. California — `backend/ca_bizfile_worker.py`

### Selector contract (production)

| Page | Selector | Type | Fail behavior |
|---|---|---|---|
| Login | `input[name='identifier']` (`backend/ca_bizfile_worker.py:649`) | fill | hard fail → `BizFileBlocked("credentials_missing")` |
| Login | `input[name='credentials.passcode']` (`backend/ca_bizfile_worker.py:654`) | fill | hard fail |
| Work queue | `text("My Work Queue")` role match (`backend/ca_bizfile_worker.py:665`) | click | hard fail → `BizFileBlocked("work_queue_link_not_found")` |
| Public search | `input:visible` (`backend/ca_bizfile_worker.py:688`) | fill | graceful continue |
| Form principal address | `input[aria-label='Principal Address: Address Line 1']` (`backend/ca_bizfile_worker.py:938`) | fill | graceful skip |
| Form attestation | `input[name='ATTEST_1_YN']` (`backend/ca_bizfile_worker.py:920`) | check | graceful skip |
| Next step | `button[name='Next Step']` (`backend/ca_bizfile_worker.py:906`) | click | hard fail |
| Processing tier | `input[name='PROCESSING_TYPE_ID'][value='901']` (`backend/ca_bizfile_worker.py:1013`) | check | hard fail |
| Payment method | `input[name*='card' i]` / `input[autocomplete='cc-number']` (`backend/ca_bizfile_worker.py:1042`) | assertion | hard fail → `BizFileBlocked("payment_method_not_tokenized")` |
| Payment submit | `button[name~='submit payment']` (`backend/ca_bizfile_worker.py:1049`) | click | hard fail → `BizFileBlocked("payment_submit_not_found")` |
| Body text | `body` (`backend/ca_bizfile_worker.py:630`, `:840`) | extract | soft fail on timeout |

### Evidence capture

- Receipt page screenshot at every gate via `checkpoint_snapshot()` (`backend/ca_bizfile_worker.py:620`).
- `submitted_receipt` artifact added (`backend/ca_bizfile_worker.py:1098`).
- `approved_certificate` artifact with `sha256_hex` (`backend/ca_bizfile_worker.py:520`, `:453-463`).
- Filing confirmation extracted via `CONFIRMATION_NUMBER_REGEX` against `body.inner_text()` (`backend/ca_bizfile_worker.py:1067`, `:1187`).
- **GAP**: approval PDF download in `try_download_document()` can silently fail; the silent path falls back to `state_correspondence` without surfacing operator_required.

### Error handling

- `BizFileBlocked` (`backend/ca_bizfile_worker.py:104`) carries snapshot evidence; escalates to `operator_required` cleanly.
- `PaymentNotReady` (`backend/ca_bizfile_worker.py:113`) sets `payment_required` and inserts the cart-evidence event.
- **GAP**: top-level `except Exception` (`backend/ca_bizfile_worker.py:1236`) logs as event but does not re-raise; if the browser crashes mid-form, the job is left in an undefined state.

### Confirmation extraction

`extract_filing_confirmation(receipt_text, CONFIRMATION_NUMBER_REGEX)` on line 1187 against `body.inner_text()` from line 1184. Pattern: `(?:File|Filing|Confirmation)\s*(?:Number|No\.?|#)\s*[:\-—]?\s*([A-Z0-9]{8,15})`. Silent no-op fallback when missing.

### Operator-required gates

- Login env-var check (`backend/ca_bizfile_worker.py:645`).
- MFA / identity checkpoint in body text (`backend/ca_bizfile_worker.py:640`) → `BizFileBlocked("mfa_or_identity_checkpoint")`.
- Incapsula WAF (`backend/ca_bizfile_worker.py:634`) → `BizFileBlocked`.
- Payment card field present (`backend/ca_bizfile_worker.py:1042`) → `BizFileBlocked`.

CA is the strongest of the three. Cleanest escalations, best evidence trail, regex extraction works.

## 2. Nevada — `backend/silverflume_filer.py`

### Selector contract (production)

| Page | Selector | Type | Fail behavior |
|---|---|---|---|
| Homepage | body text "Incapsula" assertion (`backend/silverflume_filer.py:106`) | assert | hard fail → `needs_human_review` + screenshot |
| WAF bypass | hCaptcha iframe sitekey extraction (`backend/silverflume_filer.py:167-194`) | extract | falls back to hardcoded test sitekey (`backend/silverflume_filer.py:199`) — production risk |
| WAF bypass | `[name='h-captcha-response']`, `textarea.h-captcha-response` (`backend/silverflume_filer.py:221`) | fill | silent fallback to `hcaptcha.execute()` |
| Start filing | `a, button` text match "start your business" (`backend/silverflume_filer.py:268`) | click | graceful continue |
| Form discovery | `input, select, textarea` (`backend/silverflume_filer.py:381`) | enumerate | log only |
| Form fill | dynamic field mapping (`backend/silverflume_filer.py:399-424`) | fill | try/except swallows failures (`backend/silverflume_filer.py:423`) |

### Evidence capture — CRITICAL GAPS

- ✓ Screenshots only via `_screenshot()` (`backend/silverflume_filer.py:444`).
- ✗ NO `submitted_receipt` artifact persisted.
- ✗ NO `approved_certificate` artifact persisted.
- ✗ NO database writes at all — `file_llc()` returns a result dict to the caller in `backend/state_filing.py`; that caller is responsible for status promotion and never calls `insert_filing_artifact_row`.
- ✓ Confirmation extraction attempted (`backend/silverflume_filer.py:133-139`) but result never persisted to `orders.filing_confirmation`.

### Error handling — CRITICAL GAPS

- Top-level `except Exception` (`backend/silverflume_filer.py:145-147`) catches everything, sets `needs_human_review=True` in the returned dict, but the dict is just a JSON file at `filing_receipts/{order_id}_NV_receipt.json` — the actual `filing_jobs` row stays in `automation_started`.
- Hardcoded test hCaptcha sitekey fallback (`backend/silverflume_filer.py:199`) is a production risk: in live mode a captcha solve against the test key won't pass the real challenge but will look like success in the dict.

### Confirmation extraction

`CONFIRMATION_NUMBER_REGEX` declared (`backend/silverflume_filer.py:34`). Extraction call at `backend/silverflume_filer.py:133-139` against final body text. Never written to DB.

### Operator-required gates

None. `needs_human_review=True` is a flag in a JSON receipt file. No clean operator escalation path; the operator must find the JSON file and manually flip the job.

NV is the weakest. Treat as DRY-RUN-ONLY until the persistence + WAF gaps close.

## 3. Texas — `backend/tx_sosdirect_document_worker.py`

### Selector contract (production)

| Page | Selector | Type | Fail behavior |
|---|---|---|---|
| Login | `input[name='client_id']` (`backend/tx_sosdirect_document_worker.py:344`) | fill | hard fail (no `.count()` check) |
| Login | `input[name='web_password']` (`backend/tx_sosdirect_document_worker.py:345`) | fill | hard fail |
| Login submit | `input[type='submit'][name='submit']` (`backend/tx_sosdirect_document_worker.py:346`) | click | hard fail |
| Payment select | `select[name='payment_type_id']` (`backend/tx_sosdirect_document_worker.py:349`) | select | graceful skip |
| Continue | `input[type='submit'][name='Submit'][value='Continue']` (`backend/tx_sosdirect_document_worker.py:361`) | click | conditional |
| Briefcase rows | `tr` (`backend/tx_sosdirect_document_worker.py:378`) | enumerate | graceful empty |
| Document links | `a` per row (`backend/tx_sosdirect_document_worker.py:388`) | enumerate | filtered by `row_matches` |
| Batch search | `input[name='sid']`, `input[name='op_email']` (`backend/tx_sosdirect_document_worker.py:455-456`) | fill | hard fail when session_id supplied |

### Evidence capture

- ✓ Debug HTML at `RUN_DIR` (`backend/tx_sosdirect_document_worker.py:462`).
- ✓ Downloaded PDFs at `DOCS_DIR/{order_id}/state_filings/` (`backend/tx_sosdirect_document_worker.py:410-446`).
- ✓ `approved_certificate` artifact with `sha256_hex` (`backend/tx_sosdirect_document_worker.py:246-256`).
- ✓ Confirmation extracted from candidate row + detail response text + briefcase content (`backend/tx_sosdirect_document_worker.py:515`).
- ✗ No `submitted_receipt` capture (worker is download-only).

### Error handling

- Login failure detected by re-checking for login form presence (`backend/tx_sosdirect_document_worker.py:364-372`) → raises `RuntimeError`.
- Session return-to-login (`backend/tx_sosdirect_document_worker.py:464`) raises `RuntimeError`.
- **GAP**: top-level `except Exception` (`backend/tx_sosdirect_document_worker.py:564`) logs as event but the job status is not set to `operator_required`; it stays in whatever the previous active status was.
- **GAP**: no MFA / 2FA detection; if TX adds an MFA step the worker fails opaquely.

### Confirmation extraction

`extract_filing_confirmation(candidate_haystack, CONFIRMATION_NUMBER_REGEX)` (`backend/tx_sosdirect_document_worker.py:515`). Pattern: `(?:Document|Filing|Confirmation)\s*(?:Number|No\.?|#)\s*[:\-—]?\s*([0-9]{6,12})` (`backend/tx_sosdirect_document_worker.py:215`). Haystack is `row_text + text + href + detail_text + briefcase content` (`backend/tx_sosdirect_document_worker.py:512-514`). Fallback to `evidence_summary['document_number' or 'filing_number']` in `attach_approval_document` (`backend/tx_sosdirect_document_worker.py:276-280`).

### Operator-required gates

- Credentials check at `backend/tx_sosdirect_document_worker.py:535-543` returns early with `status='credentials_missing'`.
- No MFA detection.
- Session expiry detection raises but does not escalate cleanly.

## Concrete recommendations (ordered by impact)

| # | Adapter | Recommendation | Effort |
|---|---|---|---|
| 1 | NV silverflume | **Database persistence.** Wire the result-dict path to actually call `insert_filing_artifact_row` + flip status to `operator_required` on `needs_human_review`. Today the JSON receipt is the only persistence surface. | 30 min |
| 2 | All three | **Generic `except Exception` re-raise to `operator_required`.** Catch + escalate via a shared helper, never silently log. CA `:1236`, NV `:145`, TX `:564`. | 30 min |
| 3 | TX | **MFA / 2FA detection on the login page** mirroring CA's body-text check at `:640-642`. Take a screenshot and raise `RuntimeError` → operator review. | 20 min |
| 4 | NV | **Drop the hardcoded test hCaptcha sitekey fallback** (`:199`). Fail hard or retry with 2Captcha polling; do not let a test-key "success" promote a filing. | 30 min |
| 5 | CA | **Validate the confirmation regex match before `mark_submitted`.** Today the regex can match transient page text that isn't the real number; assert against the receipt page region rather than full body. | 30 min |
| 6 | TX | **Detect declined/blocked PDF detail responses.** Right now non-HTML detail responses (`:504`) silently skip regex extraction; capture as `state_correspondence` so the operator can review. | 20 min |
| 7 | All three | **Per-state confirmation-number format validator** alongside `build_filing_confirmation_payload`. Reject obviously-wrong matches before persisting. | 30 min |
| 8 | CA | **Drift canary fixture.** Capture a known-good DOM snapshot at every gate listed in the selector table above; canary diff-checks the live portal. | 1 hour |

## Drift canary handoff

The `selectors_contract` attribute added in this PR carries the data the canary needs. The canary itself (plan v2.6 §4.5) is a separate workstream; this audit gives it the inventory to validate against. Recommended first canary states: CA + TX (live submit/download paths today) followed by NV once the gaps above close.
