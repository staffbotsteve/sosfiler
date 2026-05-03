# State Portal Operations Notes

## Texas SOSDirect Pattern

Texas SOSDirect can be operated as a SOSFiler-controlled filer account rather than creating a new portal account for each applicant.

Observed flow, 2026-05-03:

1. SOSDirect requires a regular subscriber account before business filings can be submitted.
2. Temporary login allows inquiries and orders, but not filings.
3. The subscriber account receives a USER ID by email after account setup.
4. After login, SOSDirect asks for payment method details for the batch/session before the Business Organizations filing tools are available.
5. Filing workflow comes after login and batch payment setup: name inquiry, form selection, filing data entry, review, payment/submission, receipt capture.

Operational implications:

- Treat the SOSDirect account as a production portal credential for SOSFiler's Texas filing adapter.
- Store the USER ID, password, and payment handling notes only in a secrets manager, not in repo docs, code, chat transcripts, or environment examples.
- Route SOSDirect emails into the filing job system so USER IDs, confirmations, receipts, rejections, and approvals become filing events with evidence.
- Do not mark an order `submitted_to_state` unless a receipt/confirmation or filed image has been captured.
- Do not mark an order `state_approved` unless the Certificate of Filing or file-stamped formation document has been captured.
- Default customer notifications to off for operator evidence actions unless explicitly enabled for that order.

This pattern will likely repeat in other states: a state-level filer account, a session/batch payment method, and a separate per-order filing/payment/receipt workflow.

## Registered Agent Service Workstream

SOSFiler needs a registered-agent fulfillment strategy for customers who do not want to act as their own registered agent in the formation state.

Research and product decisions needed:

- Which states allow SOSFiler or an affiliate to serve directly as registered agent.
- Which states require a physical in-state commercial registered agent partner.
- Whether to partner with a national RA network, state-by-state providers, or create SOSFiler entities/addresses in high-volume states.
- Pricing and renewal model by state.
- Consent-to-serve workflow and evidence retention.
- Portal-specific RA fields and any state-specific acceptance language.
- Customer portal display for RA name/address, renewal date, and change-of-agent actions.

Initial product model:

- `ra_choice = self`: customer supplies RA details and consent is retained.
- `ra_choice = sosfiler`: SOSFiler assigns an approved in-state RA provider before filing.
- Filing job cannot proceed to state submission until the assigned RA name/address and consent evidence are attached.
