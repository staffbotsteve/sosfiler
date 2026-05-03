# SOSFiler Operator Checklist

Use this as the running list of owner/operator tasks that are outside normal app clicks. When Steven asks for the separate things he needs to do, present this list first. The operating process lives in `docs/formation_process.md`.

## Access And Accounts

- [ ] Add `https://sosfiler.com/dashboard.html` as an allowed redirect/callback URL for Google OAuth.
- [ ] Add `https://sosfiler.com/dashboard.html` as an allowed redirect/callback URL for Apple Sign in.
- [ ] Add `https://sosfiler.com/dashboard.html` as an allowed redirect/callback URL for Facebook Login.
- [ ] Store the Texas SOSDirect account in the company credential vault with the account user ID, portal URL, and recovery email.
- [ ] Decide whether SOSFiler will keep one master filer account per state or per-operator accounts where state portals support it.

## Texas Formation Operations

- [ ] Monitor the Texas SOSDirect briefcase and admin inbox for approval/rejection of Clarence High LLC.
- [ ] When Texas approves Clarence High LLC, capture the filed certificate as evidence and mark the filing job `state_approved`.
- [ ] Verify the approval certificate appears in Jay Smith's customer portal document vault.
- [ ] Verify Jay Smith's portal shows all included deliverables: state filing, approval certificate, filing authorization, operating agreement, initial resolutions, membership certificate, organizational minutes, and EIN.

## EIN Workflow

- [ ] Install the EIN queue cron job on the production server.
- [ ] Confirm the cron job runs after Texas approval and creates `generated_docs/<order_id>/ein_queue.json`.
- [ ] Complete the IRS EIN application during IRS online assistant hours after the state approval certificate is available.
- [ ] Upload the EIN confirmation letter into the order document vault.
- [ ] Mark the EIN deliverable complete and notify the correct recipient according to the order notification policy.

## Customer Portal

- [ ] Confirm customers with multiple formations land on the company list first.
- [ ] Confirm each company opens its own dashboard with documents, order items, filing events, and compliance tasks.
- [ ] Test email/password reset from production.
- [ ] Test order-token recovery from production.
- [ ] Test Google, Apple, and Facebook sign-in from production.

## Registered Agent Strategy

- [ ] Choose registered agent provider strategy: integrate a national partner, state-by-state partners, or SOSFiler-owned service where feasible.
- [ ] Add registered agent consent and provider selection to checkout for every formation state.
- [ ] Build a fallback flow for customers who use their own registered agent.
- [ ] Add annual registered agent renewal reminders and billing where SOSFiler supplies the service.

## State Expansion

- [ ] Complete the state-by-state filing method map for LLC, corporation, nonprofit, amendments, dissolutions, annual reports, and certificates.
- [ ] Track state fee, card processing fee, expedited fee, and portal account requirements separately.
- [ ] Flag portals that support API/bulk upload versus browser automation versus manual filing.
- [ ] Define evidence required before any customer-facing status changes.
