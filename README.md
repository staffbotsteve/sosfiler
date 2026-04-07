# ⚡ SOSFiler — Business Formation Platform

**Your LLC. Filed in minutes. Not weeks. $49. Everything included.**

The anti-LegalZoom. Honest pricing, AI-powered document generation, automated state filing.

---

## What's Included for $49

- ✅ Articles of Organization (state-specific, filed with Secretary of State)
- ✅ EIN (Federal Tax ID) — filed with IRS
- ✅ Operating Agreement (single or multi-member, customized)
- ✅ Registered Agent (first year included)
- ✅ Compliance Calendar with email reminders
- ✅ Initial Resolutions, Meeting Minutes, Membership Certificates
- ✅ Real-time filing status dashboard
- ✅ Post-formation "What To Do Next" guide

### Ongoing (Optional)
- $49/yr — Registered Agent renewal (fixed, never increases)
- $25/yr — Annual report filing service
- No hidden fees. No upsells. No auto-renewals without explicit opt-in.

---

## Architecture

```
sosfiler/
├── frontend/           # Vanilla HTML/CSS/JS (no framework)
│   ├── index.html      # Landing page + state fee calculator
│   ├── app.html        # 7-step formation wizard
│   ├── dashboard.html  # Customer dashboard (status, docs, compliance)
│   ├── manifest.json   # PWA manifest
│   └── sw.js           # Service worker for offline/PWA
├── backend/
│   ├── server.py       # FastAPI server (all API endpoints)
│   ├── document_generator.py  # AI-powered doc generation (templates + GPT-4o)
│   ├── state_filing.py        # Playwright-based state filing automation
│   ├── ein_filing.py          # IRS EIN application automation
│   ├── compliance.py          # Compliance calendar engine
│   └── notifier.py            # SendGrid email notifications
├── data/
│   ├── state_fees.json        # Filing fees for all 50 states + DC
│   ├── state_requirements.json # State-specific requirements (top 10 detailed)
│   └── templates/             # Document templates (10 states + OA + resolutions)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI (Python 3.12) |
| Frontend | Vanilla HTML/CSS/JS (mobile-first, PWA) |
| Database | SQLite (WAL mode) |
| Payments | Stripe Checkout |
| Documents | ReportLab (PDF) + Markdown + OpenAI GPT-4o |
| Filing Automation | Playwright (headless Chromium) |
| Email | SendGrid (from s.swan@providence.aero) |
| Deployment | Docker / any VPS |

---

## Setup

### 1. Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Run

```bash
# Development
cd backend
uvicorn server:app --reload --port 8000

# Production (Docker)
docker-compose up -d
```

### 4. Access

- Landing page: http://localhost:8000/
- Formation wizard: http://localhost:8000/app.html
- Dashboard: http://localhost:8000/dashboard.html
- API docs: http://localhost:8000/docs

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/state-fees` | All state filing fees |
| GET | `/api/state-fees/{state}` | Fee details for a state |
| GET | `/api/name-check` | Business name availability check |
| POST | `/api/formation` | Create formation order |
| POST | `/api/checkout` | Create Stripe Checkout session |
| POST | `/api/webhooks/stripe` | Stripe webhook handler |
| GET | `/api/status/{order_id}` | Filing status + timeline |
| GET | `/api/documents/{order_id}` | List generated documents |
| GET | `/api/documents/{order_id}/download/{filename}` | Download a document |
| POST | `/api/ein` | Trigger EIN application |
| GET | `/api/compliance/{order_id}` | Compliance calendar |
| GET | `/api/health` | Health check |

All authenticated endpoints use `?token=<order_token>` for auth (email + order token model).

---

## Formation Pipeline

When a customer pays:

1. **Generate Documents** — Articles, Operating Agreement, Resolutions, Meeting Minutes, Certificates, SS-4 data
2. **File with State** — Playwright automation submits to state SOS portal (falls back to human review for CAPTCHA/errors)
3. **Apply for EIN** — Automates IRS online EIN application (Mon-Fri 7am-10pm ET only)
4. **Set Up Compliance** — Generates state-specific compliance calendar with deadlines
5. **Notify Customer** — Email at each step: confirmation, filed, approved, documents ready, EIN received

---

## State Coverage

### Full Automation (Top 10)
CA, TX, FL, NY, DE, WY, NV, WA, IL, GA

Each has:
- State-specific Articles of Organization template
- Filing portal automation (Playwright)
- State-specific compliance calendar
- Detailed requirements data

### All 50 States + DC
- Filing fees data
- Annual report schedules and fees
- Generic filing support (queued for human review)

---

## Document Generation

Templates use Handlebars-style `{{variable}}` syntax with `{{#if}}`, `{{#each}}` blocks. The generator:

1. Loads state-specific template
2. Fills template with wizard data
3. Optionally enhances with GPT-4o (fix unfilled placeholders, ensure state compliance)
4. Outputs as both Markdown and PDF (ReportLab)

---

## Deployment Checklist

- [ ] Set up Stripe account (live keys) + webhook endpoint
- [ ] Set up SendGrid verified sender (s.swan@providence.aero)
- [ ] Set up OpenAI API key
- [ ] Configure registered agent address for each state
- [ ] Set up SSL/HTTPS (Cloudflare, Let's Encrypt, etc.)
- [ ] Set up Playwright Chromium in production
- [ ] Create PWA icons (192x192 and 512x512)
- [ ] Configure Stripe webhook URL: `https://yourdomain.com/api/webhooks/stripe`
- [ ] Set up cron job for compliance reminder checks
- [ ] Test filing automation with each state portal

---

## License

Proprietary. © 2026 SOSFiler
