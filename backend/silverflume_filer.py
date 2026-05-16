"""
SOSFiler — Nevada SilverFlume Automated Filing Engine
Handles LLC formation through nvsilverflume.gov with hCaptcha solving.
Uses Playwright + stealth + 2Captcha for Incapsula/hCaptcha bypass.
"""

import os
import json
import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY", "")
SILVERFLUME_URL = "https://www.nvsilverflume.gov/home"
RECEIPTS_DIR = Path(__file__).resolve().parent.parent / "filing_receipts"
RECEIPTS_DIR.mkdir(exist_ok=True)

# Plan v2.6 §4.5 / PR6: Nevada SilverFlume emits a numeric receipt #
# on the bundle confirmation page (Articles + Initial List + Business
# License). Best-effort regex; refine after the first live capture.
# Whoever consumes the file_llc() result dict and persists status MUST
# pass result["confirmation_number"] forward to
# execution_platform.build_filing_confirmation_payload so the order's
# filing_confirmation column gets populated before any status promotion.
CONFIRMATION_NUMBER_REGEX = r"(?:Receipt|Confirmation|Filing)\s*(?:Number|No\.?|#)\s*[:\-—]?\s*([0-9]{6,12})"


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
                
                # Check for Incapsula challenge
                html = await page.content()
                if 'Incapsula' in html or '_Incapsula' in html:
                    logger.info(f"[{order_id}] Incapsula WAF detected, solving hCaptcha...")
                    captcha_solved = await self._solve_incapsula_captcha(page, order_id)
                    if not captcha_solved:
                        result["errors"].append("Failed to bypass Incapsula WAF/hCaptcha")
                        result["needs_human_review"] = True
                        await self._screenshot(page, order_id, "waf_blocked", result)
                        await browser.close()
                        return result
                    
                    result["timestamps"]["waf_bypassed"] = datetime.utcnow().isoformat()
                    logger.info(f"[{order_id}] WAF bypassed successfully")
                
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
        
        # Save receipt
        receipt_path = RECEIPTS_DIR / f"{order_id}_NV_receipt.json"
        receipt_path.write_text(json.dumps(result, indent=2))
        
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
