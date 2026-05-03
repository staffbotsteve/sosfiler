# SOSFiler Formation Process

This is the living process document for how SOSFiler should intake, file, track, approve, document, and complete business formations. Keep adding state-specific rules and operational decisions here as we learn them.

## Process Principles

- A customer-facing status must be backed by evidence.
- State submission is not state approval.
- Name availability or name reservation is not entity formation.
- Customer documents and state evidence must be visible in the customer portal.
- External filings should default to operator/admin notifications until a customer-facing message is intentionally approved.
- Sensitive identifiers, portal credentials, payment details, and customer tax data must stay out of repo docs and chat transcripts.

## Formation Lifecycle

1. Customer submits order and pays SOSFiler/state fees.
2. SOSFiler generates the internal document package:
   - Filing authorization/consent to file.
   - Operating agreement or bylaws, as applicable.
   - Initial resolutions.
   - Organizer/incorporator/member/director documents as applicable.
   - SS-4/EIN data packet.
3. Filing job enters the operator queue.
4. Operator or automation checks state-specific blockers:
   - Name availability rules.
   - Registered agent eligibility and consent.
   - Required addresses and organizer/governing-person details.
   - State fee, processing fee, and payment method requirements.
5. State filing is submitted through the state-supported channel.
6. Submission receipt/evidence is captured and attached.
7. Filing is monitored until approved or rejected.
8. Approval/rejection evidence is captured and attached.
9. If approved, the order moves to `state_approved`.
10. EIN filing is queued only after state approval.
11. EIN confirmation is captured and attached.
12. Final package is complete in the customer portal.

## Texas Approval Detection

For Texas LLC formations, the trigger for EIN work is not name reservation or name availability. The trigger is proof that Texas has filed the Certificate of Formation.

### Texas Signals

Use these signals in priority order:

1. **SOSDirect/TXSOS email**
   - Watch the admin inbox for filing/order acknowledgements, approval notices, rejection notices, and document-ready emails.
   - Convert matching messages into filing events.
   - Attach the email body, attachment, PDF, image, or zip contents as evidence when available.

2. **Texas Business Filing Tracker**
   - URL: `https://webservices.sos.state.tx.us/filing-status/status.aspx`
   - Use the submitted filing/session/document identifiers to check status.
   - Texas status should move from received/processing to filed or rejected.
   - If filed, retrieve the Certificate of Filing or file-stamped document.
   - If rejected, retrieve the rejection letter and move the job to operator review.

3. **SOSDirect Briefcase**
   - Log into the SOSFiler Texas SOSDirect account.
   - Check the briefcase/order results for the submitted document.
   - Download available certificate, filed document, or rejection evidence.
   - Treat briefcase documents as time-sensitive; Texas materials warn that some documents may only be available for a limited period.

4. **Certificate Verification**
   - URL: `https://sosdirectws.sos.state.tx.us/pdfondemand/CertVerification.aspx`
   - Use this after a Certificate of Filing has a certificate/document number.
   - Verification is supporting evidence, not a substitute for storing the actual certificate.

### Texas State Approval Gate

Do not mark a Texas order `state_approved` until SOSFiler has captured one of:

- Texas Certificate of Filing.
- File-stamped Certificate of Formation.
- Official Texas approval document showing the entity is filed.

When approval evidence exists:

1. Attach the evidence as a filing artifact.
2. Add it to the customer document vault.
3. Call the admin mark-approved flow for the filing job.
4. Keep customer notification off unless the order notification policy says otherwise.
5. Let the EIN queue cron pick up the `state_approved` order.

### Texas Rejection Gate

If Texas rejects the filing:

1. Attach the rejection letter/message as evidence.
2. Move the filing job to operator review.
3. Update the order item for state filing as blocked/rejected, not complete.
4. Identify the correction required.
5. Do not queue EIN work.
6. Do not notify the customer until the operator decides whether a correction can be made without customer action.

## EIN Trigger

EIN filing starts only after state approval evidence exists.

Internal trigger:

1. Order has `status = 'state_approved'`.
2. Order has `approved_at`.
3. Order does not already have an EIN.
4. No existing EIN queue status or `generated_docs/<order_id>/ein_queue.json` exists.
5. `backend/ein_queue_worker.py` creates the EIN queue artifact and adds `ein_pending` / `ein_queued` records.

This worker does not submit anything to the IRS and does not email customers. It queues the EIN task for operator/assisted processing.

## Current Texas/Jay Order Notes

- Order: `IL-825E3CBCCCC3`
- Entity: Clarence High LLC
- State: Texas
- Current known state: submitted to Texas, awaiting final filed/rejected result.
- Next required evidence: Texas Certificate of Filing or rejection letter.
- EIN should not be started until the approval evidence is captured and the order is marked `state_approved`.
