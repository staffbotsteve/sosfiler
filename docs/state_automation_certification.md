# State Automation Certification

SOSFiler national production requires one automation profile per state. Filing research tells us what can be filed; certification tells us whether SOSFiler can actually submit, pay, poll status, and retrieve documents at production quality.

## Matrix

The matrix is generated at:

```text
data/regulatory/state_automation_matrix.json
```

Generate it with:

```bash
python3 -m backend.regulatory.state_automation_matrix build
```

Execution adapters consume the matrix through:

```text
data/regulatory/state_adapter_manifest.json
```

Generate it with:

```bash
cd backend
python3 -m state_automation_profiles write
```

## Profile Fields

Each state profile includes:

- portal URLs
- account requirement
- master-account possibility
- human challenge status
- online LLC/corporation/nonprofit support
- online foreign qualification, amendments, annual reports
- payment method
- status-check method
- document-retrieval method
- automation lane
- production readiness
- blockers
- next action
- evidence URLs

## Automation Lanes

`playwright_needs_certification`: research found records and portal URLs, but live portal certification is still needed.

`playwright_persistent_browser`: Playwright can use a persistent browser profile on a trusted machine.

`playwright_persistent_browser_with_trusted_access_operator`: portal automation is expected to work only from a verified Trusted Access operator machine, with automation pausing for the protected step and resuming afterward.

`playwright_persistent_browser_with_operator_challenge_fallback`: portal can be automated after a human clears a challenge or login.

`operator_assisted_required`: automation can prepare and monitor, but a human must operate the protected portal step.

`mail_or_upload_packet`: online filing is not production-safe; generate packet and submit by official upload/email/mail.

`needs_certification`: not enough evidence yet.

## Production Readiness

`production_ready_operator_supervised`: ready to sell with automation plus operator supervision.

`research_ready_certification_needed`: research records exist, but portal certification remains.

`operator_assisted_required`: can be sold nationally if customer expectations and operator queue are clear.

`not_ready`: missing state filing records or portal path.

## Supabase

Supabase table:

```text
state_automation_profiles
```

The importer loads the matrix into Supabase:

```bash
python3 -m backend.regulatory.supabase_importer
```

## Certification Rule

No state is considered fully automated until we have observed:

1. Login/account path
2. Filing menu and supported filing types
3. Payment path
4. Status-check path
5. Document retrieval path
6. Human challenge behavior
7. Operator fallback path

Texas is the first proven state. North Carolina is classified as persistent-browser/operator-fallback because its online filing creation endpoint is protected by Cloudflare.

## National Production Rule

Research coverage and automation readiness are separate gates. A state may have verified filing records but still be blocked from automated production if its live portal has not been certified for submission, payment, status polling, and document retrieval.

National launch requires every state profile to move through these steps:

1. Verify official filing records and fees.
2. Browser-certify the portal entry and actual filing endpoint.
3. Certify payment path and accepted payment methods.
4. Certify status lookup or inbox/briefcase polling.
5. Certify produced-document retrieval.
6. Classify the state as automated, trusted-access operator-assisted, mail/upload packet, or not ready.
7. Store screenshots, URLs, and certification signals for audit.

Steven's Trusted Access for Cyber verification is an internal operator credential. It supports the trusted-access operator lane but does not remove the need to respect state portal controls, account rules, or human verification checkpoints.

## Execution Adapter Rule

New filing jobs must route through the state adapter manifest, not the older easy/medium/hard research labels alone.

The execution lanes are:

`browser_profile_automation`: use Playwright from a controlled SOSFiler browser profile. The portal has been observed, but live filing remains blocked until the review screen, payment screen, status polling, receipt capture, and document retrieval gates are certified.

`operator_assisted_browser_provider`: use the same automation path, but pause at a Trusted Access/operator checkpoint before resuming.

Texas uses `tx_sosdirect_v1`, the first production-ready operator-supervised adapter contract. Other states use the matrix-backed adapter until their state-specific selectors and payment/document capture are certified.

## Review-Screen Certification

After portal entry is observed, the next gate is review-screen certification. The worker is:

```bash
cd backend
python3 -m state_review_certifier status
python3 -m state_review_certifier run --limit 5
python3 -m state_review_certifier run --state CA --browser
```

The worker must never click final submission, pay fees, or authorize payment. Its job is to prove whether a notional filing can reach the review/payment-prep screen safely.

Gate outputs are stored in:

```text
data/regulatory/state_certification_gates.json
data/regulatory/review_certification_runs.json
data/regulatory/review_certification_screenshots/
```

Statuses:

`complete`: review/payment-prep screen was observed with evidence and no irreversible action.

`needs_state_script`: portal is reachable, but a state-specific Playwright script must be written to fill the form safely.

`trusted_access_required`: a protected checkpoint must be cleared from Steven's verified Trusted Access operator browser profile.

`login_required`: account login must be configured before review-screen certification can continue.

`blocked`: the runner could not safely reach or classify the review screen.
