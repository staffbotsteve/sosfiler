# State Portal Operations Notes

## Texas SOSDirect Pattern

Texas SOSDirect can be operated as a SOSFiler-controlled filer account rather than creating a new portal account for each applicant.

Observed flow, 2026-05-03:

1. SOSDirect requires a regular subscriber account before business filings can be submitted.
2. Temporary login allows inquiries and orders, but not filings.
3. The subscriber account receives a USER ID by email after account setup.
4. After login, SOSDirect asks for payment method details for the batch/session before the Business Organizations filing tools are available.
5. Filing workflow comes after login and batch payment setup: name inquiry, form selection, filing data entry, review, payment/submission, receipt capture.
6. Business Organizations > Web Filings exposes one entity-type selector. For Texas LLCs, select `Domestic Limited Liability Company (LLC)`, then `Certificate of Formation`.
7. Form 205 begins with Article 1 entity type/name, then initial mailing address, then Article 2 registered agent and registered office.
8. Article 2 requires either a registered-agent business name or individual name, a Texas registered-office street/building address, and consent status.
9. Texas explicitly warns that a preliminary name search is not a guarantee. SOSFiler should store name-search results as evidence, but final availability remains subject to SOS review after submission.

Operational implications:

- Treat the SOSDirect account as a production portal credential for SOSFiler's Texas filing adapter.
- Store the USER ID, password, and payment handling notes only in a secrets manager, not in repo docs, code, chat transcripts, or environment examples.
- Route SOSDirect emails into the filing job system so USER IDs, confirmations, receipts, rejections, and approvals become filing events with evidence.
- Treat the public Business Filing Tracker as a document-intake tracker only. Do not use `Received` there as the whole Texas filing status when SOSDirect entity inquiry may already show a stronger entity status such as `Pending Review`.
- For every Texas status check, reconcile at least two sources when available: public Business Filing Tracker plus SOSDirect entity inquiry or Briefcase.
- Store source-specific status fields separately: document tracker status, SOSDirect entity status, name status, briefcase status, and approval evidence status.
- Do not mark an order `submitted_to_state` unless a receipt/confirmation or filed image has been captured.
- Do not mark an order `state_approved` unless the Certificate of Filing or file-stamped formation document has been captured.
- Default customer notifications to off for operator evidence actions unless explicitly enabled for that order.
- Block Texas formation submission when the registered agent is not a Texas resident or authorized organization with a Texas street/building address.
- Do not use the customer's mailing address as the Texas registered office unless it is a Texas street/building address and the named agent has consented.

This pattern will likely repeat in other states: a state-level filer account, a session/batch payment method, and a separate per-order filing/payment/receipt workflow.

## Texas LLC Form 205 Portal Map

Observed pages in SOSDirect:

1. `Business Organizations Menu`
   - `Name Availability Search` is a paid inquiry and returns fuzzy/similar entity records.
   - `Web Filings` entity selector includes `Domestic Limited Liability Company (LLC)`.
2. `Filing Type`
   - Select `Certificate of Formation`.
3. `Article 1 - Entity Name and Type`
   - Organization type defaults to `Limited Liability Company`.
   - Entity name textbox requires a legal suffix such as `LLC`, `Limited Liability Company`, or equivalent.
   - Includes a separate `Name Availability Search` button from inside the filing.
4. `Initial Mailing Address`
   - Address used by the Texas Comptroller for tax information.
   - Allows out-of-state mailing addresses.
5. `Article 2 - Registered Agent and Office`
   - Registered agent can be an organization or an individual.
   - Registered office state is fixed to `TX`.
   - Registered office must be a street or building address; PO boxes are insufficient.
   - Consent defaults to `Consent on file with entity`, but SOSFiler must retain evidence before submission.

Dry-run finding: a Nevada organizer/member address can satisfy the initial mailing-address page, but it cannot satisfy the Texas registered-office requirement. For customer orders where the applicant does not have a Texas registered agent, the filing job must first assign an approved Texas registered-agent service.

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
