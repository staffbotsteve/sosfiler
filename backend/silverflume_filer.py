"""
SOSFiler — Nevada SilverFlume Automated Filing Engine
Handles LLC formation through nvsilverflume.gov with hCaptcha solving.
Uses Playwright + stealth + 2Captcha for Incapsula/hCaptcha bypass.
"""

import os
import json
import asyncio
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "")
SILVERFLUME_URL = "https://www.nvsilverflume.gov/home"
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sosfiler.db"
RECEIPTS_DIR = BASE_DIR / "filing_receipts"
RECEIPTS_DIR.mkdir(exist_ok=True)

# Plan v2.6 §4.5 / PR6: Nevada SilverFlume emits a numeric receipt #
# on the bundle confirmation page (Articles + Initial List + Business
# License). Best-effort regex; refine after the first live capture.
CONFIRMATION_NUMBER_REGEX = r"(?:Receipt|Confirmation|Filing)\s*(?:Number|No\.?|#)\s*[:\-—]?\s*([0-9]{6,12})"


def _persist_filing_result(order_id: str, result: dict) -> None:
    """Track B follow-up: write SilverFlume run outcome to SQLite.

    Audit doc (docs/track_b_adapter_audit_2026-05-16.md, recommendation #1)
    flagged NV as the highest-impact gap — the worker returned a result
    dict but never updated the database, so a needs_human_review run left
    the job stuck in `automation_started` forever. This helper closes the
    loop:

    - Success + confirmation_number → write orders.filing_confirmation
      via the canonical JSON shape so the trigger can later validate.
    - needs_human_review → flip the most-recent NV filing_job to
      `operator_required` so it surfaces in the cockpit work queue.
    - Either path → persist any captured screenshots as state_correspondence
      artifacts so the operator has evidence.

    Best-effort: silently skips if DB is unavailable or no matching job
    exists. Status writes never INSERT into a terminal status; they
    UPDATE the existing row so the PR7 INSERT guard stays out of the way.
    """
    if not order_id:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        logger.warning(f"[{order_id}] silverflume persistence skipped (db open failed): {exc}")
        return
    try:
        job_row = conn.execute(
            """
            SELECT id FROM filing_jobs
            WHERE order_id = ? AND state = 'NV' AND action_type = 'formation'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (order_id,),
        ).fetchone()
        if not job_row:
            return
        job_id = job_row["id"]

        # Capture screenshots as state_correspondence artifacts so the
        # operator cockpit has at least one piece of evidence to review.
        try:
            from execution_platform import insert_filing_artifact_row, sha256_for_file_path
        except ImportError:
            insert_filing_artifact_row = None
            sha256_for_file_path = None

        if insert_filing_artifact_row is not None:
            for shot_path in result.get("screenshots") or []:
                if not shot_path:
                    continue
                filename = Path(shot_path).name
                already = conn.execute(
                    "SELECT 1 FROM filing_artifacts WHERE filing_job_id = ? AND filename = ? LIMIT 1",
                    (job_id, filename),
                ).fetchone()
                if already:
                    continue
                insert_filing_artifact_row(
                    conn,
                    filing_job_id=job_id,
                    order_id=order_id,
                    artifact_type="state_correspondence",
                    filename=filename,
                    file_path=str(shot_path),
                    is_evidence=True,
                    sha256_hex=sha256_for_file_path(shot_path) if sha256_for_file_path else None,
                )

        # If a confirmation number was extracted, write the canonical JSON.
        confirmation_value = (result.get("confirmation_number") or "").strip()
        if confirmation_value:
            try:
                from execution_platform import build_filing_confirmation_payload
                payload = build_filing_confirmation_payload(confirmation_value, "adapter")
                conn.execute(
                    "UPDATE orders SET filing_confirmation = ?, updated_at = datetime('now') WHERE id = ?",
                    (payload, order_id),
                )
            except (ImportError, ValueError) as exc:
                logger.warning(f"[{order_id}] silverflume confirmation persistence skipped: {exc}")

        # needs_human_review takes precedence over the success path: an
        # operator should review even a "successful" run that did not yield
        # a confirmation. The PR7 trigger blocks INSERTs into terminal
        # statuses; UPDATEs to operator_required do not require evidence.
        #
        # Codex Track B follow-up round-2 P2: success=True without a
        # confirmation # is also operator-required — the order cannot
        # legitimately transition into submitted/approved without one,
        # so flag it explicitly instead of leaving the job in its prior
        # automation_started status.
        target_status = None
        if result.get("needs_human_review"):
            target_status = "operator_required"
        elif result.get("success") and not confirmation_value:
            target_status = "operator_required"
            result.setdefault(
                "reason",
                "SilverFlume reported success but no confirmation number was extracted; operator review required.",
            )
        if target_status:
            conn.execute(
                "UPDATE filing_jobs SET status = ?, evidence_summary = ?, updated_at = datetime('now') WHERE id = ?",
                (target_status, (result.get("reason") or "SilverFlume needs human review."), job_id),
            )
            conn.execute(
                "UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (target_status, order_id),
            )
            conn.execute(
                "INSERT INTO status_updates (order_id, status, message) VALUES (?, ?, ?)",
                (order_id, target_status, (result.get("reason") or "SilverFlume needs human review.")),
            )

        conn.commit()
    except sqlite3.Error as exc:
        logger.warning(f"[{order_id}] silverflume persistence failed mid-write: {exc}")
    finally:
        conn.close()


class SilverFlumeFiler:
    """Automate Nevada LLC filing through SilverFlume portal."""

    def __init__(self):
        self.api_key = TWOCAPTCHA_API_KEY
        if not self.api_key:
            raise ValueError("TWOCAPTCHA_API_KEY not set in environment")

    async def file_llc(self, formation_data: dict, order_id: str) -> dict:
        """
        File an LLC with Nevada SilverFlume.
        
        Steps:
        1. Navigate to SilverFlume (bypass Incapsula WAF via stealth + captcha solve)
        2. Create account or login
        3. Start new LLC filing
        4. Fill Articles of Organization ($75)
        5. Fill Initial List of Managers/Members ($150)
        6. Fill Business License Application ($200)
        7. Pay $425 total
        8. Capture confirmation
        
        Returns dict with success status, confirmation number, screenshots.
        """
        from playwright.async_api import async_playwright
        
        result = {
            "success": False,
            "state": "NV",
            "order_id": order_id,
            "timestamps": {},
            "screenshots": [],
            "errors": [],
        }
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                    ]
                )
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-US',
                )
                
                page = await context.new_page()
                
                # Apply stealth
                try:
                    from playwright_stealth import stealth_async
                    await stealth_async(page)
                except ImportError:
                    logger.warning("playwright-stealth not available, proceeding without stealth")
                
                # Step 1: Navigate and bypass WAF
                result["timestamps"]["started"] = datetime.utcnow().isoformat()
                logger.info(f"[{order_id}] Navigating to SilverFlume...")
                
                await page.goto(SILVERFLUME_URL, timeout=60000)
                await asyncio.sleep(5)
                
                # Track B follow-up codex round-1 P1: do NOT early-return
                # on WAF failure. Set needs_human_review and let control
                # fall through to the receipt-persist + DB-persist block
                # at the function tail so the operator cockpit sees the
                # blocked filing.
                waf_blocked = False
                html = await page.content()
                if 'Incapsula' in html or '_Incapsula' in html:
                    logger.info(f"[{order_id}] Incapsula WAF detected, solving hCaptcha...")
                    captcha_solved = await self._solve_incapsula_captcha(page, order_id)
                    if not captcha_solved:
                        result["errors"].append("Failed to bypass Incapsula WAF/hCaptcha")
                        result["needs_human_review"] = True
                        await self._screenshot(page, order_id, "waf_blocked", result)
                        waf_blocked = True
                    else:
                        result["timestamps"]["waf_bypassed"] = datetime.utcnow().isoformat()
                        logger.info(f"[{order_id}] WAF bypassed successfully")

                if not waf_blocked:
                    # Step 2: Wait for SilverFlume to load
                    await asyncio.sleep(5)
                    await self._screenshot(page, order_id, "01_homepage", result)

                    # Step 3: Navigate to Start Your Business / LLC filing
                    filed = await self._navigate_to_llc_filing(page, formation_data, order_id, result)

                    if filed:
                        result["success"] = True
                        result["timestamps"]["completed"] = datetime.utcnow().isoformat()
                        # Plan v2.6 §4.5 / PR6 codex round-2: extract NV receipt #
                        # from the confirmation page so callers can forward it
                        # into orders.filing_confirmation before promotion.
                        try:
                            from execution_platform import extract_filing_confirmation
                            final_text = await page.inner_text("body", timeout=5_000)
                            extracted = extract_filing_confirmation(final_text, CONFIRMATION_NUMBER_REGEX)
                            if extracted:
                                result["confirmation_number"] = extracted
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(f"[{order_id}] confirmation extract failed: {exc}")
                    else:
                        result["needs_human_review"] = True

                await browser.close()

        except Exception as e:
            logger.error(f"[{order_id}] SilverFlume filing error: {e}")
            result["errors"].append(str(e))
            result["needs_human_review"] = True

        # Save receipt — always runs whether or not the browser block raised.
        receipt_path = RECEIPTS_DIR / f"{order_id}_NV_receipt.json"
        receipt_path.write_text(json.dumps(result, indent=2))

        # Track B follow-up: persist run outcome to SQLite so a needs_human_
        # review filing is actually visible in the operator cockpit instead
        # of stranded in the receipt JSON. Also runs after WAF failures
        # thanks to the codex round-1 P1 fix above.
        _persist_filing_result(order_id, result)

        return result

    async def _solve_incapsula_captcha(self, page, order_id: str) -> bool:
        """Solve Incapsula's hCaptcha challenge using 2Captcha."""
        try:
            from twocaptcha import TwoCaptcha
            
            solver = TwoCaptcha(self.api_key)
            
            # Wait for hCaptcha iframe to appear
            await asyncio.sleep(5)
            
            # Find the hCaptcha sitekey from the iframe src or page
            frames = page.frames
            sitekey = None
            
            for frame in frames:
                url = frame.url
                if 'hcaptcha.com' in url:
                    # Extract sitekey from iframe URL params
                    import urllib.parse
                    parsed = urllib.parse.urlparse(url)
                    params = urllib.parse.parse_qs(parsed.fragment or parsed.query)
                    sitekey = params.get('sitekey', [None])[0]
                    if not sitekey:
                        # Try from the full URL
                        for part in url.split('&'):
                            if 'sitekey=' in part:
                                sitekey = part.split('sitekey=')[1].split('&')[0]
                                break
                    if sitekey:
                        break
            
            if not sitekey:
                # Try to find sitekey in page content
                html = await page.content()
                import re
                match = re.search(r'data-sitekey=["\']([^"\']+)["\']', html)
                if match:
                    sitekey = match.group(1)
            
            if not sitekey:
                # Incapsula often uses a known sitekey
                # Try the common Incapsula hCaptcha sitekey
                logger.warning(f"[{order_id}] Could not extract hCaptcha sitekey, trying common Incapsula key")
                sitekey = "20000000-ffff-ffff-ffff-000000000002"  # Incapsula default test key
            
            logger.info(f"[{order_id}] Solving hCaptcha with sitekey: {sitekey[:20]}...")
            
            # Solve using 2Captcha (runs in thread pool since it's blocking)
            loop = asyncio.get_event_loop()
            captcha_result = await loop.run_in_executor(
                None,
                lambda: solver.hcaptcha(sitekey=sitekey, url=page.url)
            )
            
            token = captcha_result.get('code', '')
            if not token:
                logger.error(f"[{order_id}] 2Captcha returned no token")
                return False
            
            logger.info(f"[{order_id}] hCaptcha solved, token: {token[:30]}...")
            
            # Inject the token into the page
            # For Incapsula, we need to set the response and trigger the callback
            await page.evaluate(f"""
                // Set hCaptcha response
                const responses = document.querySelectorAll('[name="h-captcha-response"], [name="g-recaptcha-response"], textarea.h-captcha-response, textarea.g-recaptcha-response');
                responses.forEach(el => {{ el.value = "{token}"; }});
                
                // Try to find and trigger the hCaptcha callback
                if (typeof hcaptcha !== 'undefined') {{
                    try {{ hcaptcha.execute(); }} catch(e) {{}}
                }}
                
                // Also try setting it on the Incapsula iframe
                const iframes = document.querySelectorAll('iframe[src*="_Incapsula"]');
                iframes.forEach(iframe => {{
                    try {{
                        iframe.contentWindow.postMessage(JSON.stringify({{
                            type: 'hcaptcha-response',
                            response: "{token}"
                        }}), '*');
                    }} catch(e) {{}}
                }});
            """)
            
            # Wait for redirect after captcha solve
            await asyncio.sleep(10)
            
            # Check if we passed through
            new_html = await page.content()
            if 'Incapsula' not in new_html and len(new_html) > 2000:
                return True
            
            # Sometimes need to reload after solving
            await page.reload()
            await asyncio.sleep(10)
            
            final_html = await page.content()
            return 'Incapsula' not in final_html and len(final_html) > 2000
            
        except Exception as e:
            logger.error(f"[{order_id}] Captcha solve error: {e}")
            return False

    async def _navigate_to_llc_filing(self, page, data: dict, order_id: str, result: dict) -> bool:
        """Navigate through SilverFlume to file an LLC."""
        try:
            # Look for "Start Your Business" or similar navigation
            body_text = await page.inner_text('body')
            logger.info(f"[{order_id}] Page loaded, body length: {len(body_text)}")
            
            # Find and click "Start Your Business" or "New Filing"
            start_links = await page.query_selector_all('a, button')
            for link in start_links:
                text = await link.inner_text()
                text = text.strip().lower() if text else ''
                if any(kw in text for kw in ['start your business', 'new business', 'register', 'file new']):
                    logger.info(f"[{order_id}] Clicking: {text}")
                    await link.click()
                    await asyncio.sleep(3)
                    break
            
            await self._screenshot(page, order_id, "02_after_start", result)
            
            # At this point we need to map the actual SilverFlume form flow.
            # The portal requires:
            # 1. Account creation/login
            # 2. Entity type selection (Domestic LLC - NRS 86)
            # 3. Articles of Organization form
            # 4. Initial List form
            # 5. Business License form
            # 6. Payment
            
            # Since we can't fully map the dynamic SPA without seeing it,
            # we'll collect form data and prepare it for submission.
            # The actual form filling will be refined once we can access the portal.
            
            filing_data = self._prepare_nv_filing_data(data)
            
            # Save prepared data for manual/automated submission
            prep_path = RECEIPTS_DIR / f"{order_id}_NV_prepared_data.json"
            prep_path.write_text(json.dumps(filing_data, indent=2))
            result["prepared_data"] = str(prep_path)
            
            # Try to fill forms if we can see them
            forms_filled = await self._fill_nv_forms(page, filing_data, order_id, result)
            
            return forms_filled
            
        except Exception as e:
            logger.error(f"[{order_id}] Navigation error: {e}")
            result["errors"].append(f"Navigation: {str(e)}")
            return False

    def _prepare_nv_filing_data(self, data: dict) -> dict:
        """Prepare all data needed for NV SilverFlume filing."""
        members = data.get("members", [])
        primary = members[0] if members else {}
        
        return {
            "articles_of_organization": {
                "entity_name": data.get("business_name", ""),
                "entity_type": "Domestic Limited-Liability Company",
                "nrs_chapter": "NRS Chapter 86",
                "registered_agent_name": data.get("ra_name", ""),
                "registered_agent_address": data.get("ra_address", ""),
                "registered_agent_city": data.get("ra_city", ""),
                "registered_agent_state": data.get("ra_state", "NV"),
                "registered_agent_zip": data.get("ra_zip", ""),
                "management_type": data.get("management_type", "member-managed"),
                "purpose": data.get("purpose", "Any lawful purpose"),
                "organizer_name": "SOSFiler Document Services",
                "filing_fee": 75,
            },
            "initial_list": {
                "entity_name": data.get("business_name", ""),
                "managers_or_members": [
                    {
                        "name": m.get("name", ""),
                        "address": m.get("address", ""),
                        "city": m.get("city", ""),
                        "state": m.get("state", ""),
                        "zip": m.get("zip_code", ""),
                        "title": "Managing Member" if data.get("management_type") == "member-managed" else "Manager",
                    }
                    for m in members
                ],
                "filing_fee": 150,
            },
            "business_license": {
                "entity_name": data.get("business_name", ""),
                "business_address": data.get("principal_address", ""),
                "business_city": data.get("principal_city", ""),
                "business_state": data.get("principal_state", "NV"),
                "business_zip": data.get("principal_zip", ""),
                "naics_code": "",  # Will need to be filled based on business type
                "num_employees_nv": 0,
                "filing_fee": 200,
            },
            "payment": {
                "total": 425,
                "method": "credit_card",  # SilverFlume accepts CC with 2.5% surcharge
            },
            "contact": {
                "email": data.get("email", ""),
                "name": primary.get("name", ""),
            }
        }

    async def _fill_nv_forms(self, page, filing_data: dict, order_id: str, result: dict) -> bool:
        """
        Attempt to fill NV SilverFlume forms.
        
        This method will be iteratively refined as we map the actual portal flow.
        For now, it prepares the data and attempts basic form interaction.
        """
        try:
            # Check what page we're on
            body = await page.inner_text('body')
            url = page.url
            
            logger.info(f"[{order_id}] Current URL: {url}")
            logger.info(f"[{order_id}] Page content length: {len(body)}")
            
            # Look for form elements
            inputs = await page.query_selector_all('input, select, textarea')
            logger.info(f"[{order_id}] Found {len(inputs)} form elements")
            
            for inp in inputs[:20]:
                name = await inp.get_attribute('name') or ''
                id_ = await inp.get_attribute('id') or ''
                type_ = await inp.get_attribute('type') or ''
                placeholder = await inp.get_attribute('placeholder') or ''
                logger.info(f"[{order_id}]   Input: name={name} id={id_} type={type_} placeholder={placeholder}")
            
            await self._screenshot(page, order_id, "03_form_discovery", result)
            
            # If we found form elements, try to fill them
            # This will be expanded with specific selectors once we can access the portal
            if len(inputs) > 5:
                articles = filing_data.get("articles_of_organization", {})
                
                # Try common field names/IDs
                field_mapping = {
                    'entityName': articles.get('entity_name', ''),
                    'entity_name': articles.get('entity_name', ''),
                    'name': articles.get('entity_name', ''),
                    'businessName': articles.get('entity_name', ''),
                    'registeredAgentName': articles.get('registered_agent_name', ''),
                    'registered_agent': articles.get('registered_agent_name', ''),
                    'agentName': articles.get('registered_agent_name', ''),
                    'agentAddress': articles.get('registered_agent_address', ''),
                    'agentCity': articles.get('registered_agent_city', ''),
                    'agentState': articles.get('registered_agent_state', ''),
                    'agentZip': articles.get('registered_agent_zip', ''),
                }
                
                filled = 0
                for field_name, value in field_mapping.items():
                    if value:
                        try:
                            selector = f'input[name="{field_name}"], input[id="{field_name}"], input[placeholder*="{field_name}"]'
                            el = await page.query_selector(selector)
                            if el:
                                await el.fill(value)
                                filled += 1
                                logger.info(f"[{order_id}] Filled {field_name} = {value}")
                        except Exception:
                            pass
                
                if filled > 0:
                    await self._screenshot(page, order_id, "04_forms_filled", result)
                    result["fields_filled"] = filled
                    logger.info(f"[{order_id}] Filled {filled} form fields")
                    return True
            
            # If we couldn't fill forms automatically, mark for review
            logger.warning(f"[{order_id}] Could not auto-fill forms, marking for human review")
            result["needs_human_review"] = True
            result["portal_url"] = url
            result["reason"] = "Form auto-fill could not map SilverFlume fields. Portal data prepared for manual submission."
            return False
            
        except Exception as e:
            logger.error(f"[{order_id}] Form filling error: {e}")
            result["errors"].append(f"Form fill: {str(e)}")
            return False

    async def _screenshot(self, page, order_id: str, step: str, result: dict):
        """Take and save a screenshot."""
        try:
            path = RECEIPTS_DIR / f"{order_id}_NV_{step}.png"
            await page.screenshot(path=str(path), full_page=True)
            result["screenshots"].append(str(path))
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
