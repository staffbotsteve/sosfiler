#!/usr/bin/env python3
"""
SOSFiler QA Test Suite
Run: python3 test_suite.py [--tag auth] [--tag api] [--tag frontend] [--tag all]
Outputs structured JSON results to stdout.
"""

import json
import sys
import time
import urllib.request
import urllib.error
import ssl
import subprocess
import argparse

BASE_URL = "https://sosfiler.com"
RESULTS = []
ssl_ctx = ssl.create_default_context()


def test(name, tag, severity="P2"):
    """Decorator to register a test."""
    def decorator(fn):
        fn._test_name = name
        fn._test_tag = tag
        fn._test_severity = severity
        return fn
    return decorator


def run_test(fn):
    """Execute a test function, capture result."""
    start = time.time()
    try:
        result = fn()
        elapsed = round((time.time() - start) * 1000)
        if result is True or result is None:
            return {"name": fn._test_name, "tag": fn._test_tag, "status": "PASS", "ms": elapsed, "severity": fn._test_severity}
        elif isinstance(result, str):
            return {"name": fn._test_name, "tag": fn._test_tag, "status": "FAIL", "ms": elapsed, "reason": result, "severity": fn._test_severity}
        else:
            return {"name": fn._test_name, "tag": fn._test_tag, "status": "FAIL", "ms": elapsed, "reason": str(result), "severity": fn._test_severity}
    except Exception as e:
        elapsed = round((time.time() - start) * 1000)
        return {"name": fn._test_name, "tag": fn._test_tag, "status": "FAIL", "ms": elapsed, "reason": str(e), "severity": fn._test_severity}


def fetch(path, method="GET", body=None, headers=None, expect_status=None):
    """Helper to make HTTP requests."""
    url = BASE_URL + path if path.startswith("/") else path
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            status = resp.status
            resp_body = resp.read().decode()
            resp_headers = dict(resp.headers)
            try:
                resp_json = json.loads(resp_body)
            except:
                resp_json = None
            if expect_status and status != expect_status:
                return None, status, resp_body, resp_headers
            return resp_json, status, resp_body, resp_headers
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        try:
            resp_json = json.loads(body_text)
        except:
            resp_json = None
        return resp_json, e.code, body_text, dict(e.headers) if e.headers else {}


# ─── HEALTH & INFRASTRUCTURE ────────────────────────────────────────────

@test("Site responds", "infra", "P0")
def test_site_up():
    _, status, _, _ = fetch("/")
    if status != 200:
        return f"Expected 200, got {status}"

@test("API health endpoint", "infra", "P0")
def test_api_health():
    data, status, _, _ = fetch("/api/health")
    if status != 200:
        return f"Expected 200, got {status}"

@test("Security headers present", "infra", "P1")
def test_security_headers():
    _, status, _, headers = fetch("/")
    missing = []
    for h in ["X-Frame-Options", "X-Content-Type-Options", "Content-Security-Policy"]:
        if h.lower() not in {k.lower(): v for k, v in headers.items()}:
            missing.append(h)
    if missing:
        return f"Missing headers: {', '.join(missing)}"

@test("App wizard loads", "frontend", "P0")
def test_wizard_loads():
    _, status, body, _ = fetch("/app.html")
    if status != 200:
        return f"Expected 200, got {status}"
    if "SOSFiler" not in body and "sosfiler" not in body.lower():
        return "Page doesn't contain SOSFiler branding"

@test("Dashboard loads", "frontend", "P0")
def test_dashboard_loads():
    _, status, _, _ = fetch("/dashboard.html")
    if status != 200:
        return f"Expected 200, got {status}"


# ─── AUTH TESTS ──────────────────────────────────────────────────────────

@test("Email signup", "auth", "P0")
def test_email_signup():
    email = f"qatest_{int(time.time())}@test.sosfiler.com"
    data, status, _, _ = fetch("/api/auth/signup", "POST", {
        "email": email, "password": "TestPass123!", "name": "QA Tester"
    })
    if status != 200:
        return f"Signup returned {status}"
    if not data or not data.get("token"):
        return "No token returned"

@test("Email login", "auth", "P0")
def test_email_login():
    # First signup, then login
    email = f"qalogin_{int(time.time())}@test.sosfiler.com"
    fetch("/api/auth/signup", "POST", {"email": email, "password": "TestPass123!", "name": "QA"})
    data, status, _, _ = fetch("/api/auth/login", "POST", {"email": email, "password": "TestPass123!"})
    if status != 200:
        return f"Login returned {status}"
    if not data or not data.get("token"):
        return "No token returned"

@test("Login with wrong password", "auth", "P1")
def test_wrong_password():
    email = f"qawrong_{int(time.time())}@test.sosfiler.com"
    fetch("/api/auth/signup", "POST", {"email": email, "password": "TestPass123!", "name": "QA"})
    _, status, _, _ = fetch("/api/auth/login", "POST", {"email": email, "password": "WrongPass"})
    if status != 401:
        return f"Expected 401, got {status}"

@test("Duplicate signup rejected", "auth", "P1")
def test_duplicate_signup():
    email = f"qadup_{int(time.time())}@test.sosfiler.com"
    fetch("/api/auth/signup", "POST", {"email": email, "password": "TestPass123!", "name": "QA"})
    _, status, _, _ = fetch("/api/auth/signup", "POST", {"email": email, "password": "TestPass123!", "name": "QA"})
    if status != 400:
        return f"Expected 400, got {status}"

@test("Auth me with valid token", "auth", "P0")
def test_auth_me():
    email = f"qame_{int(time.time())}@test.sosfiler.com"
    data, _, _, _ = fetch("/api/auth/signup", "POST", {"email": email, "password": "TestPass123!", "name": "QA Me"})
    token = data["token"]
    me_data, status, _, _ = fetch("/api/auth/me", "GET", headers={"Authorization": f"Bearer {token}"})
    if status != 200:
        return f"Expected 200, got {status}"
    # API returns {"user": {"email": ...}} or {"email": ...} — handle both
    actual_email = me_data.get("email") or (me_data.get("user", {}).get("email") if isinstance(me_data.get("user"), dict) else None)
    if actual_email != email:
        return f"Email mismatch: expected {email}, got {me_data}"

@test("Auth me with invalid token", "auth", "P1")
def test_auth_me_invalid():
    _, status, _, _ = fetch("/api/auth/me", "GET", headers={"Authorization": "Bearer fake.token.here"})
    if status != 401:
        return f"Expected 401, got {status}"

@test("Google OAuth endpoint accepts model", "auth", "P1")
def test_google_oauth_model():
    """Verify the backend accepts the expanded AuthOAuthRequest fields without crashing."""
    data, status, _, _ = fetch("/api/auth/google", "POST", {
        "token": "fake_token_for_model_test",
        "email": "test@gmail.com",
        "name": "Test User",
        "provider_id": "12345"
    })
    # Should fail auth (bad token) but NOT crash with 500/422
    if status == 500:
        return "Server error — model likely doesn't accept extra fields"
    if status == 422:
        return "Validation error — model rejects extra fields"
    # 400 = expected (bad token, but model accepted the fields)

@test("Apple OAuth endpoint exists", "auth", "P1")
def test_apple_oauth_endpoint():
    data, status, _, _ = fetch("/api/auth/apple", "POST", {"token": "fake"})
    if status == 404:
        return "Apple auth endpoint missing"
    # 400 = expected (bad token)

@test("Facebook OAuth endpoint exists", "auth", "P1")
def test_facebook_oauth_endpoint():
    data, status, _, _ = fetch("/api/auth/facebook", "POST", {"token": "fake"})
    if status == 404:
        return "Facebook auth endpoint missing"


# ─── FRONTEND AUTH FLOW TESTS (static analysis) ─────────────────────────

@test("Wizard has setIfPresent guard", "auth", "P0")
def test_setifpresent_in_wizard():
    """The collectStepData function should use setIfPresent to prevent empty DOM clobbering."""
    _, _, body, _ = fetch("/app.html")
    if "setIfPresent" not in body:
        return "collectStepData missing setIfPresent guard — form data will be lost on OAuth redirect"

@test("Wizard has mobile Google redirect", "auth", "P1")
def test_mobile_redirect():
    """Mobile users should get a full redirect, not a popup."""
    _, _, body, _ = fetch("/app.html")
    if "isMobile" not in body:
        return "triggerGoogleAuth missing mobile detection — popup will fail on mobile"

@test("onAuthSuccess restores from localStorage", "auth", "P0")
def test_auth_success_restores():
    """onAuthSuccess must restore formData from localStorage before navigating."""
    _, _, body, _ = fetch("/app.html")
    if "sosfiler_progress" not in body.split("onAuthSuccess")[1].split("goToStep")[0] if "onAuthSuccess" in body else "":
        return "onAuthSuccess doesn't restore from localStorage before goToStep"

@test("Apple auth not just an alert stub", "auth", "P2")
def test_apple_not_stub():
    _, _, body, _ = fetch("/app.html")
    func = body.split("triggerAppleAuth")[1][:200] if "triggerAppleAuth" in body else ""
    if "alert(" in func and "Apple Sign-In requires" in func:
        return "Apple Sign-In is still a stub (shows alert)"

@test("Facebook auth not just an alert stub", "auth", "P2")
def test_facebook_not_stub():
    _, _, body, _ = fetch("/app.html")
    func = body.split("triggerFacebookAuth")[1][:200] if "triggerFacebookAuth" in body else ""
    if "alert(" in func and "Facebook Login requires" in func:
        return "Facebook Login is still a stub (shows alert)"


# ─── REGISTERED AGENT TESTS ──────────────────────────────────────────────

@test("Wizard has Registered Agent step", "frontend", "P0")
def test_ra_step_exists():
    _, _, body, _ = fetch("/app.html")
    if "Registered Agent" not in body:
        return "Registered Agent step missing from wizard"
    if "selectRA" not in body:
        return "selectRA function missing"
    if 'data-ra="self"' not in body or 'data-ra="sosfiler"' not in body:
        return "RA option cards missing"

@test("Wizard has 8 steps", "frontend", "P1")
def test_eight_steps():
    _, _, body, _ = fetch("/app.html")
    if "totalSteps = 8" not in body:
        return "totalSteps should be 8"

@test("RA self fields present", "frontend", "P1")
def test_ra_self_fields():
    _, _, body, _ = fetch("/app.html")
    for field_id in ["raName", "raAddress", "raCity", "raState", "raZip"]:
        if field_id not in body:
            return f"Missing RA field: {field_id}"

@test("RA fee shows in payment summary", "frontend", "P1")
def test_ra_fee_row():
    _, _, body, _ = fetch("/app.html")
    if "raFeeRow" not in body:
        return "RA fee row missing from payment summary"
    if "reviewRAFee" not in body:
        return "RA fee amount element missing"

# ─── API TESTS ───────────────────────────────────────────────────────────

@test("State fees endpoint", "api", "P0")
def test_state_fees():
    data, status, _, _ = fetch("/api/state-fees")
    if status != 200:
        return f"Expected 200, got {status}"

@test("NV state fees correct ($425)", "api", "P1")
def test_nv_fees():
    data, status, _, _ = fetch("/api/state-fees/NV")
    if status != 200:
        return f"Expected 200, got {status}"
    if data and isinstance(data, dict):
        fee = data.get("filing_fee") or data.get("fee")
        if fee and fee != 425:
            return f"NV fee should be $425 (bundled), got ${fee}"

@test("Name check works", "api", "P1")
def test_name_check():
    data, status, _, _ = fetch("/api/name-check?name=Test+LLC&state=NV")
    if status != 200:
        return f"Expected 200, got {status}"

@test("Invalid state returns 404", "api", "P2")
def test_invalid_state():
    _, status, _, _ = fetch("/api/state-fees/XX")
    if status != 404:
        return f"Expected 404, got {status}"

@test("SQL injection blocked", "api", "P0")
def test_sql_injection():
    import urllib.parse
    encoded_name = urllib.parse.quote("'; DROP TABLE orders; --")
    data, status, _, _ = fetch(f"/api/name-check?name={encoded_name}&state=NV")
    # Should not crash or return 500
    if status == 500:
        return "SQL injection caused server error"

@test("Status with fake token rejected", "api", "P1")
def test_status_fake_token():
    _, status, _, _ = fetch("/api/status/fake-order?token=fake")
    if status not in (403, 404):
        return f"Expected 403 or 404, got {status}"

@test("Documents with fake token rejected", "api", "P1")
def test_docs_fake_token():
    _, status, _, _ = fetch("/api/documents/fake-order?token=fake")
    if status not in (403, 404):
        return f"Expected 403 or 404, got {status}"


# ─── BROWSER TESTS (Playwright) ─────────────────────────────────────────

@test("Wizard step navigation works", "browser", "P0")
def test_wizard_steps():
    """Use Playwright to verify wizard loads and steps are navigable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright not installed"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{BASE_URL}/app.html", timeout=15000)
        
        # Step 1 should be visible
        step1 = page.query_selector("#step1.active")
        if not step1:
            browser.close()
            return "Step 1 not active on load"
        
        # Select LLC
        llc_card = page.query_selector('[data-type="llc"]')
        if llc_card:
            llc_card.click()
        
        browser.close()

@test("Auth modal appears at step 2→3 gate", "browser", "P0")
def test_auth_gate():
    """Verify auth modal shows when trying to proceed past step 2 without login."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright not installed"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{BASE_URL}/app.html", timeout=15000)
        
        # Clear any saved progress
        page.evaluate("localStorage.clear()")
        page.reload()
        page.wait_for_load_state("networkidle")
        
        # Select LLC (step 1)
        llc = page.query_selector('[data-type="llc"]')
        if llc:
            llc.click()
        
        # Click Continue to go to step 2 (button text is "Continue →")
        page.evaluate("nextStep()")
        page.wait_for_timeout(500)
        
        # Select a state
        page.select_option("#formationState", "NV")
        page.select_option("#homeState", "NV")
        
        # Click Continue — should trigger auth modal (not logged in)
        page.evaluate("nextStep()")
        page.wait_for_timeout(500)
        
        modal = page.query_selector('#authModal')
        modal_visible = modal and page.evaluate("el => el.style.display !== 'none'", modal)
        
        browser.close()
        
        if not modal_visible:
            return "Auth modal did not appear at step 2→3 transition"

@test("Form data persists through save/restore cycle", "browser", "P0")
def test_form_persistence():
    """Verify formData saved to localStorage survives a reload."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright not installed"
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{BASE_URL}/app.html", timeout=15000)
        
        # Clear and start fresh
        page.evaluate("localStorage.clear()")
        page.reload()
        page.wait_for_load_state("networkidle")
        
        # Fill step 1 (entity type)
        llc = page.query_selector('[data-type="llc"]')
        if llc:
            llc.click()
        
        # Go to step 2 via JS, fill state
        page.evaluate("nextStep()")
        page.wait_for_timeout(500)
        page.select_option("#formationState", "NV")
        page.select_option("#homeState", "NV")
        
        # Collect step 2 data and save progress
        page.evaluate("collectStepData(2); saveProgress()")
        
        # Reload page (simulates redirect return)
        page.reload()
        page.wait_for_load_state("networkidle")
        
        # Check if state was restored
        saved = page.evaluate("JSON.parse(localStorage.getItem('sosfiler_progress'))")
        
        browser.close()
        
        if not saved or not saved.get("data"):
            return "No saved progress found after reload"
        if saved["data"].get("state") != "NV":
            return f"State not persisted: expected NV, got {saved['data'].get('state')}"
        if saved["data"].get("entity_type", "").upper() != "LLC":
            return f"Entity type not persisted: expected LLC, got {saved['data'].get('entity_type')}"


# ─── DOCUMENT GENERATION TESTS ───────────────────────────────────────────

@test("Document generator produces PDFs", "docs", "P0")
def test_doc_gen_pdfs():
    """Run the document generator with test data and verify PDFs are created."""
    import subprocess
    result = subprocess.run([
        "python3", "-c", """
import asyncio, json, os, sys
sys.path.insert(0, '/root/.openclaw/workspace/builds/sosfiler/backend')
from document_generator import DocumentGenerator

data = {
    'entity_type': 'LLC', 'state': 'NV', 'business_name': 'QA Test LLC',
    'purpose': 'Any lawful purpose',
    'principal_address': '123 Test St', 'principal_city': 'Las Vegas',
    'principal_state': 'NV', 'principal_zip': '89101',
    'management_type': 'member-managed',
    'ra_choice': 'self', 'ra_name': 'QA Tester', 'ra_address': '123 Test St',
    'ra_city': 'Las Vegas', 'ra_state': 'NV', 'ra_zip': '89101',
    'members': [{'name': 'QA Tester', 'ownership_pct': 100, 'address': '123 Test St',
                  'city': 'Las Vegas', 'state': 'NV', 'zip_code': '89101', 'is_responsible_party': True}],
    'profit_distribution': 'pro-rata', 'voting_rights': 'pro-rata',
    'dissolution_terms': 'unanimous', 'transfer_restrictions': True,
    'tax_distributions': True, 'fiscal_year_end': 'December'
}

async def main():
    gen = DocumentGenerator()
    docs = await gen.generate_all('QA-TEST-RUN', data)
    pdfs = [d for d in docs if d['format'] == 'pdf']
    for p in pdfs:
        if not os.path.exists(p['path']) or os.path.getsize(p['path']) < 100:
            print(f'BAD:{p["filename"]}')
            return
    print(f'OK:{len(pdfs)}')

asyncio.run(main())
"""
    ], capture_output=True, text=True, timeout=30)
    output = result.stdout.strip()
    if output.startswith("BAD:"):
        return f"PDF generation failed for: {output[4:]}"
    if not output.startswith("OK:"):
        return f"Unexpected output: {output}\nStderr: {result.stderr[:200]}"
    count = int(output.split(":")[1])
    if count < 4:
        return f"Expected at least 4 PDFs, got {count}"

@test("No Articles of Organization generated", "docs", "P0")
def test_no_articles_generated():
    """Verify we don't generate Articles — the state returns those."""
    import subprocess
    result = subprocess.run([
        "python3", "-c", """
import asyncio, sys
sys.path.insert(0, '/root/.openclaw/workspace/builds/sosfiler/backend')
from document_generator import DocumentGenerator

data = {
    'entity_type': 'LLC', 'state': 'NV', 'business_name': 'QA Test LLC',
    'members': [{'name': 'Tester', 'ownership_pct': 100}],
    'management_type': 'member-managed', 'ra_choice': 'self'
}

async def main():
    gen = DocumentGenerator()
    docs = await gen.generate_all('QA-NOARTICLES', data)
    articles = [d for d in docs if 'articles' in d.get('type', '').lower()]
    print(f'ARTICLES:{len(articles)}')

asyncio.run(main())
"""
    ], capture_output=True, text=True, timeout=30)
    output = result.stdout.strip()
    if "ARTICLES:0" not in output:
        return f"Articles of Organization should NOT be generated (state returns them). Got: {output}"

@test("RA data populates in Operating Agreement", "docs", "P1")
def test_ra_in_oa():
    """Verify registered agent info appears in the Operating Agreement."""
    import subprocess
    result = subprocess.run([
        "python3", "-c", """
import asyncio, sys
sys.path.insert(0, '/root/.openclaw/workspace/builds/sosfiler/backend')
from document_generator import DocumentGenerator

data = {
    'entity_type': 'LLC', 'state': 'NV', 'business_name': 'RA Test LLC',
    'members': [{'name': 'John Doe', 'ownership_pct': 100}],
    'management_type': 'member-managed',
    'ra_choice': 'self', 'ra_name': 'Jane Agent',
    'ra_address': '456 Agent Ave', 'ra_city': 'Reno', 'ra_state': 'NV', 'ra_zip': '89501'
}

async def main():
    gen = DocumentGenerator()
    docs = await gen.generate_all('QA-RATEST', data)
    oa = [d for d in docs if 'operating_agreement' in d.get('type','') and d['format'] == 'markdown']
    if oa:
        content = open(oa[0]['path']).read()
        if 'Jane Agent' in content:
            print('OK')
        else:
            print('MISSING_RA')
    else:
        print('NO_OA')

asyncio.run(main())
"""
    ], capture_output=True, text=True, timeout=30)
    output = result.stdout.strip()
    if output == "MISSING_RA":
        return "RA name not found in Operating Agreement"
    if output == "NO_OA":
        return "No Operating Agreement generated"
    if output != "OK":
        return f"Unexpected: {output}\n{result.stderr[:200]}"


# ─── RUNNER ──────────────────────────────────────────────────────────────

def get_all_tests():
    """Collect all decorated test functions."""
    tests = []
    for name, obj in globals().items():
        if callable(obj) and hasattr(obj, '_test_name'):
            tests.append(obj)
    return tests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", action="append", default=[], help="Filter by tag (auth, api, frontend, browser, infra, all)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()
    
    tags = set(args.tag) if args.tag else {"all"}
    tests = get_all_tests()
    
    if "all" not in tags:
        tests = [t for t in tests if t._test_tag in tags]
    
    results = []
    for t in tests:
        r = run_test(t)
        results.append(r)
    
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    total = len(results)
    
    failures = [r for r in results if r["status"] == "FAIL"]
    p0_fails = [r for r in failures if r["severity"] == "P0"]
    p1_fails = [r for r in failures if r["severity"] == "P1"]
    p2_fails = [r for r in failures if r["severity"] == "P2"]
    
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "p0_failures": len(p0_fails),
            "p1_failures": len(p1_fails),
            "p2_failures": len(p2_fails),
        },
        "results": results,
        "failures": failures,
    }
    
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  SOSFiler QA Report — {report['timestamp']}")
        print(f"{'='*60}")
        print(f"  Total: {total}  |  PASS: {passed}  |  FAIL: {failed}")
        print(f"  P0: {len(p0_fails)}  |  P1: {len(p1_fails)}  |  P2: {len(p2_fails)}")
        print(f"{'='*60}\n")
        
        for r in results:
            icon = "PASS" if r["status"] == "PASS" else "FAIL"
            line = f"  [{icon}] [{r['tag']}] {r['name']} ({r['ms']}ms)"
            if r["status"] == "FAIL":
                line += f"\n         -> {r['reason']}"
            print(line)
        
        print(f"\n{'='*60}\n")
    
    # Exit code: 1 if any P0 failures
    sys.exit(1 if p0_fails else 0)


if __name__ == "__main__":
    main()
