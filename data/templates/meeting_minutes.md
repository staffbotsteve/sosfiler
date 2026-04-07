# ORGANIZATIONAL MEETING MINUTES
## {{llc_name}}
### A {{state_name}} Limited Liability Company

---

**Date:** {{effective_date}}
**Time:** {{meeting_time}}
**Location:** {{meeting_location}}

## ATTENDANCE

The following {{#if manager_managed}}Managers{{else}}Members{{/if}} were present, constituting a quorum:

{{#each attendees}}
- {{name}} ({{title}}, {{ownership_pct}}% ownership)
{{/each}}

## CALL TO ORDER

The meeting was called to order at {{meeting_time}} by {{chairperson_name}}, who presided as Chairperson. {{secretary_name}} was appointed as Secretary of the meeting.

## FORMATION OF THE COMPANY

The Chairperson reported that **{{llc_name}}** was duly organized as a limited liability company under the laws of the State of {{state_name}} by the filing of Articles of Organization with the {{filing_office}} on {{filing_date}}.

A copy of the filed Articles of Organization was presented and ordered to be filed with the Company records.

## OPERATING AGREEMENT

The Operating Agreement of the Company, dated {{effective_date}}, was presented. Upon motion duly made, seconded, and unanimously carried, it was:

**RESOLVED,** that the Operating Agreement, as presented, is hereby adopted as the governing document of the Company, and that each member shall execute the Operating Agreement.

## REGISTERED AGENT

It was noted that the Company's registered agent is **{{registered_agent_name}}**, with the registered office at {{registered_agent_address}}.

## EMPLOYER IDENTIFICATION NUMBER

Upon motion duly made, seconded, and unanimously carried, it was:

**RESOLVED,** that the {{#if manager_managed}}Manager(s) are{{else}}appropriate Member(s) are{{/if}} authorized to apply for an Employer Identification Number (EIN) from the Internal Revenue Service.

## BANK ACCOUNTS

Upon motion duly made, seconded, and unanimously carried, it was:

**RESOLVED,** that the Company is authorized to open business bank account(s) at {{bank_name}} or such other financial institution as may be selected, and that the following individuals are authorized signers:

{{#each authorized_signers}}
- {{name}} ({{title}})
{{/each}}

## TAX ELECTIONS

Upon motion duly made, seconded, and unanimously carried, it was:

**RESOLVED,** that the Company shall adopt a calendar year as its fiscal year and shall use the {{accounting_method}} method of accounting for tax and financial reporting purposes.

**RESOLVED,** that the Company shall be classified as a {{tax_classification}} for federal income tax purposes.

## INITIAL CAPITAL CONTRIBUTIONS

The following initial capital contributions were acknowledged:

{{#each members}}
| {{name}} | {{contribution}} | {{ownership_pct}}% |
{{/each}}

## BUSINESS LICENSES AND PERMITS

Upon motion duly made, seconded, and unanimously carried, it was:

**RESOLVED,** that the {{#if manager_managed}}Manager(s) are{{else}}Members are{{/if}} authorized to obtain all necessary business licenses, permits, and registrations.

## ADDITIONAL BUSINESS

{{additional_business}}

## ADJOURNMENT

There being no further business, upon motion duly made, seconded, and unanimously carried, the meeting was adjourned at {{adjournment_time}}.

---

**Secretary:**
Signature: _________________________
Printed Name: {{secretary_name}}
Date: {{effective_date}}

**Chairperson:**
Signature: _________________________
Printed Name: {{chairperson_name}}
Date: {{effective_date}}
