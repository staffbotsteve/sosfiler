# ARTICLES OF ORGANIZATION
## Limited Liability Company
### State of Nevada

**Filed with the Nevada Secretary of State**

---

### 1. NAME

The name of the limited liability company is: **{{llc_name}}**

### 2. REGISTERED AGENT

The name and street address of the registered agent for service of process:

Name: **{{registered_agent_name}}**
Street Address: {{registered_agent_street}}, {{registered_agent_city}}, Nevada {{registered_agent_zip}}

### 3. MANAGEMENT

{{#if manager_managed}}
The company will be managed by: **Manager(s)**

Names and addresses of initial managers:
{{#each managers}}
- {{name}}, {{address}}
{{/each}}
{{else}}
The company will be managed by: **Its Members**

Names and addresses of initial members:
{{#each members}}
- {{name}}, {{address}}
{{/each}}
{{/if}}

### 4. AUTHORIZED SIGNER

Name: {{organizer_name}}
Address: {{organizer_address}}
Title: Organizer

### 5. CERTIFICATE OF ACCEPTANCE OF APPOINTMENT OF REGISTERED AGENT

I, {{registered_agent_name}}, hereby accept the appointment as Registered Agent for the above named limited liability company.

Authorized Signature of Registered Agent: _________________________
Date: {{filing_date}}

---

**ORGANIZER SIGNATURE**

Signature: _________________________
Printed Name: {{organizer_name}}
Date: {{filing_date}}

---

*Filed pursuant to NRS Chapter 86*
