#!/usr/bin/env python3
"""
SOSFiler 50-State LLC Formation Guide Generator

Reads state_requirements_v2.json and generates:
- Individual state guide pages: /frontend/states/llc-{slug}.html
- State index page: /frontend/states/index.html

Usage: python3 generate_state_guides.py
"""

import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_FILE = os.path.join(PROJECT_DIR, "data", "state_requirements_v2.json")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "frontend", "states")

# State abbreviation map
STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"
}

# Competitor pricing for cost comparison
COMPETITORS = {
    "LegalZoom": 149,
    "ZenBusiness": 0,     # but $199/yr after
    "Incfile": 0,          # but upsells
    "Northwest": 39,
    "Bizee": 0,            # but $199/yr
}


def slugify(name):
    """Convert state name to URL slug."""
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def extract_fee_number(fee_str):
    """Extract the primary numeric fee from a fee string like '$208 ($200 + $8 online)'."""
    # Try to find the first dollar amount
    match = re.search(r'\$([0-9,]+(?:\.\d{2})?)', fee_str)
    if match:
        return float(match.group(1).replace(',', ''))
    return 0


def escape_html(text):
    """Escape HTML special characters."""
    if not text:
        return ""
    return (str(text)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))


def format_fee_display(fee_val):
    """Format a fee number for display."""
    if fee_val == int(fee_val):
        return f"${int(fee_val)}"
    return f"${fee_val:.2f}"


def build_gotchas_section(state):
    """Build the gotchas/watch-out items list."""
    items = []

    if state.get("publication_required"):
        pub = state.get("publication_details", "Publication required after formation")
        items.append(f"📰 <strong>Publication Required:</strong> {escape_html(pub)}")

    if state.get("gotchas"):
        for g in state["gotchas"].split("+"):
            g = g.strip()
            if g and g.lower() not in ("publication",):
                items.append(f"⚠️ {escape_html(g)}")

    ar = state.get("annual_report", {})
    if ar.get("notes"):
        items.append(f"💰 {escape_html(ar['notes'])}")

    if ar.get("required") and ar.get("fee") and ar["fee"] >= 200:
        items.append(f"📅 High annual cost: {format_fee_display(ar['fee'])} {escape_html(ar.get('frequency', 'annual'))}")

    if not items:
        items.append("✅ No major gotchas — this is a straightforward state for LLC formation.")

    return items


def generate_state_page(state):
    """Generate a single state guide HTML page."""
    name = state["state_name"]
    slug = slugify(name)
    code = STATE_CODES.get(name, "")
    fee_str = state["filing_fee"]
    fee_num = extract_fee_number(fee_str)
    total = fee_num + 49
    ar = state.get("annual_report", {})
    gotchas = build_gotchas_section(state)

    # Build required fields list
    req_fields = state.get("required_fields", [])
    req_fields_html = "".join(f"<li>{escape_html(f)}</li>" for f in req_fields) if req_fields else "<li>Standard formation information</li>"

    # Expedited options
    exp = state.get("expedited_options", "")
    exp_display = escape_html(exp) if exp else "Not available"

    # Online filing
    online = "Yes ✅" if state.get("online_filing_available") else "No — Mail/In-Person Only"

    # Annual report section
    if ar.get("required"):
        ar_fee = format_fee_display(ar["fee"]) if ar.get("fee") else "Free"
        ar_due = escape_html(ar.get("due", "Varies"))
        ar_freq = escape_html(ar.get("frequency", "annual"))
        ar_display = f"{ar_fee} ({ar_freq}, due {ar_due})"
    else:
        ar_display = "None required ✅"

    # Payment methods
    payments = ", ".join(state.get("payment_methods", ["Credit Card"]))

    # Gotchas HTML
    gotchas_html = "".join(f'<div class="gotcha-item">{g}</div>' for g in gotchas)

    # Name requirements
    name_req = escape_html(state.get("name_requirements", "")) or "Standard LLC naming rules apply (must include \"LLC\" or \"Limited Liability Company\")"

    # RA requirements
    ra_req = escape_html(state.get("registered_agent_requirements", "")) or "Physical address in state required"

    # What state returns
    returns = escape_html(state.get("what_state_returns", "Approved formation documents"))

    # Publication details
    pub_section = ""
    if state.get("publication_required"):
        pub_section = f"""
        <div class="content-card pub-card">
            <h2>📰 Publication Requirement</h2>
            <p>{escape_html(state.get('publication_details', 'Publication in local newspaper required after formation.'))}</p>
            <p class="text-secondary">This is an additional cost beyond your filing fee. SOSFiler can help coordinate publication for you.</p>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>How to Form an LLC in {escape_html(name)} — $49 Flat | SOSFiler</title>
    <meta name="description" content="Form your {escape_html(name)} LLC fast. {escape_html(name)} filing fee: {fee_str}. SOSFiler charges just $49 flat — no upsells, no subscriptions. Here's everything you need to know.">
    <meta name="keywords" content="{escape_html(name)} LLC, form LLC in {escape_html(name)}, {escape_html(name)} LLC cost, {code} LLC formation, {escape_html(name)} articles of organization">
    <meta property="og:title" content="How to Form an LLC in {escape_html(name)} — $49 Flat | SOSFiler">
    <meta property="og:description" content="Form your {escape_html(name)} LLC for just $49 + {fee_str} state fee. Fast, transparent, no upsells.">
    <meta property="og:type" content="article">
    <meta property="og:url" content="https://sosfiler.com/states/llc-{slug}.html">
    <link rel="canonical" href="https://sosfiler.com/states/llc-{slug}.html">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #0A0A0A;
            --surface: #141414;
            --border: #1E1E1E;
            --text: #FFFFFF;
            --text-secondary: #999999;
            --accent: #CCFF00;
            --green: #39FF14;
            --red: #FF3366;
            --cyan: #00FFE5;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }}

        /* Nav */
        .nav {{
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(10, 10, 10, 0.95);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border);
            padding: 0 24px;
        }}
        .nav-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 64px;
        }}
        .nav-logo {{
            font-size: 1.25rem;
            font-weight: 800;
            color: var(--text);
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .nav-logo span {{ color: var(--accent); }}
        .nav-links {{
            display: flex;
            align-items: center;
            gap: 24px;
            list-style: none;
        }}
        .nav-links a {{
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.9rem;
            font-weight: 500;
            transition: color 0.2s;
        }}
        .nav-links a:hover {{ color: var(--text); }}
        .nav-cta {{
            background: var(--accent);
            color: #000;
            padding: 8px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.9rem;
            text-decoration: none;
            transition: opacity 0.2s;
        }}
        .nav-cta:hover {{ opacity: 0.9; }}

        /* Hero */
        .hero {{
            padding: 80px 24px 60px;
            text-align: center;
            background: linear-gradient(180deg, rgba(204, 255, 0, 0.03) 0%, transparent 60%);
        }}
        .hero-badge {{
            display: inline-block;
            background: rgba(204, 255, 0, 0.1);
            border: 1px solid rgba(204, 255, 0, 0.3);
            color: var(--accent);
            padding: 6px 16px;
            border-radius: 100px;
            font-size: 0.85rem;
            font-weight: 600;
            margin-bottom: 24px;
        }}
        .hero h1 {{
            font-size: clamp(2rem, 5vw, 3.5rem);
            font-weight: 800;
            line-height: 1.1;
            margin-bottom: 16px;
        }}
        .hero h1 .accent {{ color: var(--accent); }}
        .hero-sub {{
            font-size: 1.15rem;
            color: var(--text-secondary);
            max-width: 600px;
            margin: 0 auto 32px;
        }}
        .hero-fee {{
            display: inline-flex;
            align-items: center;
            gap: 12px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 28px;
            margin-bottom: 32px;
        }}
        .hero-fee .label {{ color: var(--text-secondary); font-size: 0.9rem; }}
        .hero-fee .amount {{ font-size: 2rem; font-weight: 800; color: var(--accent); }}
        .hero-fee .plus {{ color: var(--text-secondary); font-size: 1.2rem; }}
        .hero-fee .state-fee {{ font-size: 1.1rem; color: var(--text); }}

        /* Container */
        .container {{
            max-width: 900px;
            margin: 0 auto;
            padding: 0 24px;
        }}

        /* Content Cards */
        .content-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-top: 2px solid var(--accent);
            border-radius: 16px;
            padding: 32px;
            margin-bottom: 32px;
        }}
        .content-card h2 {{
            font-size: 1.4rem;
            font-weight: 700;
            margin-bottom: 20px;
        }}
        .content-card h3 {{
            font-size: 1.1rem;
            font-weight: 600;
            margin: 20px 0 12px;
            color: var(--accent);
        }}
        .content-card p {{
            color: var(--text-secondary);
            margin-bottom: 12px;
            line-height: 1.7;
        }}
        .content-card ul {{
            list-style: none;
            padding: 0;
        }}
        .content-card ul li {{
            padding: 8px 0;
            padding-left: 24px;
            position: relative;
            color: var(--text-secondary);
        }}
        .content-card ul li::before {{
            content: "→";
            position: absolute;
            left: 0;
            color: var(--accent);
            font-weight: 600;
        }}

        /* Quick Facts Grid */
        .facts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
        }}
        .fact-item {{
            background: rgba(204, 255, 0, 0.03);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
        }}
        .fact-label {{
            font-size: 0.8rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }}
        .fact-value {{
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text);
        }}
        .fact-value.highlight {{ color: var(--accent); }}

        /* Gotchas */
        .gotcha-item {{
            padding: 16px 20px;
            background: rgba(255, 51, 102, 0.05);
            border-left: 3px solid var(--red);
            border-radius: 0 8px 8px 0;
            margin-bottom: 12px;
            color: var(--text-secondary);
            font-size: 0.95rem;
            line-height: 1.6;
        }}
        .gotcha-item:only-child {{
            background: rgba(57, 255, 20, 0.05);
            border-left-color: var(--green);
        }}

        /* Cost Comparison */
        .cost-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
        }}
        .cost-table th, .cost-table td {{
            padding: 14px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            font-size: 0.95rem;
        }}
        .cost-table th {{
            color: var(--text-secondary);
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .cost-table .sos-row {{
            background: rgba(204, 255, 0, 0.05);
        }}
        .cost-table .sos-row td {{
            color: var(--accent);
            font-weight: 700;
        }}
        .cost-table .total {{ font-weight: 700; }}
        .cost-table .fine-print {{
            font-size: 0.8rem;
            color: var(--text-secondary);
            font-weight: 400;
        }}

        /* True Cost Calculator */
        .calc-box {{
            background: rgba(204, 255, 0, 0.05);
            border: 1px solid rgba(204, 255, 0, 0.2);
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            margin-top: 20px;
        }}
        .calc-box .calc-label {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }}
        .calc-box .calc-total {{
            font-size: 2.5rem;
            font-weight: 800;
            color: var(--accent);
        }}
        .calc-box .calc-breakdown {{
            font-size: 0.9rem;
            color: var(--text-secondary);
            margin-top: 8px;
        }}

        /* CTA Section */
        .cta-section {{
            text-align: center;
            padding: 60px 24px;
            margin: 40px 0;
        }}
        .cta-section h2 {{
            font-size: 2rem;
            font-weight: 800;
            margin-bottom: 16px;
        }}
        .cta-section p {{
            color: var(--text-secondary);
            margin-bottom: 32px;
            font-size: 1.1rem;
        }}
        .cta-btn {{
            display: inline-block;
            background: var(--accent);
            color: #000;
            padding: 16px 40px;
            border-radius: 12px;
            font-size: 1.1rem;
            font-weight: 700;
            text-decoration: none;
            transition: all 0.2s;
            box-shadow: 0 0 30px rgba(204, 255, 0, 0.2);
        }}
        .cta-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 0 40px rgba(204, 255, 0, 0.3);
        }}
        .cta-sub {{
            margin-top: 16px;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }}
        .cta-sub a {{
            color: var(--cyan);
            text-decoration: none;
        }}
        .cta-sub a:hover {{ text-decoration: underline; }}

        /* DIY Section */
        .diy-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            margin-top: 20px;
        }}
        .diy-col {{
            padding: 24px;
            border-radius: 12px;
        }}
        .diy-col.diy-self {{
            background: rgba(255, 51, 102, 0.05);
            border: 1px solid rgba(255, 51, 102, 0.2);
        }}
        .diy-col.diy-sos {{
            background: rgba(204, 255, 0, 0.05);
            border: 1px solid rgba(204, 255, 0, 0.2);
        }}
        .diy-col h3 {{
            font-size: 1rem;
            font-weight: 700;
            margin-bottom: 16px;
        }}
        .diy-col.diy-self h3 {{ color: var(--red); }}
        .diy-col.diy-sos h3 {{ color: var(--accent); }}
        .diy-col ul {{
            list-style: none;
            padding: 0;
        }}
        .diy-col ul li {{
            padding: 6px 0;
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}
        .diy-col.diy-self ul li::before {{
            content: "✗ ";
            color: var(--red);
        }}
        .diy-col.diy-sos ul li::before {{
            content: "✓ ";
            color: var(--green);
        }}

        /* SOS Portal Link */
        .portal-link {{
            display: block;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 32px;
            color: var(--text-secondary);
            font-size: 0.95rem;
        }}
        .portal-link a {{
            color: var(--cyan);
            text-decoration: none;
        }}
        .portal-link a:hover {{ text-decoration: underline; }}

        /* Disclaimer */
        .disclaimer {{
            text-align: center;
            padding: 40px 24px;
            color: var(--text-secondary);
            font-size: 0.8rem;
            border-top: 1px solid var(--border);
            margin-top: 40px;
        }}

        /* Footer */
        .footer {{
            background: var(--surface);
            border-top: 1px solid var(--border);
            padding: 48px 24px 32px;
        }}
        .footer-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: 2fr 1fr 1fr 1fr;
            gap: 32px;
        }}
        .footer-brand {{
            font-size: 1.1rem;
            font-weight: 700;
            margin-bottom: 12px;
        }}
        .footer-brand span {{ color: var(--accent); }}
        .footer-desc {{
            color: var(--text-secondary);
            font-size: 0.85rem;
            line-height: 1.6;
        }}
        .footer-col h4 {{
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            margin-bottom: 16px;
        }}
        .footer-col a {{
            display: block;
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.9rem;
            padding: 4px 0;
            transition: color 0.2s;
        }}
        .footer-col a:hover {{ color: var(--text); }}
        .footer-bottom {{
            max-width: 1200px;
            margin: 32px auto 0;
            padding-top: 24px;
            border-top: 1px solid var(--border);
            text-align: center;
            color: var(--text-secondary);
            font-size: 0.8rem;
        }}

        /* Responsive */
        @media (max-width: 768px) {{
            .facts-grid {{ grid-template-columns: 1fr 1fr; }}
            .diy-grid {{ grid-template-columns: 1fr; }}
            .footer-inner {{ grid-template-columns: 1fr 1fr; }}
            .nav-links {{ display: none; }}
        }}
        @media (max-width: 480px) {{
            .facts-grid {{ grid-template-columns: 1fr; }}
            .hero-fee {{ flex-direction: column; gap: 4px; padding: 16px; }}
        }}

        /* Schema.org hidden */
        .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); border: 0; }}
    </style>
</head>
<body>
    <!-- Structured Data -->
    <script type="application/ld+json">
    {{
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": "How to Form an LLC in {escape_html(name)}",
        "description": "Complete guide to forming an LLC in {escape_html(name)}. Filing fee: {fee_str}. SOSFiler files for $49 flat.",
        "author": {{
            "@type": "Organization",
            "name": "SOSFiler",
            "url": "https://sosfiler.com"
        }},
        "publisher": {{
            "@type": "Organization",
            "name": "SOSFiler",
            "url": "https://sosfiler.com"
        }}
    }}
    </script>

    <!-- Nav -->
    <nav class="nav">
        <div class="nav-inner">
            <a href="/" class="nav-logo">📋 SOS<span>Filer</span></a>
            <ul class="nav-links">
                <li><a href="/#how-it-works">How It Works</a></li>
                <li><a href="/#pricing">Pricing</a></li>
                <li><a href="/states/">All States</a></li>
                <li><a href="/app.html" class="nav-cta">Start Filing →</a></li>
            </ul>
        </div>
    </nav>

    <!-- Hero -->
    <section class="hero">
        <div class="hero-badge">{code} LLC Formation Guide</div>
        <h1>Form an LLC in <span class="accent">{escape_html(name)}</span></h1>
        <p class="hero-sub">Here's exactly what you need to know about forming an LLC in {escape_html(name)}. No fluff.</p>
        <div class="hero-fee">
            <div>
                <div class="label">SOSFiler</div>
                <div class="amount">$49</div>
            </div>
            <div class="plus">+</div>
            <div>
                <div class="label">State Fee</div>
                <div class="state-fee">{escape_html(fee_str)}</div>
            </div>
        </div>
    </section>

    <div class="container">

        <!-- Quick Facts -->
        <div class="content-card">
            <h2>⚡ Quick Facts</h2>
            <div class="facts-grid">
                <div class="fact-item">
                    <div class="fact-label">Filing Fee</div>
                    <div class="fact-value highlight">{escape_html(fee_str)}</div>
                </div>
                <div class="fact-item">
                    <div class="fact-label">Processing Time</div>
                    <div class="fact-value">{escape_html(state.get('standard_processing_time', 'Varies'))}</div>
                </div>
                <div class="fact-item">
                    <div class="fact-label">Online Filing</div>
                    <div class="fact-value">{online}</div>
                </div>
                <div class="fact-item">
                    <div class="fact-label">Expedited Options</div>
                    <div class="fact-value">{exp_display}</div>
                </div>
                <div class="fact-item">
                    <div class="fact-label">Annual Report</div>
                    <div class="fact-value">{ar_display}</div>
                </div>
                <div class="fact-item">
                    <div class="fact-label">Form</div>
                    <div class="fact-value">{escape_html(state.get('form_name_number', 'Articles of Organization'))}</div>
                </div>
            </div>
        </div>

        <!-- Requirements -->
        <div class="content-card">
            <h2>📋 What You Need to File</h2>

            <h3>Required Information</h3>
            <ul>{req_fields_html}</ul>

            <h3>Name Requirements</h3>
            <p>{name_req}</p>

            <h3>Registered Agent</h3>
            <p>{ra_req}</p>
            <p>Don't have a registered agent? SOSFiler offers registered agent service for $49/year.</p>

            <h3>What {escape_html(name)} Returns</h3>
            <p>{returns}</p>
        </div>

        <!-- Gotchas -->
        <div class="content-card">
            <h2>🚨 Watch Out</h2>
            <p>State-specific things that trip people up:</p>
            {gotchas_html}
        </div>
        {pub_section}
        <!-- DIY vs SOSFiler -->
        <div class="content-card">
            <h2>🤔 DIY vs SOSFiler</h2>
            <p>You can absolutely file yourself. Here's what that looks like:</p>
            <div class="diy-grid">
                <div class="diy-col diy-self">
                    <h3>Do It Yourself</h3>
                    <ul>
                        <li>Research {escape_html(name)} requirements</li>
                        <li>Navigate the SOS portal</li>
                        <li>Fill out {escape_html(state.get('form_name_number', 'formation forms'))}</li>
                        <li>Hope you didn't miss anything</li>
                        <li>Track your filing status</li>
                        <li>Figure out next steps (EIN, Operating Agreement, etc.)</li>
                        <li>2-4 hours of your time</li>
                    </ul>
                </div>
                <div class="diy-col diy-sos">
                    <h3>SOSFiler — $49</h3>
                    <ul>
                        <li>Answer a few questions (5 minutes)</li>
                        <li>We handle {escape_html(name)} filing</li>
                        <li>We prepare your Operating Agreement</li>
                        <li>We file for your EIN</li>
                        <li>We track everything for you</li>
                        <li>We deliver all docs when ready</li>
                        <li>No subscriptions, no upsells</li>
                    </ul>
                </div>
            </div>
        </div>

        <!-- Cost Comparison -->
        <div class="content-card">
            <h2>💸 True Cost Comparison</h2>
            <p>What you'll actually pay to form an LLC in {escape_html(name)}:</p>
            <table class="cost-table">
                <thead>
                    <tr>
                        <th>Service</th>
                        <th>Service Fee</th>
                        <th>State Fee</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                    <tr class="sos-row">
                        <td>SOSFiler</td>
                        <td>$49</td>
                        <td>{escape_html(fee_str)}</td>
                        <td class="total">{format_fee_display(total)}</td>
                    </tr>
                    <tr>
                        <td>LegalZoom</td>
                        <td>$149+</td>
                        <td>{escape_html(fee_str)}</td>
                        <td class="total">{format_fee_display(fee_num + 149)}+ <span class="fine-print">(+ upsells)</span></td>
                    </tr>
                    <tr>
                        <td>ZenBusiness</td>
                        <td>"Free"</td>
                        <td>{escape_html(fee_str)}</td>
                        <td class="total">{format_fee_display(fee_num)}* <span class="fine-print">(*$199/yr after)</span></td>
                    </tr>
                    <tr>
                        <td>Northwest</td>
                        <td>$39</td>
                        <td>{escape_html(fee_str)}</td>
                        <td class="total">{format_fee_display(fee_num + 39)} <span class="fine-print">(+ $125/yr RA)</span></td>
                    </tr>
                    <tr>
                        <td>DIY</td>
                        <td>Free</td>
                        <td>{escape_html(fee_str)}</td>
                        <td class="total">{format_fee_display(fee_num)} <span class="fine-print">(+ your time)</span></td>
                    </tr>
                </tbody>
            </table>

            <div class="calc-box">
                <div class="calc-label">Your Total with SOSFiler</div>
                <div class="calc-total">{format_fee_display(total)}</div>
                <div class="calc-breakdown">$49 SOSFiler + {escape_html(fee_str)} state fee — that's it. No subscriptions. No hidden fees.</div>
            </div>
        </div>

        <!-- SOS Portal Link -->
        <div class="portal-link">
            🏛️ <strong>Want to do it yourself?</strong> Here's the official {escape_html(name)} Secretary of State portal:
            <a href="{escape_html(state.get('sos_portal_url', '#'))}" target="_blank" rel="noopener">{escape_html(state.get('sos_portal_url', ''))}</a>
        </div>

    </div>

    <!-- CTA -->
    <section class="cta-section">
        <h2>Ready to form your {escape_html(name)} LLC?</h2>
        <p>$49 flat fee. No subscriptions. No upsells. Just filing.</p>
        <a href="/app.html?state={code}" class="cta-btn">Form My {escape_html(name)} LLC for $49 + {format_fee_display(fee_num)} State Fee →</a>
        <div class="cta-sub">
            Payment methods accepted by {escape_html(name)}: {escape_html(payments)}<br>
            <a href="/states/">Browse all state guides →</a>
        </div>
    </section>

    <!-- Disclaimer -->
    <div class="disclaimer">
        <p>I'm a document preparation service, not a law firm. This isn't legal advice.</p>
        <p>Filing fees and requirements are subject to change. Last verified 2026. Always confirm with your state's Secretary of State.</p>
    </div>

    <!-- Footer -->
    <footer class="footer">
        <div class="footer-inner">
            <div>
                <div class="footer-brand">📋 SOS<span>Filer</span></div>
                <p class="footer-desc">Business formation for $49 flat. No subscriptions, no upsells, no nonsense. Just fast, honest filing.</p>
            </div>
            <div class="footer-col">
                <h4>Services</h4>
                <a href="/app.html">Form an LLC</a>
                <a href="/app.html">Form a Corporation</a>
                <a href="/states/">State Guides</a>
            </div>
            <div class="footer-col">
                <h4>Resources</h4>
                <a href="/states/">All 50 States</a>
                <a href="/#pricing">Pricing</a>
                <a href="/#how-it-works">How It Works</a>
            </div>
            <div class="footer-col">
                <h4>Company</h4>
                <a href="mailto:support@sosfiler.com">Contact</a>
                <a href="/#faq">FAQ</a>
            </div>
        </div>
        <div class="footer-bottom">
            © 2026 SOSFiler. A SwanBill company. All rights reserved.
        </div>
    </footer>

</body>
</html>"""

    return html


def generate_index_page(states):
    """Generate the state index page with grid of all states."""
    # Sort states by name
    sorted_states = sorted(states, key=lambda s: s["state_name"])

    state_cards = ""
    for state in sorted_states:
        name = state["state_name"]
        slug = slugify(name)
        code = STATE_CODES.get(name, "")
        fee_str = state["filing_fee"]
        fee_num = extract_fee_number(fee_str)
        online = "✅ Online" if state.get("online_filing_available") else "📬 Mail Only"
        processing = escape_html(state.get("standard_processing_time", "Varies"))

        state_cards += f"""
            <a href="llc-{slug}.html" class="state-card">
                <div class="state-code">{code}</div>
                <div class="state-name">{escape_html(name)}</div>
                <div class="state-fee">{escape_html(fee_str)}</div>
                <div class="state-meta">
                    <span>{online}</span>
                    <span>{processing}</span>
                </div>
            </a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LLC Formation Guides — All 50 States + DC | SOSFiler</title>
    <meta name="description" content="Form an LLC in any state for $49 flat. Compare filing fees, processing times, and requirements for all 50 states plus DC.">
    <meta name="keywords" content="LLC formation, form LLC, state LLC guide, LLC filing fees, business formation all states">
    <meta property="og:title" content="LLC Formation Guides — All 50 States + DC | SOSFiler">
    <meta property="og:description" content="Compare LLC filing fees, processing times, and requirements for all 50 states. SOSFiler: $49 flat.">
    <link rel="canonical" href="https://sosfiler.com/states/">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #0A0A0A;
            --surface: #141414;
            --border: #1E1E1E;
            --text: #FFFFFF;
            --text-secondary: #999999;
            --accent: #CCFF00;
            --green: #39FF14;
            --red: #FF3366;
            --cyan: #00FFE5;
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }}

        .nav {{
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(10, 10, 10, 0.95);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border);
            padding: 0 24px;
        }}
        .nav-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            height: 64px;
        }}
        .nav-logo {{
            font-size: 1.25rem;
            font-weight: 800;
            color: var(--text);
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .nav-logo span {{ color: var(--accent); }}
        .nav-links {{
            display: flex;
            align-items: center;
            gap: 24px;
            list-style: none;
        }}
        .nav-links a {{
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.9rem;
            font-weight: 500;
            transition: color 0.2s;
        }}
        .nav-links a:hover {{ color: var(--text); }}
        .nav-cta {{
            background: var(--accent);
            color: #000;
            padding: 8px 20px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.9rem;
            text-decoration: none;
            transition: opacity 0.2s;
        }}
        .nav-cta:hover {{ opacity: 0.9; }}

        .hero {{
            padding: 80px 24px 60px;
            text-align: center;
            background: linear-gradient(180deg, rgba(204, 255, 0, 0.03) 0%, transparent 60%);
        }}
        .hero h1 {{
            font-size: clamp(2rem, 5vw, 3rem);
            font-weight: 800;
            margin-bottom: 16px;
        }}
        .hero h1 .accent {{ color: var(--accent); }}
        .hero-sub {{
            font-size: 1.1rem;
            color: var(--text-secondary);
            max-width: 600px;
            margin: 0 auto 32px;
        }}
        .hero-stats {{
            display: flex;
            justify-content: center;
            gap: 48px;
            margin-top: 32px;
        }}
        .hero-stat .num {{
            font-size: 2rem;
            font-weight: 800;
            color: var(--accent);
        }}
        .hero-stat .label {{
            font-size: 0.85rem;
            color: var(--text-secondary);
        }}

        /* Search/Filter */
        .search-bar {{
            max-width: 500px;
            margin: -20px auto 40px;
            position: relative;
        }}
        .search-bar input {{
            width: 100%;
            padding: 14px 20px 14px 48px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            color: var(--text);
            font-size: 1rem;
            font-family: inherit;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-bar input:focus {{
            border-color: var(--accent);
        }}
        .search-bar input::placeholder {{
            color: var(--text-secondary);
        }}
        .search-bar::before {{
            content: "🔍";
            position: absolute;
            left: 16px;
            top: 50%;
            transform: translateY(-50%);
            font-size: 1.1rem;
        }}

        /* State Grid */
        .states-grid {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 24px 80px;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 16px;
        }}
        .state-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-top: 2px solid var(--accent);
            border-radius: 16px;
            padding: 24px;
            text-decoration: none;
            transition: all 0.2s;
            display: block;
        }}
        .state-card:hover {{
            border-color: var(--accent);
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(204, 255, 0, 0.1);
        }}
        .state-code {{
            font-size: 0.8rem;
            font-weight: 700;
            color: var(--accent);
            letter-spacing: 0.1em;
            margin-bottom: 4px;
        }}
        .state-name {{
            font-size: 1.15rem;
            font-weight: 700;
            color: var(--text);
            margin-bottom: 8px;
        }}
        .state-fee {{
            font-size: 1rem;
            color: var(--accent);
            font-weight: 600;
            margin-bottom: 12px;
        }}
        .state-meta {{
            display: flex;
            gap: 12px;
            font-size: 0.8rem;
            color: var(--text-secondary);
        }}

        .no-results {{
            text-align: center;
            padding: 60px 24px;
            color: var(--text-secondary);
            font-size: 1.1rem;
            display: none;
            grid-column: 1 / -1;
        }}

        /* Footer */
        .footer {{
            background: var(--surface);
            border-top: 1px solid var(--border);
            padding: 48px 24px 32px;
        }}
        .footer-inner {{
            max-width: 1200px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: 2fr 1fr 1fr 1fr;
            gap: 32px;
        }}
        .footer-brand {{
            font-size: 1.1rem;
            font-weight: 700;
            margin-bottom: 12px;
        }}
        .footer-brand span {{ color: var(--accent); }}
        .footer-desc {{
            color: var(--text-secondary);
            font-size: 0.85rem;
            line-height: 1.6;
        }}
        .footer-col h4 {{
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            margin-bottom: 16px;
        }}
        .footer-col a {{
            display: block;
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.9rem;
            padding: 4px 0;
            transition: color 0.2s;
        }}
        .footer-col a:hover {{ color: var(--text); }}
        .footer-bottom {{
            max-width: 1200px;
            margin: 32px auto 0;
            padding-top: 24px;
            border-top: 1px solid var(--border);
            text-align: center;
            color: var(--text-secondary);
            font-size: 0.8rem;
        }}

        .disclaimer {{
            text-align: center;
            padding: 24px;
            color: var(--text-secondary);
            font-size: 0.8rem;
        }}

        @media (max-width: 768px) {{
            .hero-stats {{ gap: 24px; }}
            .footer-inner {{ grid-template-columns: 1fr 1fr; }}
            .states-grid {{ grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); }}
        }}
    </style>
</head>
<body>

    <!-- Nav -->
    <nav class="nav">
        <div class="nav-inner">
            <a href="/" class="nav-logo">📋 SOS<span>Filer</span></a>
            <ul class="nav-links">
                <li><a href="/#how-it-works">How It Works</a></li>
                <li><a href="/#pricing">Pricing</a></li>
                <li><a href="/states/">All States</a></li>
                <li><a href="/app.html" class="nav-cta">Start Filing →</a></li>
            </ul>
        </div>
    </nav>

    <!-- Hero -->
    <section class="hero">
        <h1>LLC Formation Guides — <span class="accent">All 50 States + DC</span></h1>
        <p class="hero-sub">Everything you need to know about forming an LLC in any state. Filing fees, processing times, requirements, and gotchas — all in one place.</p>
        <div class="hero-stats">
            <div class="hero-stat">
                <div class="num">51</div>
                <div class="label">State Guides</div>
            </div>
            <div class="hero-stat">
                <div class="num">$49</div>
                <div class="label">Flat Fee</div>
            </div>
            <div class="hero-stat">
                <div class="num">$0</div>
                <div class="label">Hidden Fees</div>
            </div>
        </div>
    </section>

    <!-- Search -->
    <div class="search-bar">
        <input type="text" id="stateSearch" placeholder="Search states..." oninput="filterStates()">
    </div>

    <!-- State Grid -->
    <div class="states-grid" id="statesGrid">
        {state_cards}
        <div class="no-results" id="noResults">No states match your search.</div>
    </div>

    <!-- Disclaimer -->
    <div class="disclaimer">
        <p>I'm a document preparation service, not a law firm. This isn't legal advice.</p>
        <p>Filing fees and requirements are subject to change. Last verified 2026.</p>
    </div>

    <!-- Footer -->
    <footer class="footer">
        <div class="footer-inner">
            <div>
                <div class="footer-brand">📋 SOS<span>Filer</span></div>
                <p class="footer-desc">Business formation for $49 flat. No subscriptions, no upsells, no nonsense.</p>
            </div>
            <div class="footer-col">
                <h4>Services</h4>
                <a href="/app.html">Form an LLC</a>
                <a href="/app.html">Form a Corporation</a>
                <a href="/states/">State Guides</a>
            </div>
            <div class="footer-col">
                <h4>Resources</h4>
                <a href="/states/">All 50 States</a>
                <a href="/#pricing">Pricing</a>
                <a href="/#how-it-works">How It Works</a>
            </div>
            <div class="footer-col">
                <h4>Company</h4>
                <a href="mailto:support@sosfiler.com">Contact</a>
                <a href="/#faq">FAQ</a>
            </div>
        </div>
        <div class="footer-bottom">
            © 2026 SOSFiler. A SwanBill company. All rights reserved.
        </div>
    </footer>

    <script>
    function filterStates() {{
        const q = document.getElementById('stateSearch').value.toLowerCase();
        const cards = document.querySelectorAll('.state-card');
        let visible = 0;
        cards.forEach(card => {{
            const name = card.querySelector('.state-name').textContent.toLowerCase();
            const code = card.querySelector('.state-code').textContent.toLowerCase();
            const match = name.includes(q) || code.includes(q);
            card.style.display = match ? '' : 'none';
            if (match) visible++;
        }});
        document.getElementById('noResults').style.display = visible === 0 ? 'block' : 'none';
    }}
    </script>

</body>
</html>"""

    return html


def main():
    """Generate all state guide pages and index."""
    # Load data
    print(f"Loading data from {DATA_FILE}...")
    with open(DATA_FILE, 'r') as f:
        states = json.load(f)

    print(f"Found {len(states)} states/territories")

    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Generate individual state pages
    for state in states:
        name = state["state_name"]
        slug = slugify(name)
        filename = f"llc-{slug}.html"
        filepath = os.path.join(OUTPUT_DIR, filename)

        html = generate_state_page(state)
        with open(filepath, 'w') as f:
            f.write(html)
        print(f"  ✓ {filename}")

    # Generate index page
    index_html = generate_index_page(states)
    index_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(index_path, 'w') as f:
        f.write(index_html)
    print(f"  ✓ index.html")

    print(f"\nDone! Generated {len(states)} state guides + 1 index page in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
