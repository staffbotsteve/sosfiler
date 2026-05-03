# SOSFiler 50-State Filing Research Program

This is the long-running research program for turning SOSFiler into a complete formation and business-change filing platform. The goal is not just to collect facts. The goal is to produce verified, machine-actionable filing instructions that can become product screens, pricing, operator tasks, automation adapters, and customer portal statuses.

NotebookLM notebook:

- `SOSFiler 50-State Formation and Change Filing Research`
- Notebook ID: `200a224f-8a1a-4f82-8104-9bcbddaf0e0d`

## Research Standard

Every state/action record must be sourced from official or primary materials whenever possible:

- Secretary of State, Corporation Commission, Department of State, or equivalent filing office.
- County clerk, recorder, assessor, tax collector, or equivalent county filing office for fictitious business names and county licenses.
- City clerk, finance department, business tax office, licensing department, or equivalent municipal filing office for city business licenses.
- Official fee schedules.
- Official forms and instructions.
- Official online filing portal pages/help pages.
- Official statutes or administrative rules when portal instructions are incomplete.
- Official payment processor disclosures for card/convenience fees.

Use competitor pages only as secondary product-comparison context. Do not use competitor pages as the source of truth for fees, required fields, or legal filing rules.

## Primary-Source Path

The research agent must start from official government pages and work outward.

For each state:

1. Start at the Secretary of State, Corporation Commission, Department of State, or equivalent business-entity filing office.
2. Find the official pages for:
   - business entity forms,
   - fee schedule,
   - online filing portal,
   - business search/name availability,
   - copies and certificates,
   - annual/periodic reports,
   - domestic formations,
   - foreign registrations/qualifications,
   - amendments,
   - registered agent changes,
   - dissolutions/withdrawals,
   - reinstatements.
3. Follow official links to related statewide agencies where needed:
   - revenue/tax department,
   - business licensing office,
   - professional licensing agencies,
   - one-stop business portal,
   - payment processor fee disclosures.
4. For local operating authority, use official county and city pages:
   - county clerk/recorder for fictitious business names,
   - county tax collector or licensing office for county business licenses,
   - city clerk/finance/business tax/licensing office for city licenses,
   - zoning or planning department for home occupation or zoning clearance.
5. Record the official URL for every fee, form, portal, required field, and evidence gate.

Commercial pages may be used only to discover likely filings or compare product packaging. Any fact discovered from a commercial page must be verified against official state/county/city material before it can be marked `verified_primary`, `verified_form`, or `verified_portal_observed`.

If an official state page links to an external vendor portal, payment processor, or one-stop system, treat that linked portal as official for procedural observation, but still record the government page that routes to it.

## Scope

Research all U.S. states plus District of Columbia.

For each jurisdiction, identify every customer-facing filing SOSFiler may reasonably sell. The phrase "formation" is broad for this program: it includes domestic entity formation, foreign qualification/registration to transact business, professional or regulated entity registration, nonprofit authority, and any state gateway that lets a person or entity legally conduct profitable or nonprofit activity in that state.

Include:

- LLC formation.
- Corporation formation.
- Nonprofit corporation formation.
- Professional entity formation, where available.
- Limited partnership / limited liability partnership filings, where supported.
- Foreign LLC registration / application for authority.
- Foreign corporation registration / qualification.
- Foreign nonprofit corporation registration / qualification.
- Foreign LP, LLP, LLLP, professional entity, statutory trust, or other entity authority filings where supported.
- Amendments to foreign registrations, including name, jurisdiction, address, registered agent, officers/directors/managers, and duration changes where accepted.
- Foreign entity withdrawal / cancellation / surrender of authority.
- Assumed-name filings required when a foreign entity's legal name is unavailable in the state.
- State tax registrations or gateway registrations that are bundled with or required immediately after authority to transact business.
- State business license registrations that are required as a general condition of doing business, separate from industry licenses.
- Name reservation.
- DBA / assumed name / fictitious name filings.
- County fictitious business name filings, renewals, abandonments, withdrawals, and publication requirements.
- City and county general business licenses, business tax certificates, privilege licenses, occupational licenses, gross-receipts licenses, and home-occupation permits where generally required to operate.
- Local business license renewals, amendments, address changes, ownership changes, and closure/cancellation filings.
- Registered agent change.
- Registered office change.
- Principal office / mailing address change.
- Manager, member, officer, director, or governing-person change filings where the state accepts or requires them.
- Articles/certificate amendments.
- Restatements.
- Conversions.
- Mergers where feasible for assisted filing.
- Dissolution / termination / cancellation.
- Withdrawal of foreign entity.
- Reinstatement.
- Annual report / periodic report / statement of information.
- Franchise-tax-linked public information report or similar compliance filing.
- Certificates of status / good standing / existence.
- Certified copies and plain copies.

Some internal company changes do not require a state filing. Those still need a SOSFiler process record if customers ask for them, because SOSFiler may generate documents such as member consents, amended operating agreements, resolutions, stock ledgers, membership ledgers, and customer portal evidence.

Foreign entity research must distinguish:

- Domestic formation in the customer's home state.
- Foreign qualification/registration in another state.
- General state business license or tax registration needed to legally operate.
- Local business license/permit needs, which may be handled by the license agent rather than the formation filing system.

Local licensing and fictitious-name research must distinguish:

- State-level DBA/assumed-name filings.
- County-level fictitious business name filings and publication requirements.
- City-level business licenses or business tax certificates.
- County-level general business licenses or tax registrations.
- Industry-specific licenses, which may stay under `license_agent.py` unless they are part of a general business-opening package.
- Jurisdictions where a city and county both require separate registrations.

## Deliverable Shape

Each researched filing must produce a record compatible with `data/filing_actions.schema.json` and eventually be added to `data/filing_actions.json`.

At minimum, each action needs:

- Jurisdiction and filing office.
- Entity type.
- Action type.
- Domestic vs foreign/entity authority classification.
- State, county, city, or multi-jurisdiction scope.
- Customer-facing product name.
- Whether it is state-filed, internal-document-only, or both.
- Filing method: API, web portal, upload, email, mail, in-person, or manual review.
- Portal URL and portal account requirements.
- Government fee.
- Processing/convenience/card fee.
- SOSFiler recommended service fee.
- Required customer inputs.
- Physical location, mailing address, NAICS/business activity, estimated receipts, employee count, zoning/home occupation details, and owner/officer data where local licenses require them.
- Required consents/signatures.
- Required generated documents.
- Required certificates from the home jurisdiction, such as certificate of existence/good standing.
- Required evidence before status changes.
- Approval/rejection detection method.
- Customer portal document/status outputs.
- Automation feasibility and blockers.
- Official source URLs.
- Verification date.

## Source Verification Levels

- `verified_primary`: official source confirms the field directly.
- `verified_form`: official form/instructions confirm the field.
- `verified_portal_observed`: operator observed the field in the portal.
- `needs_confirmation`: plausible but not yet proven from official materials.
- `unsupported`: SOSFiler should not offer this action yet.

Only `verified_primary`, `verified_form`, or `verified_portal_observed` records may power automatic pricing or customer-facing filing promises.

## Research Workflow

1. Start with one state and one entity family.
2. Begin at the official state business filing office pages for forms, fees, online services, and instructions.
3. Collect official domestic formation, foreign qualification/authority, amendment, annual report, dissolution/withdrawal, registered agent, DBA/FBN, city/county general business license, tax registration, and copy/certificate materials.
4. Identify the portal and whether the action can be filed online.
5. Record all fees and processing fees separately.
6. Record every required customer input and generated document.
7. For foreign entities, record whether a certificate of existence/good standing from the home jurisdiction is required, how recent it must be, and whether a fictitious/assumed name is required when the legal name is unavailable.
8. For city/county filings, record whether the jurisdiction requires publication, zoning clearance, home occupation review, tax registration, estimated gross receipts, inspections, or renewal tracking.
9. Record the evidence required for:
   - ready to file,
   - submitted,
   - approved,
   - rejected,
   - completed.
10. Note whether customer notification should be automatic or operator-reviewed.
11. Add the action record to the research queue or `filing_actions.json`.
12. Build or update the product workflow and portal/operator adapter.
13. Test with notional data before customer use.

## Implementation Targets

The research should drive these product areas:

- Checkout questions and conditional fields.
- Fee calculation.
- Registered agent decisioning.
- Filing job creation.
- Operator queue instructions.
- Playwright/browser automation where appropriate.
- API integrations where states expose usable interfaces.
- Document generation.
- Customer portal order items and document vault.
- Status notification policy.
- Compliance calendar.
- Upsell/cross-sell surfaces for later changes and annual filings.
- License-agent integration for city/county business licenses and industry-specific licenses.

## First States

Recommended order:

1. Texas, because Jay's order is live and the workflow is already partially mapped.
2. Nevada, because SOSFiler already has SilverFlume automation scaffolding.
3. California, Delaware, Florida, Wyoming, New York, Georgia, and Illinois, because they are likely high-volume states.
4. Remaining states by filing-volume opportunity and automation feasibility.

## Open Product Questions

- Which entity types will SOSFiler offer immediately versus later?
- Which filings require attorney review or should be excluded?
- Which changes can be self-service and which require operator review?
- Should SOSFiler file internal-only changes for a fee even when no state filing is required?
- What registered agent partner strategy will be used in states where customers need an in-state agent?
- How should SOSFiler price amendments, annual reports, dissolutions, and certificates?
- What evidence should be shown to customers versus retained internally only?
- Which foreign qualification filings can be completed without ordering a separate certificate of good standing from the home state?
- Which general state business registrations belong in SOSFiler's formation/change workflow versus the license agent workflow?
- Which local license/FBN filings are common enough to productize directly versus route to license-agent/operator research?
- How should SOSFiler price publication coordination for fictitious business names?
- Which local business-license filings require customer-provided revenue estimates, zoning details, landlord approval, inspections, or local tax account setup?
