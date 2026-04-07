# ARTICLES OF ORGANIZATION
## Limited Liability Company
### State of Florida

**Filed with the Florida Division of Corporations**

---

### ARTICLE I — NAME

The name of the Limited Liability Company is: **{{llc_name}}**

### ARTICLE II — PRINCIPAL PLACE OF BUSINESS

The mailing address and street address of the principal office of the Limited Liability Company is:

Principal Address: {{principal_address}}
Mailing Address: {{mailing_address}}

### ARTICLE III — REGISTERED AGENT

The name and Florida street address of the registered agent:

Registered Agent Name: **{{registered_agent_name}}**
Registered Agent Address: {{registered_agent_street}}, {{registered_agent_city}}, Florida {{registered_agent_zip}}

### ARTICLE IV — MANAGER/MEMBER INFORMATION

{{#if manager_managed}}
The limited liability company is managed by: **Manager(s)**

Name and address of each manager:
{{#each managers}}
- {{name}}, {{address}}
{{/each}}
{{else}}
The limited liability company is managed by: **Member(s)**

Name and address of each member:
{{#each members}}
- {{name}}, {{address}}
{{/each}}
{{/if}}

### ARTICLE V — EFFECTIVE DATE

The effective date of this document is: {{filing_date}}

### ARTICLE VI — ADDITIONAL PROVISIONS

{{additional_provisions}}

---

**SIGNATURE OF AUTHORIZED PERSON**

Name: {{organizer_name}}
Title: Authorized Representative
Date: {{filing_date}}
Signature: _________________________

---

*Filed pursuant to Section 605.0201, Florida Statutes*
