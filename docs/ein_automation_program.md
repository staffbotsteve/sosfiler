# SOSFiler EIN Automation Program

EIN filing is a core SOSFiler service, not an operator-only afterthought. The production design must automate the IRS EIN assistant end to end while preserving customer authorization, SSN safety, evidence capture, and status fidelity.

## Non-Negotiables

- Use the customer's actual formed legal entity name and address from the approved order.
- Start only after state approval evidence exists.
- Submit only when full responsible-party SSN/ITIN has been collected through the secure portal.
- Never display, log, email, or commit SSN/ITIN values.
- Capture the official IRS EIN confirmation letter/CP575 as the completion artifact.
- Mark `ein_received` only after the official confirmation document is saved to the customer portal.
- Respect IRS availability and one-EIN-per-responsible-party-per-day constraints.

## Why The Web Server Cannot Be The EIN Worker

The production DigitalOcean/headless server was blocked by the IRS EIN site with an `Access Denied` response before the wizard loaded. Therefore, the normal application server cannot be the only automation executor.

The EIN automation lane needs dedicated browser workers with:

- Stable, compliant US egress that can access the IRS EIN assistant.
- A real browser context, persistent profile, downloads enabled, and PDF print/save support.
- Encrypted access to queue payloads.
- No customer SSN in logs, screenshots, filenames, or chat transcripts.
- Health checks that prove the IRS assistant can load before assigning live orders.

## Queue States

Use `generated_docs/<order_id>/ein_queue.json` as the queue artifact until this moves into a secure database table.

Required states:

- `queued`: state approval exists but full SSN/ITIN may still be missing.
- `ready_for_submission`: full SSN/ITIN received and IRS submission may begin.
- `submission_started`: a worker has claimed the order.
- `irs_unavailable`: IRS assistant is outside operating hours or in maintenance.
- `blocked_by_access_control`: worker environment cannot access IRS.
- `needs_operator_review`: the IRS assistant presented an unexpected question/error.
- `submitted`: IRS application submitted and confirmation capture is in progress.
- `ein_received`: official EIN confirmation letter/CP575 captured and ingested.
- `failed_retryable`: transient worker/browser/network error.
- `failed_terminal`: IRS rejected the application or requires a non-online filing path.

## Worker Responsibilities

1. Select `ready_for_submission` queue items.
2. Lock the item so another worker cannot submit the same responsible party on the same day.
3. Confirm the current time is within IRS online EIN assistant hours.
4. Launch a controlled browser context.
5. Navigate to the IRS EIN assistant.
6. Fill the wizard using the SS-4 payload:
   - Entity type.
   - LLC member count and tax classification path.
   - Responsible party name and SSN/ITIN.
   - Legal entity name.
   - Business and mailing address.
   - Formation/start date and formation state.
   - Reason for applying.
   - Contact and confirmation delivery options.
7. Review the final screen against the queue payload before submit.
8. Submit.
9. Save the official EIN confirmation letter/CP575 as PDF into `generated_docs/<order_id>/`.
10. Run `backend/ein_completion_ingest.py` or call equivalent ingestion logic.
11. Verify the customer portal shows the EIN confirmation document.

## Browser Worker Deployment Options

Preferred order:

1. Dedicated US browser-worker machines with persistent desktop/browser profiles and secure queue access.
2. A managed browser automation provider with compliant US egress, download capture, and secrets isolation.
3. Local desktop worker for bootstrap/testing only.

Do not rely on the main DigitalOcean web app server for live EIN submissions unless the IRS assistant preflight passes from that environment.

## Evidence And Logging

Allowed logs:

- Order ID.
- Worker ID.
- Queue state.
- Page/state names.
- Redacted payload fingerprint.
- Confirmation PDF filename.
- EIN last 4 only if needed for debugging.

Disallowed logs:

- Full SSN/ITIN.
- Full EIN before portal ingest.
- Screenshots containing SSN/ITIN unless encrypted and treated as sensitive evidence.
- IRS page HTML containing sensitive identifiers.

## Portal Completion Rule

The EIN step is complete only when:

1. Official IRS confirmation PDF/image exists under `generated_docs/<order_id>/`.
2. `backend/ein_completion_ingest.py` has added it as `doc_type = 'ein_confirmation_letter'`.
3. Order status is `ein_received` or `complete`.
4. The customer dashboard document vault includes the EIN confirmation document.

