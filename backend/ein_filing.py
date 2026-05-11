"""
SOSFiler — EIN Filing Automation
Automates IRS EIN online application and generates confirmation letter.
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "generated_docs"


class EINFiler:
    """Automate IRS EIN application process."""

    IRS_EIN_URL = "https://sa.www4.irs.gov/modiein/individual/index.jsp"

    async def apply(self, formation_data: dict, order_id: str) -> dict:
        """
        Apply for an EIN with the IRS.
        
        Production flow:
        1. Navigate to IRS EIN online assistant
        2. Select entity type (LLC)
        3. Fill in responsible party info (from SS-4 data)
        4. Complete application
        5. Capture EIN from confirmation page
        6. Generate confirmation letter PDF
        
        IRS Online EIN is available Mon-Fri, 7am-10pm ET.
        """
        ss4_data = self._prepare_ss4(formation_data)
        
        try:
            result = await self._automate_irs_application(ss4_data, order_id)
            
            if result.get("ein"):
                # Generate confirmation letter
                await self._generate_ein_letter(result["ein"], formation_data, order_id)
                return result
            
            # Fall back to manual process
            return self._queue_manual_ein(ss4_data, order_id)
            
        except Exception as e:
            logger.error(f"EIN application error: {e}")
            return self._queue_manual_ein(ss4_data, order_id)

    def _prepare_ss4(self, data: dict) -> dict:
        """Prepare Form SS-4 data from formation wizard data."""
        members = data.get("members", [])
        responsible = next(
            (m for m in members if m.get("is_responsible_party")),
            members[0] if members else {}
        )
        
        return {
            "entity_name": data.get("business_name", ""),
            "entity_type": data.get("entity_type", "LLC"),
            "state": data.get("state", ""),
            "num_members": len(members),
            "responsible_party": {
                "name": responsible.get("name", ""),
                "ssn": "",
                "ssn_vault_id": data.get("responsible_party_ssn_vault_id", ""),
                "ssn_last4": data.get("responsible_party_ssn_last4") or responsible.get("ssn_last4", ""),
                "address": responsible.get("address", ""),
                "city": responsible.get("city", ""),
                "state": responsible.get("state", ""),
                "zip": responsible.get("zip_code", ""),
            },
            "business_address": {
                "street": data.get("principal_address", ""),
                "city": data.get("principal_city", ""),
                "state": data.get("principal_state", ""),
                "zip": data.get("principal_zip", ""),
            },
            "purpose": data.get("purpose", "Any lawful purpose"),
            "fiscal_year_end": data.get("fiscal_year_end", "December"),
            "date_started": datetime.now().strftime("%m/%d/%Y"),
        }

    async def _automate_irs_application(self, ss4_data: dict, order_id: str) -> dict:
        """
        Automate the IRS EIN online application using Playwright.
        
        The IRS EIN online assistant is a multi-step wizard:
        1. Select entity type
        2. Confirm LLC details  
        3. Enter responsible party info
        4. Enter entity details
        5. Review and submit
        6. Receive EIN immediately
        """
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )
                page = await context.new_page()
                
                try:
                    # Navigate to IRS EIN application
                    await page.goto(self.IRS_EIN_URL, timeout=30000)
                    
                    # Check if portal is available (Mon-Fri 7am-10pm ET only)
                    content = await page.content()
                    if "not available" in content.lower() or "maintenance" in content.lower():
                        return {
                            "success": False,
                            "reason": "IRS EIN portal is currently unavailable (limited hours: Mon-Fri 7am-10pm ET)",
                            "queued": True
                        }
                    
                    # Screenshot for records
                    screenshot_path = DOCS_DIR / order_id / f"irs_ein_screenshot.png"
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(screenshot_path))
                    
                    # In production: complete the full IRS form flow
                    # The IRS online EIN application is a JavaScript-heavy app
                    # that requires careful step-by-step automation
                    
                    # For MVP: queue for manual application
                    return {
                        "success": False,
                        "reason": "IRS EIN portal accessed. Queued for assisted application.",
                        "screenshot": str(screenshot_path),
                        "queued": True
                    }
                    
                finally:
                    await browser.close()
                    
        except ImportError:
            return {"success": False, "reason": "Playwright not installed", "queued": True}
        except Exception as e:
            return {"success": False, "reason": str(e), "queued": True}

    def _queue_manual_ein(self, ss4_data: dict, order_id: str) -> dict:
        """Queue EIN application for manual processing."""
        # Save SS-4 data for manual application
        queue_path = DOCS_DIR / order_id / "ein_queue.json"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_data = {
            "order_id": order_id,
            "ss4_data": ss4_data,
            "queued_at": datetime.utcnow().isoformat(),
            "status": "queued",
            "notes": "Apply via https://sa.www4.irs.gov/modiein/individual/index.jsp (Mon-Fri 7am-10pm ET)"
        }
        queue_path.write_text(json.dumps(queue_data, indent=2))
        
        return {
            "success": False,
            "ein": None,
            "status": "queued",
            "reason": "EIN application queued for processing. The IRS online application is available Mon-Fri, 7am-10pm ET. You'll receive your EIN within 1-2 business days.",
            "queue_file": str(queue_path)
        }

    async def _generate_ein_letter(self, ein: str, formation_data: dict, order_id: str):
        """Generate EIN confirmation letter as PDF and Markdown."""
        business_name = formation_data.get("business_name", "")
        members = formation_data.get("members", [])
        responsible = next(
            (m for m in members if m.get("is_responsible_party")),
            members[0] if members else {}
        )
        
        letter_content = f"""# EIN Confirmation Letter

## Department of the Treasury — Internal Revenue Service

---

**Date:** {datetime.now().strftime("%B %d, %Y")}

**To:**
{responsible.get("name", "")}
{formation_data.get("principal_address", "")}
{formation_data.get("principal_city", "")}, {formation_data.get("principal_state", "")} {formation_data.get("principal_zip", "")}

---

**Re: Employer Identification Number (EIN) Assignment**

Dear {responsible.get("name", "").split()[0] if responsible.get("name") else "Applicant"},

We assigned you an Employer Identification Number (EIN) for the entity listed below:

**Legal Name:** {business_name}
**EIN:** {ein}
**Entity Type:** {formation_data.get("entity_type", "LLC")}
**State of Formation:** {formation_data.get("state", "")}
**Effective Date:** {datetime.now().strftime("%B %d, %Y")}

This EIN is for use with your business tax returns and related filings. Please keep this letter in your permanent records.

### Important Information:

1. **Banking:** Present this letter to your financial institution when opening a business bank account.
2. **Tax Filing:** Use this EIN on all federal tax returns, statements, and related documents.
3. **Employees:** If you plan to hire employees, you must file Form 941 (quarterly) and related employment tax forms.
4. **1099s:** Provide this EIN to anyone who pays your business $600 or more per year.

### Next Steps:

- Open a business bank account using this EIN
- Set up accounting/bookkeeping for the business
- Register for state and local taxes as required
- File annual tax returns (Form 1065 for partnerships / Schedule C for single-member LLCs)

---

*This confirmation was generated by SOSFiler on behalf of {business_name}.*
*Keep this document in your permanent business records.*

**IRS Customer Service:** 1-800-829-4933 (Mon-Fri, 7am-7pm local time)
"""

        # Save markdown
        order_dir = DOCS_DIR / order_id
        order_dir.mkdir(parents=True, exist_ok=True)
        
        md_path = order_dir / "ein_confirmation_letter.md"
        md_path.write_text(letter_content)
        
        # Generate PDF
        pdf_path = order_dir / "ein_confirmation_letter.pdf"
        try:
            from document_generator import DocumentGenerator
            gen = DocumentGenerator()
            gen._markdown_to_pdf(letter_content, str(pdf_path), f"EIN Confirmation — {business_name}")
        except Exception:
            pdf_path.write_text(letter_content)  # Fallback to text
