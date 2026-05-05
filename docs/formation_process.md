# SOSFiler Formation Process

This is the living process document for how SOSFiler should intake, file, track, approve, document, and complete business formations. Keep adding state-specific rules and operational decisions here as we learn them.

## Process Principles

- A customer-facing status must be backed by evidence.
- State submission is not state approval.
- Name availability or name reservation is not entity formation.
- Customer documents and state evidence must be visible in the customer portal.
- External filings should default to operator/admin notifications until a customer-facing message is intentionally approved.
- Sensitive identifiers, portal credentials, payment details, and customer tax data must stay out of repo docs and chat transcripts.
- State/action research must follow `docs/50_state_research_program.md`; only verified official-source records should drive product promises, pricing, or automation.

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

Use these signals together. Do not report a single Texas status from the public Business Filing Tracker alone.

1. **SOSDirect/TXSOS email**
   - Watch the admin inbox for filing/order acknowledgements, approval notices, rejection notices, and document-ready emails.
   - Convert matching messages into filing events.
   - Attach the email body, attachment, PDF, image, or zip contents as evidence when available.

2. **SOSDirect Entity Inquiry**
   - Log into the SOSFiler Texas SOSDirect account.
   - Use `Business Organizations > Name Availability Search` / entity inquiry for the exact legal name.
   - Capture the filing number, entity type, entity status, name type, and name status.
   - Treat entity inquiry as the best non-document signal for the current entity lifecycle.
   - `Pending Review` means Texas has created an entity record, but has not issued final approval evidence.
   - `In existence` or equivalent must still be paired with a Certificate of Filing or filed/stamped document before marking `state_approved`.

3. **SOSDirect Briefcase**
   - Log into the SOSFiler Texas SOSDirect account.
   - Check the briefcase/order results for the submitted document.
   - Download available certificate, filed document, or rejection evidence.
   - Treat briefcase documents as time-sensitive; Texas materials warn that some documents may only be available for a limited period.

4. **Texas Business Filing Tracker**
   - URL: `https://webservices.sos.state.tx.us/filing-status/status.aspx`
   - Use the submitted filing/session/document identifiers to check status.
   - Treat this as the document-intake tracker, not the full entity lifecycle status.
   - `Received` means the submitted document has been received/entered into the tracker. It does not rule out a separate SOSDirect entity status such as `Pending Review`.
   - If the tracker exposes a certificate/rejection `View` link, retrieve it and reconcile with SOSDirect entity inquiry and briefcase.

5. **Certificate Verification**
   - URL: `https://sosdirectws.sos.state.tx.us/pdfondemand/CertVerification.aspx`
   - Use this after a Certificate of Filing has a certificate/document number.
   - Verification is supporting evidence, not a substitute for storing the actual certificate.

### Texas Composite Status Rule

When reporting Texas status internally or to the customer portal, store/report source-specific statuses:

- `document_tracker_status`: public Business Filing Tracker result, such as `Received`.
- `entity_status`: SOSDirect entity inquiry result, such as `Pending Review`.
- `name_status`: SOSDirect entity inquiry name result, such as `Pend. for Review`.
- `briefcase_status`: briefcase document result, such as no document, certificate available, or rejection available.
- `approval_evidence_status`: whether a Certificate of Filing, file-stamped formation document, or rejection letter has been captured.

The displayed operational status should be the strongest official signal:

1. Rejection evidence captured -> `rejected/operator_review`.
2. Approval certificate or file-stamped formation captured -> `state_approved`.
3. SOSDirect entity inquiry shows `Pending Review` -> `pending_state_review`.
4. Public tracker shows only `Received` and no entity inquiry has been checked -> `received_needs_sosdirect_check`.
5. No official state signal -> `submitted_unconfirmed`.

Never describe `document_tracker_status = Received` as the full Texas status unless SOSDirect entity inquiry and briefcase have not yet been checked, and make that limitation explicit.

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
5. Captured state approval evidence exists in the filing artifacts or customer document vault.
6. `backend/ein_queue_worker.py` creates the EIN queue artifact and adds `ein_pending` / `ein_queued` records.
7. After the IRS application is completed and the EIN confirmation/CP575 file is captured in `generated_docs/<order_id>/`, `backend/ein_completion_ingest.py` publishes it to the customer portal and records `ein_received`.

The queue worker does not submit anything to the IRS and does not email customers. It queues the EIN task for controlled operator/assisted processing. The completion ingester only publishes captured IRS confirmation documents into the portal.

## Current Texas/Jay Order Notes

- Order: `IL-825E3CBCCCC3`
- Entity: Clarence High LLC
- State: Texas
- Current known state: submitted to Texas, awaiting final filed/rejected result.
- Public Texas Business Filing Tracker check on May 4, 2026 Pacific time:
  - Document number: `1583055400004`
  - Entity name: Clarence High LLC
  - Delivery method: Web
  - Received: `05/03/2026`
  - Document type: Legal entity filing document
  - Status: `Received`
  - Filing number: not yet assigned
- SOSDirect entity inquiry screenshot supplied by Steven on May 5, 2026:
  - Filing number: `806580183`
  - Entity type: Domestic Limited Liability Company (LLC)
  - Entity status: `Pending Review`
  - Name type: Legal
  - Name status: `Pend. for Review`
  - Operational status: `pending_state_review`
- Next required evidence: Texas Certificate of Filing or rejection letter.
- EIN should not be started until the approval evidence is captured and the order is marked `state_approved`.
