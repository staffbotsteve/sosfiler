# SOSFiler 50-State Filing Research Program

This is the long-running research program for turning SOSFiler into a complete formation and business-change filing platform. The goal is not just to collect facts. The goal is to produce verified, machine-actionable filing instructions that can become product screens, pricing, operator tasks, automation adapters, and customer portal statuses.

NotebookLM notebook:

- `SOSFiler 50-State Formation and Change Filing Research`
- Notebook ID: `200a224f-8a1a-4f82-8104-9bcbddaf0e0d`

## Research Standard

Every state/action record must be sourced from official or primary materials whenever possible:

- Secretary of State, Corporation Commission, Department of State, or equivalent filing office.
- Official fee schedules.
- Official forms and instructions.
- Official online filing portal pages/help pages.
- Official statutes or administrative rules when portal instructions are incomplete.
- Official payment processor disclosures for card/convenience fees.

Use competitor pages only as secondary product-comparison context. Do not use competitor pages as the source of truth for fees, required fields, or legal filing rules.

## Scope

Research all U.S. states plus District of Columbia.

For each jurisdiction, identify every customer-facing filing SOSFiler may reasonably sell, including:

- LLC formation.
- Corporation formation.
- Nonprofit corporation formation.
- Professional entity formation, where available.
- Limited partnership / limited liability partnership filings, where supported.
- Name reservation.
- DBA / assumed name / fictitious name filings.
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

## Deliverable Shape

Each researched filing must produce a record compatible with `data/filing_actions.schema.json` and eventually be added to `data/filing_actions.json`.

At minimum, each action needs:

- Jurisdiction and filing office.
- Entity type.
- Action type.
- Customer-facing product name.
- Whether it is state-filed, internal-document-only, or both.
- Filing method: API, web portal, upload, email, mail, in-person, or manual review.
- Portal URL and portal account requirements.
- Government fee.
- Processing/convenience/card fee.
- SOSFiler recommended service fee.
- Required customer inputs.
- Required consents/signatures.
- Required generated documents.
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
2. Collect official formation, amendment, annual report, dissolution, registered agent, and copy/certificate materials.
3. Identify the portal and whether the action can be filed online.
4. Record all fees and processing fees separately.
5. Record every required customer input and generated document.
6. Record the evidence required for:
   - ready to file,
   - submitted,
   - approved,
   - rejected,
   - completed.
7. Note whether customer notification should be automatic or operator-reviewed.
8. Add the action record to the research queue or `filing_actions.json`.
9. Build or update the product workflow and portal/operator adapter.
10. Test with notional data before customer use.

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
