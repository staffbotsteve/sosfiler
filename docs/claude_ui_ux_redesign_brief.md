# SOSFiler UI/UX Redesign Brief for Claude

Use this brief to redesign the SOSFiler website and product experience while preserving the working production backend, payment flow, all-state launch readiness, and operator-assisted fulfillment model.

## Mission

SOSFiler is not a hobby project or a generic landing page. It is a revenue-generating entity formation and post-formation management platform for business owners across all U.S. states and D.C.

The redesign must make SOSFiler feel like serious, reliable, fast, enterprise-grade software with transparent pricing and operational follow-through. The site should convince a business owner that SOSFiler can take their order today, collect the right information, charge clearly, route the filing through the fastest compliant lane, and keep them updated with evidence-backed status.

Do not make this a Texas-first product. The public experience must support national checkout and fulfillment. Texas can be shown as automated where appropriate, but the product promise is all-state coverage with automation-first and operator-assisted fulfillment where required.

## Current Product Reality

Production already supports:

- Public customer intake wizard at `frontend/app.html`
- Public marketing/home page at `frontend/index.html`
- Customer dashboard at `frontend/dashboard.html`
- Operator cockpit at `frontend/operator.html`
- Shared chat widget at `frontend/chat-widget.js` and `frontend/chat-widget.css`
- Privacy/terms pages at `frontend/privacy.html` and `frontend/terms.html`
- Registered agent rescue page at `frontend/ra-rescue.html`
- Formation availability API at `/api/formation-availability`
- State launch readiness and operator packets in admin APIs
- Stripe Checkout with manual capture
- SendGrid live delivery from `support@sosfiler.com`
- Operator fulfillment packets for every state plus D.C.

CorpNet API credentials are pending. The UI must not pretend partner registered-agent automation is complete. It should present registered-agent fulfillment as operator/partner-assisted until credentials are configured, with clear customer expectations.

## Primary Business Goals

1. Increase trust and conversion for business owners ready to form an entity now.
2. Make national coverage clear without overpromising unsafe full automation.
3. Preserve transparent pricing: official government fees, the $29 SOSFiler service fee for formations, the $29 SOSFiler service fee for annual filings, optional partner costs, and total.
4. Make operator-assisted states feel like a professional fulfillment lane, not a weakness.
5. Make checkout confidence high: clear authorization, no hidden fees, no surprise upsells.
6. Make the dashboard feel evidence-backed: statuses, next actions, documents, receipts, and timelines.
7. Make the operator cockpit efficient enough to fulfill real paid orders across all states.

## Design Direction

The product should feel quiet, precise, and operational. Avoid oversized marketing theatrics. Use a modern SaaS/fintech/compliance feel:

- Clear hierarchy
- Dense but readable information
- Strong trust signals
- Clean comparison and pricing surfaces
- Minimal decorative noise
- No decorative icons, emoji, icon-only controls, or mascot-like visuals
- Fast path to start an order
- Professional dashboard and operator tooling

The user dislikes icons. Treat this as a text-first enterprise interface. Use numbers, short labels, tables, step names, dividers, and typography for structure. Do not add icon libraries. Remove existing decorative icons/emoji from the public site and dashboard as pages are redesigned.

Keep the SOSFiler neon/accent identity if useful, but reduce any overly gamer-like or novelty feel. The customer should feel: "This is fast, serious, transparent, and accountable."

## Pages to Redesign

### 1. Public Home: `frontend/index.html`

The homepage must quickly answer:

- What is SOSFiler?
- What does it cost?
- Can it handle my state?
- What happens after I pay?
- Is this automated or operator-assisted?
- Why should I trust it?

Required sections:

- First viewport with a direct formation CTA and price clarity.
- State availability/search or "choose your state" entry point.
- Transparent pricing explanation.
- Pricing must say: formation is $29 plus official state fees; annual filings are $29 plus official state fees; SOSFiler does not charge an expedite fee.
- Automation-first fulfillment explanation.
- Evidence-backed dashboard/status explanation.
- Competitor comparison focused on price, speed, transparency, annual filing management, status evidence, and no hidden subscriptions.
- Registered agent section that is honest while CorpNet key is pending.
- Legal disclaimer: document preparation and filing service, not a law firm.

Do not create a generic startup hero that could belong to any SaaS company.

### 2. Formation Wizard: `frontend/app.html`

This is the money path. Preserve API calls and form payload shape unless a backend-compatible change is absolutely required.

Current critical calls:

- `GET /api/formation-availability`
- `POST /api/quote`
- `POST /api/orders`
- `POST /api/orders/{order_id}/authorize`

Required UX improvements:

- Make each step feel shorter and more confident.
- Keep national state selection obvious.
- Show fulfillment lane for selected state: automated, operator-assisted, partner-assisted, customer-data-required.
- Show exact price breakdown before checkout.
- Make registered-agent choice clear and honest while CorpNet key is pending.
- Use inline validation with specific fixes.
- Reduce alert boxes; use inline errors and status regions.
- Preserve legal authorization checkboxes.
- Preserve local progress saving.
- Make mobile flow polished and usable.

Do not remove or weaken the authorization language around SOSFiler acting as organizer/filing agent.

### 3. Customer Dashboard: `frontend/dashboard.html`

The dashboard must answer:

- What is the current status?
- What is SOSFiler doing next?
- What does the customer need to do?
- What documents/evidence are available?
- What fees were authorized/captured?
- What lifecycle workflows are available after formation?

Required UX improvements:

- Evidence-backed timeline with clear states.
- Next action panel.
- Document vault with receipts, confirmations, filed documents, EIN letters, annual report packets.
- Transparent payment/fee summary.
- Post-formation actions: annual reports, amendments, RA changes, certificates, certified copies, dissolution/withdrawal, reinstatement, reminders.
- Clear messaging for operator-required states.

### 4. Operator Cockpit: `frontend/operator.html`

This is mission-critical operations software. It must remain functional and dense.

The operator cockpit now receives `job.operator_fulfillment_packet` from `/api/admin/filing-jobs/{job_id}`. The redesign must make this packet the core filing surface.

The packet contains:

- Customer filing data
- Registered agent data
- Member/manager data
- Official route and portal
- Operator actions
- State portal steps
- Safe-stop conditions
- Required evidence
- Completion criteria

Required UX improvements:

- Make "what do I do next?" obvious.
- Put operator fulfillment packet above secondary diagnostics.
- Show customer data in copy-friendly structured fields.
- Show official route and portal link.
- Show state-specific portal steps.
- Show safe-stop conditions prominently.
- Show evidence requirements before status action buttons.
- Make status transitions hard to misuse.
- Keep PII redaction intact. Do not expose full SSN/ITIN in the cockpit.
- Preserve admin session/auth behavior.

### 5. Legal & Trust Pages

Keep `frontend/privacy.html` and `frontend/terms.html` legally clear. Improve readability and consistency, but do not remove:

- Not-a-law-firm disclaimer
- Organizer/filing authorization terms
- Transparent government-fee language
- No hidden fees/no auto-renewal language
- PII/security language

## Accessibility & Interface Requirements

Follow WCAG-oriented best practices:

- Semantic landmarks: `header`, `main`, `nav`, `section`, `footer`
- Skip link to main content
- Buttons for actions, anchors for navigation
- All form fields need labels and `name`
- Correct input types and autocomplete attributes
- Inline errors near fields
- Focus first invalid field on submit
- Visible `:focus-visible` styles
- Do not remove outlines without replacement
- Touch targets at least 44x44
- Keyboard-accessible dialogs, tabs, and controls
- `aria-live="polite"` for async status/toasts
- Respect `prefers-reduced-motion`
- Do not use `transition: all`
- Do not disable zoom
- Images need dimensions and meaningful alt text
- Use `Intl.NumberFormat` for money
- Use `Intl.DateTimeFormat` for dates
- Long text must wrap/truncate without breaking layout

## Visual/UI Constraints

- Do not use decorative gradient blobs/orbs/bokeh.
- Do not use nested cards inside cards.
- Cards should be for real contained items, modals, repeated records, and tooling panels.
- Avoid a one-note purple/blue or dark-slate-only palette.
- Avoid huge hero typography inside tool surfaces.
- Do not hide important state coverage details behind vague marketing copy.
- Do not create a landing page that delays the actual product. The first screen should drive a formation order.
- Do not use decorative icons or emoji. If an external brand mark is legally required for an OAuth button, keep it only inside that authentication button and pair it with text.
- Avoid checkmark/cross icon tables. Use words such as "Included", "Add-on", "Not included", "Evidence-backed", and "Plan-based".

## Security & Compliance Constraints

- Do not expose secrets, admin tokens, Stripe keys, SendGrid keys, or PII in frontend code.
- Do not expose full SSNs/ITINs in operator UI.
- Do not add CAPTCHA-solving, bypass, scraping, or unsafe automation language.
- Do not weaken terms, payment authorization, or evidence-gated status updates.
- Do not mark filings submitted/approved/complete in UI copy unless official evidence exists.
- Do not change API payloads without updating backend tests.
- Do not remove manual-capture payment messaging.

## Backend/API Contracts to Preserve

Do not break these unless you also update backend and tests:

- `GET /api/formation-availability`
- `GET /api/formation-availability/{state}`
- `POST /api/quote`
- `POST /api/orders`
- `POST /api/orders/{order_id}/authorize`
- `GET /api/order/{order_id}?token=...`
- `GET /api/admin/launch-readiness`
- `GET /api/admin/filing-jobs/{job_id}`
- `POST /api/admin/filing-jobs/{job_id}/automation`
- `POST /api/admin/filing-jobs/{job_id}/mark-submitted`
- `POST /api/admin/filing-jobs/{job_id}/mark-approved`

## Implementation Guidance

Prefer incremental, shippable changes:

1. Establish shared visual tokens in each static file or shared CSS if refactoring is safe.
2. Redesign homepage and wizard first.
3. Redesign dashboard second.
4. Redesign operator cockpit carefully without breaking controls.
5. Harmonize legal/support pages last.

This repo currently uses static HTML/CSS/JS files, not a compiled React app. Keep that unless you have a clear, tested migration plan. A full framework migration is not required for this redesign.

## Required Verification Before Handback

Run:

```bash
cd /Users/stevenswan/project-folders/sosfiler/app
.venv312/bin/python -m py_compile backend/server.py
.venv312/bin/python -m unittest discover -s qa -p 'test_*.py'
git diff --check
```

Also run Playwright/browser smoke checks for:

- `https://sosfiler.com/`
- `https://sosfiler.com/app.html`
- `https://sosfiler.com/dashboard.html`
- `https://ops.sosfiler.com/operator.html`

For local checks, use the local server if available. Verify:

- Pages render without console errors.
- Wizard state selection shows national availability.
- Checkout path still creates order authorization in test/smoke mode only.
- Dashboard empty/loading states are coherent.
- Operator cockpit still loads and renders the fulfillment packet.
- Mobile width around 390px is usable.
- Desktop width around 1440px is polished.

## Acceptance Criteria

The redesign is acceptable only when:

- The site clearly sells national entity formation, not only Texas.
- The wizard can still collect required data and reach checkout.
- Customer-facing pricing is transparent.
- Operator-assisted fulfillment is described as a professional lane with clear SLAs and evidence tracking.
- Operator cockpit shows exact instructions and customer filing data for each filing job.
- No PII/security regression is introduced.
- No API contract is broken.
- Full local tests pass.
- Playwright smoke checks show no console/page errors.

## Suggested Claude Prompt

Paste this to Claude:

> You are redesigning SOSFiler, a serious revenue-generating entity formation and post-formation management platform. Read `docs/claude_ui_ux_redesign_brief.md` and `docs/sosfiler_world_class_redesign.md` first, then inspect `frontend/index.html`, `frontend/app.html`, `frontend/dashboard.html`, `frontend/operator.html`, `frontend/chat-widget.css`, `frontend/chat-widget.js`, `frontend/privacy.html`, and `frontend/terms.html`.
>
> Redesign the UI/UX for national paid launch readiness. Do not make this Texas-first. The business model is $29 plus official state fees for formation and $29 plus official state fees for annual filings. SOSFiler charges no expedite fee and routes every order through the fastest compliant lane available. The user hates icons, so remove decorative icons and emoji and build a text-first enterprise interface. Preserve backend API contracts, Stripe manual-capture checkout, legal authorization language, PII redaction, operator/admin auth, and evidence-gated status behavior. The operator cockpit must make `job.operator_fulfillment_packet` the primary filing surface and show exact state-specific instructions plus customer filing data.
>
> Implement directly in the repo. Keep changes shippable and static-file-compatible unless a framework migration is explicitly justified and fully tested. After implementation, run the verification commands in the brief, perform browser/Playwright smoke checks on desktop and mobile, and report exactly what changed, what tests passed, and any remaining launch risks.
