# OPERATING AGREEMENT
## {{llc_name}}
### A {{state_name}} Limited Liability Company

### MULTI-MEMBER OPERATING AGREEMENT

**Effective Date:** {{effective_date}}

---

## ARTICLE I — ORGANIZATION

**1.1 Formation.** The Members listed herein have formed **{{llc_name}}** (the "Company"), a limited liability company under the laws of the State of {{state_name}}, by filing Articles of Organization with the {{filing_office}} on {{filing_date}}.

**1.2 Name.** The name of the Company is **{{llc_name}}**.

**1.3 Principal Office.** The principal office of the Company is located at: {{principal_address}}

**1.4 Registered Agent.** The registered agent for the Company is **{{registered_agent_name}}**, located at {{registered_agent_address}}.

**1.5 Term.** The Company shall continue in existence perpetually unless dissolved in accordance with this Agreement or applicable law.

**1.6 Purpose.** The Company is formed for the purpose of {{purpose}}, and any other lawful business purpose permitted under the laws of {{state_name}}.

## ARTICLE II — MEMBERS AND CAPITAL CONTRIBUTIONS

**2.1 Members.** The members of the Company, their addresses, capital contributions, and ownership percentages are:

| Name | Address | Capital Contribution | Ownership % |
|------|---------|---------------------|-------------|
{{#each members}}
| {{name}} | {{address}} | {{contribution}} | {{ownership_pct}}% |
{{/each}}

**2.2 Additional Contributions.** No Member shall be required to make additional capital contributions. Any additional contributions shall be made only upon the unanimous written consent of all Members and shall adjust ownership percentages proportionally (or as otherwise agreed).

**2.3 Capital Accounts.** A separate capital account shall be maintained for each Member. Each Member's capital account shall be:
- (a) Credited with the Member's capital contributions and allocated share of profits;
- (b) Debited with distributions to the Member and allocated share of losses.

**2.4 No Interest on Capital.** No Member shall be entitled to interest on any capital contribution.

**2.5 Return of Capital.** No Member has the right to demand a return of capital contributions except as provided herein or required by law.

## ARTICLE III — MANAGEMENT

{{#if manager_managed}}
**3.1 Manager-Managed.** The Company shall be managed by the following Manager(s):

{{#each managers}}
- **{{name}}** — {{title}}
{{/each}}

**3.2 Manager Authority.** The Manager(s) shall have full authority to manage the business and affairs of the Company, including the power to:
- (a) Enter into contracts and agreements;
- (b) Open and maintain bank accounts;
- (c) Hire and fire employees and contractors;
- (d) Purchase, sell, or lease property;
- (e) Borrow money;
- (f) Take any action necessary for the Company's business.

**3.3 Major Decisions.** The following actions require the approval of Members holding a majority of ownership interests:
- (a) Sale of all or substantially all Company assets;
- (b) Merger or conversion of the Company;
- (c) Amendment of this Agreement;
- (d) Admission of new Members;
- (e) Dissolution of the Company;
- (f) Incurring debt exceeding ${{major_decision_threshold}};
- (g) Entering into contracts exceeding ${{major_decision_threshold}};
- (h) Any transaction between the Company and a Manager or Member.

**3.4 Manager Compensation.** Manager(s) shall be compensated as follows: {{manager_compensation}}

**3.5 Removal of Manager.** A Manager may be removed with or without cause by a vote of Members holding {{manager_removal_vote_pct}}% of the total ownership interests.
{{else}}
**3.1 Member-Managed.** The Company shall be managed by its Members. Each Member shall have the authority to bind the Company in the ordinary course of business.

**3.2 Voting.** Each Member shall vote in proportion to their ownership percentage. Except as otherwise provided herein:
- **Ordinary business decisions:** Majority vote (>50% of ownership interests)
- **Major decisions:** {{major_decision_vote}}% of ownership interests

**3.3 Major Decisions.** The following actions require {{major_decision_vote}}% approval:
- (a) Sale of all or substantially all Company assets;
- (b) Merger or conversion of the Company;
- (c) Amendment of this Agreement;
- (d) Admission of new Members;
- (e) Dissolution of the Company;
- (f) Incurring debt exceeding ${{major_decision_threshold}};
- (g) Entering into contracts exceeding ${{major_decision_threshold}};
- (h) Any transaction between the Company and a Member.
{{/if}}

**3.6 Meetings.** Members may hold meetings in person, by telephone, or by video conference. {{meeting_frequency}} meetings shall be held. Special meetings may be called by any Member with {{meeting_notice_days}} days' written notice.

**3.7 Action Without Meeting.** Any action may be taken without a meeting if all Members consent in writing (including email).

## ARTICLE IV — PROFIT AND LOSS ALLOCATION

**4.1 Allocation Method.** {{#if custom_allocation}}Profits and losses shall be allocated as follows: {{allocation_method}}{{else}}Profits and losses shall be allocated to the Members in proportion to their respective ownership percentages.{{/if}}

**4.2 Tax Allocations.** For federal income tax purposes, the Company shall be treated as a partnership. Items of income, gain, loss, deduction, and credit shall be allocated in accordance with the profit and loss allocations set forth herein.

## ARTICLE V — DISTRIBUTIONS

**5.1 Distributions.** {{#if custom_distribution}}Distributions shall be made as follows: {{distribution_method}}{{else}}Distributions shall be made to the Members in proportion to their respective ownership percentages, at such times and in such amounts as determined by {{#if manager_managed}}the Manager(s){{else}}a majority vote of the Members{{/if}}.{{/if}}

**5.2 Minimum Distributions.** {{#if tax_distributions}}The Company shall make quarterly tax distributions sufficient to cover each Member's estimated tax liability attributable to Company income.{{else}}There is no requirement for minimum distributions.{{/if}}

**5.3 Limitation on Distributions.** No distribution shall be made if it would render the Company unable to pay its debts as they become due.

## ARTICLE VI — TRANSFER OF MEMBERSHIP INTERESTS

**6.1 Restrictions on Transfer.** No Member may sell, assign, pledge, or otherwise transfer all or any portion of their membership interest without the prior written consent of Members holding {{transfer_approval_pct}}% of the remaining ownership interests.

**6.2 Right of First Refusal.** Before transferring any interest to a third party, the transferring Member must first offer the interest to the existing Members in proportion to their ownership percentages, at the same price and on the same terms offered by the third party. Existing Members shall have {{rofr_days}} days to accept or decline.

**6.3 Valuation.** For purposes of any permitted transfer, the value of a Member's interest shall be determined by {{valuation_method}}.

**6.4 Prohibited Transfers.** Any transfer in violation of this Article shall be null and void.

## ARTICLE VII — WITHDRAWAL AND BUYOUT

**7.1 Voluntary Withdrawal.** A Member may withdraw from the Company upon {{withdrawal_notice_days}} days' written notice to all other Members.

**7.2 Buyout.** Upon withdrawal, death, disability, or bankruptcy of a Member:
- (a) The remaining Members shall have the right (but not the obligation) to purchase the departing Member's interest;
- (b) The purchase price shall be the fair market value of the interest as determined by {{valuation_method}};
- (c) Payment shall be made {{buyout_payment_terms}}.

**7.3 Death or Disability.** Upon the death or permanent disability of a Member, the deceased/disabled Member's interest shall be subject to the buyout provisions of Section 7.2.

## ARTICLE VIII — DISSOLUTION AND WINDING UP

**8.1 Dissolution Events.** The Company shall be dissolved upon the earliest to occur of:
- (a) Written consent of Members holding {{dissolution_vote_pct}}% of ownership interests;
- (b) Entry of a decree of judicial dissolution;
- (c) Any event that makes it unlawful for the Company's business to continue;
- (d) The passage of 90 consecutive days during which the Company has no members.

**8.2 Winding Up.** Upon dissolution, the Company's affairs shall be wound up by the Members (or a person appointed by the Members or a court). The assets shall be applied in the following order:
- (a) Payment of debts and liabilities owed to creditors;
- (b) Payment of debts and liabilities owed to Members;
- (c) Distribution to Members in accordance with their positive capital account balances.

**8.3 Articles of Dissolution.** Upon completion of winding up, Articles of Dissolution (or equivalent) shall be filed with the {{filing_office}}.

## ARTICLE IX — LIABILITY AND INDEMNIFICATION

**9.1 Limited Liability.** No Member or Manager shall be personally liable for any debts, obligations, or liabilities of the Company solely by reason of being a member or manager.

**9.2 Indemnification.** The Company shall indemnify and hold harmless each Member, Manager, and their agents from any claims, losses, or liabilities arising out of the Company's business, provided the person acted in good faith and in a manner reasonably believed to be in the best interests of the Company.

**9.3 Insurance.** The Company may purchase and maintain insurance on behalf of any Member, Manager, or agent.

## ARTICLE X — NON-COMPETE AND CONFIDENTIALITY

**10.1 Non-Compete.** {{#if non_compete}}During membership and for {{non_compete_months}} months after withdrawal, no Member shall engage in a business that directly competes with the Company within {{non_compete_geography}}.{{else}}Members are not restricted from engaging in other business activities, including those that may compete with the Company.{{/if}}

**10.2 Confidentiality.** Each Member agrees to maintain the confidentiality of all proprietary information of the Company during and after their membership.

## ARTICLE XI — DISPUTE RESOLUTION

**11.1 Mediation.** Any dispute arising under this Agreement shall first be submitted to mediation in {{mediation_location}}.

**11.2 Arbitration.** If mediation fails to resolve the dispute within 30 days, it shall be submitted to binding arbitration under the rules of the American Arbitration Association in {{arbitration_location}}.

**11.3 Costs.** The prevailing party in any dispute shall be entitled to recover reasonable attorney's fees and costs.

## ARTICLE XII — GENERAL PROVISIONS

**12.1 Governing Law.** This Agreement shall be governed by the laws of the State of {{state_name}}.

**12.2 Amendments.** This Agreement may be amended only by written consent of Members holding {{amendment_vote_pct}}% of the total ownership interests.

**12.3 Severability.** If any provision is held invalid, the remaining provisions remain in full force.

**12.4 Entire Agreement.** This Agreement constitutes the entire agreement among the Members.

**12.5 Notices.** All notices shall be in writing and sent to the addresses listed herein (or updated addresses provided in writing).

**12.6 Headings.** Headings are for convenience only.

---

## SIGNATURES

**IN WITNESS WHEREOF,** the undersigned have executed this Operating Agreement as of {{effective_date}}.

{{#each members}}
**MEMBER:**
Signature: _________________________
Printed Name: {{name}}
Ownership: {{ownership_pct}}%
Date: {{../effective_date}}

{{/each}}

---

*This Operating Agreement is a legally binding document. It is recommended that all Members consult with an attorney licensed in the applicable state before signing.*
