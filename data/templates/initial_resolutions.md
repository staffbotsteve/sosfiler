# INITIAL RESOLUTIONS
## {{llc_name}}
### A {{state_name}} Limited Liability Company

**Date of Adoption:** {{effective_date}}

---

The undersigned, being {{#if manager_managed}}the Manager(s){{else}}all of the Members{{/if}} of **{{llc_name}}** (the "Company"), a {{state_name}} limited liability company, hereby adopt the following resolutions effective as of the date first written above:

## RESOLUTION 1 — ADOPTION OF OPERATING AGREEMENT

**RESOLVED,** that the Operating Agreement of the Company, dated {{effective_date}}, is hereby adopted and ratified as the governing document of the Company.

## RESOLUTION 2 — DESIGNATION OF TAX YEAR AND ACCOUNTING METHOD

**RESOLVED,** that the Company's tax year shall be the calendar year (January 1 through December 31), and the Company shall use the {{accounting_method}} method of accounting.

## RESOLUTION 3 — DESIGNATION OF TAX CLASSIFICATION

**RESOLVED,** that the Company shall be classified for federal income tax purposes as a {{tax_classification}}{{#if s_corp_election}} and that an S Corporation election shall be filed with the Internal Revenue Service{{/if}}.

## RESOLUTION 4 — EMPLOYER IDENTIFICATION NUMBER

**RESOLVED,** that the {{#if manager_managed}}Manager(s) are{{else}}Members are{{/if}} authorized and directed to apply for an Employer Identification Number (EIN) from the Internal Revenue Service on behalf of the Company.

## RESOLUTION 5 — BANKING

**RESOLVED,** that the Company is authorized to open bank accounts at {{bank_name}} or such other financial institution(s) as the {{#if manager_managed}}Manager(s) may{{else}}Members may{{/if}} select, and that the following persons are authorized as signers on all Company bank accounts:

{{#each authorized_signers}}
- {{name}} — {{title}}
{{/each}}

## RESOLUTION 6 — REGISTERED AGENT

**RESOLVED,** that **{{registered_agent_name}}** is hereby confirmed as the registered agent of the Company in the State of {{state_name}}, with the registered office address at {{registered_agent_address}}.

## RESOLUTION 7 — BUSINESS LICENSES AND PERMITS

**RESOLVED,** that the {{#if manager_managed}}Manager(s) are{{else}}Members are{{/if}} authorized to apply for and obtain all necessary business licenses, permits, and registrations required for the conduct of the Company's business at the federal, state, and local level.

## RESOLUTION 8 — INDEMNIFICATION

**RESOLVED,** that the Company shall indemnify its {{#if manager_managed}}Managers and{{/if}} Members to the fullest extent permitted by {{state_name}} law, as set forth in the Operating Agreement.

## RESOLUTION 9 — INITIAL CAPITAL CONTRIBUTIONS

**RESOLVED,** that the initial capital contributions of the Members, as set forth in the Operating Agreement, are hereby acknowledged and accepted:

{{#each members}}
- **{{name}}:** {{contribution}} ({{ownership_pct}}% ownership interest)
{{/each}}

## RESOLUTION 10 — AUTHORIZATION OF ACTIONS

**RESOLVED,** that the {{#if manager_managed}}Manager(s) are{{else}}Members are{{/if}} authorized to take any and all actions necessary to carry out the foregoing resolutions and to execute any documents required in connection therewith.

---

## CERTIFICATION

The undersigned hereby certify that the above resolutions were duly adopted by {{#if manager_managed}}the Manager(s){{else}}all Members{{/if}} of the Company effective as of the date first written above.

{{#each signers}}
Signature: _________________________
Printed Name: {{name}}
Title: {{title}}
Date: {{../effective_date}}

{{/each}}
