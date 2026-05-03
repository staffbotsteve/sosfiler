# INITIAL BOARD RESOLUTIONS
## {{entity_name}}
### A {{state_name}} Corporation

**Date of Adoption:** {{effective_date}}

---

The undersigned directors of {{entity_name}} (the "Corporation") adopt the following resolutions effective as of the date above.

## RESOLUTION 1 - ORGANIZATION

**RESOLVED,** that the filing of the Articles of Incorporation with the {{filing_office}} is ratified and approved.

## RESOLUTION 2 - BYLAWS

**RESOLVED,** that the Corporate Bylaws presented to the Board are adopted as the bylaws of the Corporation.

## RESOLUTION 3 - OFFICERS

**RESOLVED,** that the following persons are appointed as initial officers of the Corporation:

{{#each officers}}
- {{office}}: {{name}}
{{/each}}

## RESOLUTION 4 - ISSUANCE OF SHARES

**RESOLVED,** that the Corporation is authorized to issue the following shares as fully paid and non-assessable, subject to receipt of consideration approved by the Board:

{{#each shareholders}}
- {{name}}: {{shares}} shares
{{/each}}

## RESOLUTION 5 - STOCK LEDGER AND CERTIFICATES

**RESOLVED,** that the officers are authorized to establish a stock ledger and issue stock certificates or electronic book-entry records to the initial shareholders.

## RESOLUTION 6 - EMPLOYER IDENTIFICATION NUMBER

**RESOLVED,** that the officers are authorized and directed to apply for an Employer Identification Number (EIN) from the Internal Revenue Service.

## RESOLUTION 7 - BANKING

**RESOLVED,** that the Corporation is authorized to open bank accounts at {{bank_name}} or another financial institution selected by the officers, and the following persons are authorized signers:

{{#each authorized_signers}}
- {{name}} - {{title}}
{{/each}}

## RESOLUTION 8 - TAX ELECTIONS

**RESOLVED,** that the officers are authorized to make federal or state tax elections appropriate for the Corporation{{#if s_corp_election}}, including an S corporation election if timely and eligible{{/if}}.

## RESOLUTION 9 - AUTHORITY

**RESOLVED,** that the officers are authorized to take all actions necessary or advisable to carry out these resolutions.

---

## DIRECTOR CONSENT

{{#each corporate_signers}}
Signature: _________________________
Printed Name: {{name}}
Title: {{title}}
Date: {{../effective_date}}

{{/each}}
