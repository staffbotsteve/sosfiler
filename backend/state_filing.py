"""
SOSFiler — Automated State Filing Engine
All 50 states + DC. Uses Playwright for online portals, generates pre-filled
PDFs for mail-only states. Fallback to human review queue.
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent

# Filing status constants
STATUS_QUEUED = "queued"
STATUS_FILING = "filing"
STATUS_SUBMITTED = "submitted"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_HUMAN_REVIEW = "human_review"
STATUS_FAILED = "failed"

MAX_RETRIES = 3
RETRY_INTERVAL_MINUTES = 30

# ── All 50 states + DC: portal info ─────────────────────────────────────────

STATE_PORTALS = {
    "AL": {
        "name": "Alabama",
        "office": "Alabama Secretary of State",
        "portal_url": "https://www.sos.alabama.gov/business-entities",
        "method": "online",
        "form": "Articles of Organization (Domestic LLC)",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name", "purpose"],
        "fee": 200,
        "notes": "Also requires county-level recording (probate court)."
    },
    "AK": {
        "name": "Alaska",
        "office": "Alaska Division of Corporations",
        "portal_url": "https://www.commerce.alaska.gov/cbp/main/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "purpose", "organizer_name"],
        "fee": 250
    },
    "AZ": {
        "name": "Arizona",
        "office": "Arizona Corporation Commission",
        "portal_url": "https://ecorp.azcc.gov/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "management_type", "member_info", "organizer_name"],
        "fee": 50,
        "notes": "Must also publish in newspaper within 60 days ($100-300 varies)."
    },
    "AR": {
        "name": "Arkansas",
        "office": "Arkansas Secretary of State",
        "portal_url": "https://www.sos.arkansas.gov/corps/search_all.php",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 45
    },
    "CA": {
        "name": "California",
        "office": "California Secretary of State",
        "portal_url": "https://bizfileonline.sos.ca.gov",
        "method": "online",
        "form": "LLC-1 (Articles of Organization)",
        "required_fields": ["llc_name", "purpose", "registered_agent_name", "registered_agent_address", "management_type", "organizer_name"],
        "fee": 70,
        "notes": "$800 annual franchise tax. File Statement of Information (LLC-12) within 90 days."
    },
    "CO": {
        "name": "Colorado",
        "office": "Colorado Secretary of State",
        "portal_url": "https://www.sos.state.co.us/pubs/business/businessHome.html",
        "method": "online",
        "form": "Articles of Organization (online only)",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 50
    },
    "CT": {
        "name": "Connecticut",
        "office": "Connecticut Secretary of State",
        "portal_url": "https://service.ct.gov/business/s/onlinebusinessfilings",
        "method": "online",
        "form": "Certificate of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 120
    },
    "DE": {
        "name": "Delaware",
        "office": "Delaware Division of Corporations",
        "portal_url": "https://icis.corp.delaware.gov/ecorp/logintax.aspx",
        "method": "online",
        "form": "Certificate of Formation",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address"],
        "fee": 90,
        "notes": "$300 annual franchise tax."
    },
    "DC": {
        "name": "District of Columbia",
        "office": "DC Department of Consumer and Regulatory Affairs",
        "portal_url": "https://corponline.dcra.dc.gov/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name", "purpose"],
        "fee": 99
    },
    "FL": {
        "name": "Florida",
        "office": "Florida Division of Corporations",
        "portal_url": "https://efile.sunbiz.org",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "principal_address", "mailing_address", "registered_agent_name", "registered_agent_address", "member_info", "effective_date"],
        "fee": 125
    },
    "GA": {
        "name": "Georgia",
        "office": "Georgia Secretary of State",
        "portal_url": "https://ecorp.sos.ga.gov/BusinessSearch",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name", "management_type"],
        "fee": 100,
        "notes": "Must publish formation notice in county legal organ."
    },
    "HI": {
        "name": "Hawaii",
        "office": "Hawaii Department of Commerce and Consumer Affairs",
        "portal_url": "https://hbe.ehawaii.gov/documents/search.html",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name", "principal_address"],
        "fee": 50
    },
    "ID": {
        "name": "Idaho",
        "office": "Idaho Secretary of State",
        "portal_url": "https://sosbiz.idaho.gov/",
        "method": "online",
        "form": "Certificate of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name", "principal_address"],
        "fee": 100
    },
    "IL": {
        "name": "Illinois",
        "office": "Illinois Secretary of State",
        "portal_url": "https://www.ilsos.gov/corporatellc/",
        "method": "online",
        "form": "LLC-5.5 (Articles of Organization)",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "purpose", "management_type", "organizer_name"],
        "fee": 150
    },
    "IN": {
        "name": "Indiana",
        "office": "Indiana Secretary of State",
        "portal_url": "https://inbiz.in.gov/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 95
    },
    "IA": {
        "name": "Iowa",
        "office": "Iowa Secretary of State",
        "portal_url": "https://sos.iowa.gov/business/FormsAndFees.html",
        "method": "online",
        "form": "Certificate of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 50
    },
    "KS": {
        "name": "Kansas",
        "office": "Kansas Secretary of State",
        "portal_url": "https://www.sos.ks.gov/business/business.html",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 160
    },
    "KY": {
        "name": "Kentucky",
        "office": "Kentucky Secretary of State",
        "portal_url": "https://app.sos.ky.gov/ftsearch/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 40
    },
    "LA": {
        "name": "Louisiana",
        "office": "Louisiana Secretary of State",
        "portal_url": "https://coraweb.sos.la.gov/commercialsearch/CommercialSearch.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "purpose", "organizer_name"],
        "fee": 100
    },
    "ME": {
        "name": "Maine",
        "office": "Maine Secretary of State",
        "portal_url": "https://www.maine.gov/sos/cec/corp/",
        "method": "online",
        "form": "Certificate of Formation",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 175
    },
    "MD": {
        "name": "Maryland",
        "office": "Maryland State Department of Assessments and Taxation",
        "portal_url": "https://egov.maryland.gov/BusinessExpress",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "purpose", "organizer_name"],
        "fee": 100
    },
    "MA": {
        "name": "Massachusetts",
        "office": "Massachusetts Secretary of the Commonwealth",
        "portal_url": "https://corp.sec.state.ma.us/corpweb/CorpSearch/CorpSearch.aspx",
        "method": "online",
        "form": "Certificate of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 500
    },
    "MI": {
        "name": "Michigan",
        "office": "Michigan Department of Licensing and Regulatory Affairs",
        "portal_url": "https://cofs.lara.state.mi.us/SearchApi/Search/Search",
        "method": "online",
        "form": "Articles of Organization (Form 700)",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "purpose", "organizer_name", "duration"],
        "fee": 50
    },
    "MN": {
        "name": "Minnesota",
        "office": "Minnesota Secretary of State",
        "portal_url": "https://mblsportal.sos.state.mn.us/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 155
    },
    "MS": {
        "name": "Mississippi",
        "office": "Mississippi Secretary of State",
        "portal_url": "https://corp.sos.ms.gov/corp/portal/c/page/corpBusinessIdSearch/portal.aspx",
        "method": "online",
        "form": "Certificate of Formation",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 50
    },
    "MO": {
        "name": "Missouri",
        "office": "Missouri Secretary of State",
        "portal_url": "https://bsd.sos.mo.gov/BusinessEntity/BESearch.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name", "purpose"],
        "fee": 50
    },
    "MT": {
        "name": "Montana",
        "office": "Montana Secretary of State",
        "portal_url": "https://sosmt.gov/business/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 70
    },
    "NE": {
        "name": "Nebraska",
        "office": "Nebraska Secretary of State",
        "portal_url": "https://www.nebraska.gov/sos/corp/corpsearch.cgi",
        "method": "online",
        "form": "Certificate of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 105,
        "notes": "Must publish in newspaper."
    },
    "NV": {
        "name": "Nevada",
        "office": "Nevada Secretary of State",
        "portal_url": "https://www.nvsilverflume.gov/home",
        "method": "online",
        "form": "Articles of Organization + Initial List + Business License",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "management_type", "organizer_name"],
        "fee": 425,
        "notes": "Bundled: $75 Articles + $150 Initial List + $200 Business License. All required at filing."
    },
    "NH": {
        "name": "New Hampshire",
        "office": "New Hampshire Secretary of State",
        "portal_url": "https://quickstart.sos.nh.gov/online/BusinessInquire",
        "method": "online",
        "form": "Certificate of Formation",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 100
    },
    "NJ": {
        "name": "New Jersey",
        "office": "New Jersey Division of Revenue",
        "portal_url": "https://www.njportal.com/DOR/BusinessFormation",
        "method": "online",
        "form": "Certificate of Formation",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 125
    },
    "NM": {
        "name": "New Mexico",
        "office": "New Mexico Secretary of State",
        "portal_url": "https://portal.sos.state.nm.us/BFS/online/corporationbusinesssearch",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name", "duration"],
        "fee": 50
    },
    "NY": {
        "name": "New York",
        "office": "New York Department of State",
        "portal_url": "https://appext20.dos.ny.gov/corp_public/corpsearch.entity_search_entry",
        "method": "online",
        "form": "Articles of Organization (DOS 1336)",
        "required_fields": ["llc_name", "county", "registered_agent_address", "organizer_name"],
        "fee": 200,
        "notes": "Must publish in 2 newspapers for 6 consecutive weeks within 120 days ($300-$1500+)."
    },
    "NC": {
        "name": "North Carolina",
        "office": "North Carolina Secretary of State",
        "portal_url": "https://www.sosnc.gov/online_services/search/by_title/_Business_Registration",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 125
    },
    "ND": {
        "name": "North Dakota",
        "office": "North Dakota Secretary of State",
        "portal_url": "https://firststop.sos.nd.gov/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 135
    },
    "OH": {
        "name": "Ohio",
        "office": "Ohio Secretary of State",
        "portal_url": "https://www.ohiobusinesscentral.gov/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 99
    },
    "OK": {
        "name": "Oklahoma",
        "office": "Oklahoma Secretary of State",
        "portal_url": "https://www.sos.ok.gov/business/corp/filing.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name", "duration"],
        "fee": 100
    },
    "OR": {
        "name": "Oregon",
        "office": "Oregon Secretary of State",
        "portal_url": "https://sos.oregon.gov/business/Pages/register.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 100
    },
    "PA": {
        "name": "Pennsylvania",
        "office": "Pennsylvania Department of State",
        "portal_url": "https://www.dos.pa.gov/BusinessCharities/Business/RegistrationForms/Pages/default.aspx",
        "method": "mail",
        "form": "Certificate of Organization (DSCB:15-8821)",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 125,
        "mailing_address": "Pennsylvania Department of State, Bureau of Corporations and Charitable Organizations, P.O. Box 8722, Harrisburg, PA 17105-8722",
        "notes": "Must advertise intent in 2 newspapers."
    },
    "RI": {
        "name": "Rhode Island",
        "office": "Rhode Island Secretary of State",
        "portal_url": "https://business.sos.ri.gov/CorpWeb/CorpSearch/CorpSearch.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 150
    },
    "SC": {
        "name": "South Carolina",
        "office": "South Carolina Secretary of State",
        "portal_url": "https://businessfilings.sc.gov/BusinessFiling",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 110
    },
    "SD": {
        "name": "South Dakota",
        "office": "South Dakota Secretary of State",
        "portal_url": "https://sosenterprise.sd.gov/BusinessServices/Business/FilingSearch.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 150
    },
    "TN": {
        "name": "Tennessee",
        "office": "Tennessee Secretary of State",
        "portal_url": "https://tnbear.tn.gov/NewBiz/Default.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 300,
        "notes": "Fee is $300 minimum or $50 per member."
    },
    "TX": {
        "name": "Texas",
        "office": "Texas Secretary of State",
        "portal_url": "https://direct.sos.state.tx.us/acct/acct-login.asp",
        "method": "online",
        "form": "Certificate of Formation (Form 205)",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "purpose", "management_type", "organizer_name", "member_names"],
        "fee": 300
    },
    "UT": {
        "name": "Utah",
        "office": "Utah Division of Corporations",
        "portal_url": "https://secure.utah.gov/bes/",
        "method": "online",
        "form": "Certificate of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 54
    },
    "VT": {
        "name": "Vermont",
        "office": "Vermont Secretary of State",
        "portal_url": "https://sos.vermont.gov/corporations/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 125
    },
    "VA": {
        "name": "Virginia",
        "office": "Virginia State Corporation Commission",
        "portal_url": "https://cis.scc.virginia.gov/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "organizer_name"],
        "fee": 100
    },
    "WA": {
        "name": "Washington",
        "office": "Washington Secretary of State",
        "portal_url": "https://ccfs.sos.wa.gov",
        "method": "online",
        "form": "Certificate of Formation",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "principal_address", "duration", "management_type", "organizer_name"],
        "fee": 200
    },
    "WV": {
        "name": "West Virginia",
        "office": "West Virginia Secretary of State",
        "portal_url": "https://apps.wv.gov/SOS/BusinessEntity/",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 100
    },
    "WI": {
        "name": "Wisconsin",
        "office": "Wisconsin Department of Financial Institutions",
        "portal_url": "https://www.wdfi.org/apps/CorpSearch/Search.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 130
    },
    "WY": {
        "name": "Wyoming",
        "office": "Wyoming Secretary of State",
        "portal_url": "https://wyobiz.wyo.gov/Business/FilingSearch.aspx",
        "method": "online",
        "form": "Articles of Organization",
        "required_fields": ["llc_name", "registered_agent_name", "registered_agent_address", "organizer_name"],
        "fee": 100
    },
}

# States that are primarily mail-filing (may also accept online for some entity types)
MAIL_ONLY_STATES = {"PA"}  # PA is the primary mail-only state; others accept online


class StateFiler:
    """Automate state LLC filing via headless browser or mail preparation."""

    def __init__(self):
        self.receipts_dir = BASE_DIR / "filing_receipts"
        self.receipts_dir.mkdir(exist_ok=True)

    async def file(self, state: str, formation_data: dict, order_id: str) -> dict:
        """
        File formation documents with the state.
        Routes to online automation or mail-filing preparation.
        """
        state = state.upper()
        portal = STATE_PORTALS.get(state)
        if not portal:
            return {
                "success": False,
                "status": STATUS_FAILED,
                "reason": f"Unknown state: {state}"
            }

        if portal["method"] == "mail":
            return await self._prepare_mail_filing(state, formation_data, order_id, portal)

        # Online filing — try automation with retry
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"Filing attempt {attempt}/{MAX_RETRIES} for {order_id} in {state}")
                result = await self._file_online(state, formation_data, order_id, portal)

                if result.get("success"):
                    self._save_receipt(order_id, state, result)
                    return result

                if result.get("needs_human_review"):
                    logger.warning(f"Order {order_id}: Filing requires human review — {result.get('reason')}")
                    return {
                        "success": False,
                        "status": STATUS_HUMAN_REVIEW,
                        "reason": result.get("reason", "Automation blocked"),
                        "portal_url": portal["portal_url"],
                        "form": portal["form"],
                        "state_fee": portal["fee"],
                        "attempt": attempt,
                    }

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"Filing error attempt {attempt}: {e}")
                if attempt >= MAX_RETRIES:
                    return {
                        "success": False,
                        "status": STATUS_FAILED,
                        "reason": str(e),
                        "attempt": attempt,
                        "needs_human_review": True,
                    }

        return {
            "success": False,
            "status": STATUS_HUMAN_REVIEW,
            "reason": "Max retries exceeded",
            "attempt": MAX_RETRIES,
        }

    # ── Online filing (Playwright skeleton) ─────────────────────────────────

    async def _file_online(self, state: str, data: dict, order_id: str, portal: dict) -> dict:
        """
        Generic online filing via Playwright.
        Each state has specific form flow handled by a sub-method if available;
        otherwise falls back to human-review with portal info prepared.
        """
        specific_filers = {
            "CA": self._flow_california,
            "TX": self._flow_texas,
            "FL": self._flow_florida,
            "NY": self._flow_new_york,
            "DE": self._flow_delaware,
            "WY": self._flow_wyoming,
            "NV": self._flow_nevada,
            "CO": self._flow_colorado,
            "IL": self._flow_illinois,
            "GA": self._flow_georgia,
            "WA": self._flow_washington,
            "OH": self._flow_ohio,
            "NC": self._flow_north_carolina,
            "IN": self._flow_indiana,
            "NJ": self._flow_new_jersey,
            "AZ": self._flow_arizona,
        }

        specific = specific_filers.get(state)
        if specific:
            return await specific(data, order_id, portal)

        # Generic: attempt portal access, screenshot, queue for human review
        return await self._generic_online_filing(state, data, order_id, portal)

    async def _generic_online_filing(self, state: str, data: dict, order_id: str, portal: dict) -> dict:
        """Attempt to access portal, take screenshot, prepare for human filing."""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )
                page = await ctx.new_page()
                try:
                    await page.goto(portal["portal_url"], timeout=30000)

                    # Check for CAPTCHA
                    if await page.query_selector("[class*='captcha']") or await page.query_selector("[id*='captcha']"):
                        return {"success": False, "needs_human_review": True, "reason": f"CAPTCHA detected on {state} portal"}

                    screenshot = self.receipts_dir / f"{order_id}_{state}_screenshot.png"
                    await page.screenshot(path=str(screenshot))

                    return {
                        "success": False,
                        "needs_human_review": True,
                        "reason": f"Automated {state} filing ready for human completion. Portal accessed successfully.",
                        "portal_url": portal["portal_url"],
                        "form": portal["form"],
                        "state_fee": portal["fee"],
                        "screenshot": str(screenshot),
                    }
                finally:
                    await browser.close()

        except ImportError:
            return self._manual_fallback(state, data, order_id, portal)
        except Exception as e:
            logger.error(f"{state} generic filing error: {e}")
            return self._manual_fallback(state, data, order_id, portal)

    # ── State-specific Playwright flows ─────────────────────────────────────

    async def _flow_california(self, data: dict, order_id: str, portal: dict) -> dict:
        """
        California – bizfileonline.sos.ca.gov
        Flow: Home → File → LLC → LLC-1 → Fill fields → Pay $70 → Confirm
        """
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )).new_page()
                try:
                    await page.goto("https://bizfileonline.sos.ca.gov", timeout=30000)
                    if await page.query_selector("[class*='captcha']"):
                        return {"success": False, "needs_human_review": True, "reason": "CAPTCHA on CA portal"}
                    ss = self.receipts_dir / f"{order_id}_CA_screenshot.png"
                    await page.screenshot(path=str(ss))
                    # Production: click "File" → select "LLC" → "Articles of Organization (LLC-1)"
                    # Fill: LLC name, agent name/address, management type, organizer
                    # Pay $70 via platform card
                    # Capture confirmation number
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "CA portal accessed. Data prepared for filing.",
                        "portal_url": portal["portal_url"], "form": "LLC-1",
                        "state_fee": 70, "screenshot": str(ss),
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("CA", data, order_id, portal)

    async def _flow_texas(self, data: dict, order_id: str, portal: dict) -> dict:
        """Texas – SOSDirect. Form 205 (Certificate of Formation)."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://direct.sos.state.tx.us/acct/acct-login.asp", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_TX_screenshot.png"
                    await page.screenshot(path=str(ss))
                    # Production: Login to SOSDirect → File Form 205
                    # Fill: LLC name, agent, purpose, management, members
                    # Pay $300
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "TX SOSDirect requires account login. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "Form 205",
                        "state_fee": 300,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("TX", data, order_id, portal)

    async def _flow_florida(self, data: dict, order_id: str, portal: dict) -> dict:
        """Florida – Sunbiz e-filing."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://efile.sunbiz.org", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_FL_screenshot.png"
                    await page.screenshot(path=str(ss))
                    # Production: Select "Florida LLC" → fill all fields → pay $125
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "FL Sunbiz portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "Articles of Organization",
                        "state_fee": 125,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("FL", data, order_id, portal)

    async def _flow_new_york(self, data: dict, order_id: str, portal: dict) -> dict:
        """New York – DOS filing. Also requires newspaper publication."""
        return self._manual_fallback("NY", data, order_id, portal)

    async def _flow_delaware(self, data: dict, order_id: str, portal: dict) -> dict:
        """Delaware – Division of Corporations online filing."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://icis.corp.delaware.gov/ecorp/logintax.aspx", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_DE_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "DE portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "Certificate of Formation",
                        "state_fee": 90,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("DE", data, order_id, portal)

    async def _flow_wyoming(self, data: dict, order_id: str, portal: dict) -> dict:
        """Wyoming – WyoBiz online filing."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://wyobiz.wyo.gov/Business/FilingSearch.aspx", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_WY_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "WY WyoBiz portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "Articles of Organization",
                        "state_fee": 100,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("WY", data, order_id, portal)

    async def _flow_nevada(self, data: dict, order_id: str, portal: dict) -> dict:
        """Nevada – SilverFlume automated filing with hCaptcha solving."""
        try:
            from silverflume_filer import SilverFlumeFiler
            filer = SilverFlumeFiler()
            result = await filer.file_llc(data, order_id)
            
            if result.get("success"):
                return {
                    "success": True,
                    "status": "submitted",
                    "confirmation_number": result.get("confirmation_number", ""),
                    "portal_url": portal["portal_url"],
                    "state_fee": 425,
                    "screenshots": result.get("screenshots", []),
                    "timestamps": result.get("timestamps", {}),
                }
            else:
                return {
                    "success": False,
                    "needs_human_review": result.get("needs_human_review", True),
                    "reason": result.get("reason", "SilverFlume automation incomplete"),
                    "portal_url": portal["portal_url"],
                    "form": "Articles of Organization + Initial List + Business License",
                    "state_fee": 425,
                    "prepared_data": result.get("prepared_data", ""),
                    "errors": result.get("errors", []),
                    "screenshots": result.get("screenshots", []),
                }
        except ValueError as e:
            # Missing API key
            logger.warning(f"SilverFlume filer not configured: {e}")
            return self._manual_fallback("NV", data, order_id, portal)
        except Exception as e:
            logger.error(f"SilverFlume filer error: {e}")
            return self._manual_fallback("NV", data, order_id, portal)

    async def _flow_colorado(self, data: dict, order_id: str, portal: dict) -> dict:
        """Colorado – online only filing through SOS website."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://www.sos.state.co.us/pubs/business/businessHome.html", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_CO_screenshot.png"
                    await page.screenshot(path=str(ss))
                    # Production: Navigate to file new entity → LLC → fill form → pay $50
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "CO SOS portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": portal["form"],
                        "state_fee": 50,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("CO", data, order_id, portal)

    async def _flow_illinois(self, data: dict, order_id: str, portal: dict) -> dict:
        """Illinois – SOS LLC-5.5 online filing."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://www.ilsos.gov/corporatellc/", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_IL_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "IL SOS portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "LLC-5.5",
                        "state_fee": 150,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("IL", data, order_id, portal)

    async def _flow_georgia(self, data: dict, order_id: str, portal: dict) -> dict:
        """Georgia – eCorp online filing. Publication also required."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://ecorp.sos.ga.gov/BusinessSearch", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_GA_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "GA eCorp portal accessed. Data prepared. Publication also required.",
                        "portal_url": portal["portal_url"], "form": "Articles of Organization",
                        "state_fee": 100,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("GA", data, order_id, portal)

    async def _flow_washington(self, data: dict, order_id: str, portal: dict) -> dict:
        """Washington – CCFS online filing."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://ccfs.sos.wa.gov", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_WA_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "WA CCFS portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "Certificate of Formation",
                        "state_fee": 200,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("WA", data, order_id, portal)

    async def _flow_ohio(self, data: dict, order_id: str, portal: dict) -> dict:
        """Ohio – Ohio Business Central online filing."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://www.ohiobusinesscentral.gov/", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_OH_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "OH Business Central portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "Articles of Organization",
                        "state_fee": 99,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("OH", data, order_id, portal)

    async def _flow_north_carolina(self, data: dict, order_id: str, portal: dict) -> dict:
        """North Carolina – SOS online filing."""
        return self._manual_fallback("NC", data, order_id, portal)

    async def _flow_indiana(self, data: dict, order_id: str, portal: dict) -> dict:
        """Indiana – INBiz online filing."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://inbiz.in.gov/", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_IN_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "IN INBiz portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "Articles of Organization",
                        "state_fee": 95,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("IN", data, order_id, portal)

    async def _flow_new_jersey(self, data: dict, order_id: str, portal: dict) -> dict:
        """New Jersey – NJ Business Formation portal."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://www.njportal.com/DOR/BusinessFormation", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_NJ_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "NJ portal accessed. Data prepared.",
                        "portal_url": portal["portal_url"], "form": "Certificate of Formation",
                        "state_fee": 125,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("NJ", data, order_id, portal)

    async def _flow_arizona(self, data: dict, order_id: str, portal: dict) -> dict:
        """Arizona – ACC eCorp online filing. Publication also required."""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto("https://ecorp.azcc.gov/", timeout=30000)
                    ss = self.receipts_dir / f"{order_id}_AZ_screenshot.png"
                    await page.screenshot(path=str(ss))
                    return {
                        "success": False, "needs_human_review": True,
                        "reason": "AZ ACC portal accessed. Data prepared. Newspaper publication required within 60 days.",
                        "portal_url": portal["portal_url"], "form": "Articles of Organization",
                        "state_fee": 50,
                    }
                finally:
                    await browser.close()
        except (ImportError, Exception):
            return self._manual_fallback("AZ", data, order_id, portal)

    # ── Mail filing (PDF generation) ────────────────────────────────────────

    async def _prepare_mail_filing(self, state: str, data: dict, order_id: str, portal: dict) -> dict:
        """Generate pre-filled PDF forms and mailing instructions for mail-only states."""
        try:
            pdf_path = await self._generate_filing_pdf(state, data, order_id, portal)
            return {
                "success": False,
                "needs_human_review": True,
                "status": STATUS_HUMAN_REVIEW,
                "reason": f"{state} requires mail filing. PDF prepared and ready to print.",
                "state": state,
                "form": portal["form"],
                "state_fee": portal["fee"],
                "mailing_address": portal.get("mailing_address", f"{portal['office']}, {portal['name']}"),
                "pdf_path": str(pdf_path) if pdf_path else None,
                "check_payable_to": portal["office"],
                "notes": portal.get("notes", ""),
                "order_id": order_id,
                "queued_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Mail filing prep error for {state}: {e}")
            return self._manual_fallback(state, data, order_id, portal)

    async def _generate_filing_pdf(self, state: str, data: dict, order_id: str, portal: dict) -> Optional[Path]:
        """Generate a pre-filled PDF for mail filing using ReportLab."""
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import inch

            pdf_path = self.receipts_dir / f"{order_id}_{state}_filing_form.pdf"
            c = canvas.Canvas(str(pdf_path), pagesize=letter)
            width, height = letter

            # Header
            c.setFont("Helvetica-Bold", 16)
            c.drawString(1 * inch, height - 1 * inch, f"LLC Formation Filing — {portal['name']}")
            c.setFont("Helvetica", 11)
            c.drawString(1 * inch, height - 1.3 * inch, f"Form: {portal['form']}")
            c.drawString(1 * inch, height - 1.55 * inch, f"Order: {order_id}")
            c.drawString(1 * inch, height - 1.8 * inch, f"Date: {datetime.utcnow().strftime('%B %d, %Y')}")

            # Business info
            y = height - 2.5 * inch
            c.setFont("Helvetica-Bold", 12)
            c.drawString(1 * inch, y, "Business Information")
            y -= 0.3 * inch
            c.setFont("Helvetica", 10)

            fields = [
                ("LLC Name", data.get("business_name", "")),
                ("State", state),
                ("Entity Type", data.get("entity_type", "LLC")),
                ("Purpose", data.get("purpose", "Any lawful purpose")),
                ("Management", data.get("management_type", "member-managed")),
            ]

            # Members
            members = data.get("members", [])
            if members:
                for i, m in enumerate(members):
                    if isinstance(m, dict):
                        fields.append((f"Member {i+1}", f"{m.get('name', '')} — {m.get('address', '')}"))

            for label, value in fields:
                c.drawString(1 * inch, y, f"{label}: {value}")
                y -= 0.22 * inch
                if y < 2 * inch:
                    c.showPage()
                    y = height - 1 * inch

            # Mailing instructions
            y -= 0.4 * inch
            c.setFont("Helvetica-Bold", 12)
            c.drawString(1 * inch, y, "Mailing Instructions")
            y -= 0.3 * inch
            c.setFont("Helvetica", 10)
            c.drawString(1 * inch, y, f"Mail to: {portal.get('mailing_address', portal['office'])}")
            y -= 0.22 * inch
            c.drawString(1 * inch, y, f"Filing fee: ${portal['fee']} (check payable to: {portal['office']})")
            y -= 0.22 * inch
            if portal.get("notes"):
                c.drawString(1 * inch, y, f"Notes: {portal['notes']}")

            c.save()
            return pdf_path

        except ImportError:
            logger.warning("reportlab not available for PDF generation")
            return None
        except Exception as e:
            logger.error(f"PDF generation error: {e}")
            return None

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _manual_fallback(self, state: str, data: dict, order_id: str, portal: dict) -> dict:
        """Fallback when automation isn't possible."""
        return {
            "success": False,
            "needs_human_review": True,
            "status": STATUS_HUMAN_REVIEW,
            "reason": f"Automated filing for {state} queued for human review. All data prepared.",
            "state": state,
            "portal_url": portal["portal_url"],
            "form": portal["form"],
            "state_fee": portal["fee"],
            "business_name": data.get("business_name", ""),
            "entity_type": data.get("entity_type", "LLC"),
            "order_id": order_id,
            "queued_at": datetime.utcnow().isoformat(),
        }

    def _save_receipt(self, order_id: str, state: str, result: dict):
        """Save filing receipt to disk."""
        receipt_path = self.receipts_dir / f"{order_id}_{state}_receipt.json"
        receipt = {
            "order_id": order_id,
            "state": state,
            "filed_at": datetime.utcnow().isoformat(),
            **result,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2))

    @staticmethod
    def get_portal_info(state: str) -> Optional[dict]:
        """Return portal metadata for a given state."""
        return STATE_PORTALS.get(state.upper())

    @staticmethod
    def get_all_states() -> dict:
        """Return all state portal info."""
        return STATE_PORTALS
