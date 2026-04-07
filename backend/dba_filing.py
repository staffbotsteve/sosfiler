"""
SOSFiler — DBA Filing Automation
Handles state-level and county-level DBA/Fictitious Business Name filings.
Includes newspaper publication support for states that require it.
"""

import os
import json
import uuid
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sosfiler.db"
DATA_DIR = BASE_DIR / "data"

# DBA Filing statuses
DBA_STATUS_PENDING = "pending"
DBA_STATUS_PREPARING = "preparing"
DBA_STATUS_FILING = "filing"
DBA_STATUS_PUBLICATION_PENDING = "publication_pending"
DBA_STATUS_PUBLICATION_SUBMITTED = "publication_submitted"
DBA_STATUS_COMPLETE = "complete"
DBA_STATUS_FAILED = "failed"
DBA_STATUS_HUMAN_REVIEW = "human_review"

# Top 25 counties by population for county-level automation
TOP_25_COUNTIES = {
    "CA": [
        {"county": "Los Angeles", "clerk_url": "https://www.lavote.gov/home/county-clerk/fictitious-business-names", "fee": 26},
        {"county": "San Diego", "clerk_url": "https://arcc.sdcounty.ca.gov/pages/fictitiousbusinessnames.aspx", "fee": 26},
        {"county": "Orange", "clerk_url": "https://www.ocrecorder.com/services/fictitious-business-names", "fee": 26},
        {"county": "Riverside", "clerk_url": "https://www.asrclkrec.com/recorder/fictitious-business-name", "fee": 26},
        {"county": "San Bernardino", "clerk_url": "https://www.sbcounty.gov/arc/fictitious-business-name/", "fee": 26},
    ],
    "TX": [
        {"county": "Harris", "clerk_url": "https://www.cclerk.hctx.net/assumed-names", "fee": 25},
        {"county": "Dallas", "clerk_url": "https://www.dallascounty.org/departments/countyclerk/assumed-name.php", "fee": 25},
        {"county": "Tarrant", "clerk_url": "https://www.tarrantcounty.com/en/county-clerk/assumed-name.html", "fee": 25},
        {"county": "Bexar", "clerk_url": "https://www.bexar.org/1664/Assumed-Name-Certificate", "fee": 25},
        {"county": "Travis", "clerk_url": "https://www.traviscountyclerk.org/assumed-name-certificates", "fee": 25},
    ],
    "FL": [
        {"county": "Miami-Dade", "clerk_url": "https://www.miamidadeclerk.gov/", "fee": 50, "note": "FL DBAs filed at state level via Sunbiz"},
    ],
    "NY": [
        {"county": "New York (Manhattan)", "clerk_url": "https://www.manhattancountyclerk.com/", "fee": 25},
        {"county": "Kings (Brooklyn)", "clerk_url": "https://www.kingscountyclerk.com/", "fee": 25},
        {"county": "Queens", "clerk_url": "https://www.queenscountyclerk.com/", "fee": 25},
        {"county": "Suffolk", "clerk_url": "https://www.suffolkcountyny.gov/Departments/County-Clerk", "fee": 25},
        {"county": "Nassau", "clerk_url": "https://www.nassaucountyny.gov/480/County-Clerk", "fee": 25},
    ],
    "IL": [
        {"county": "Cook", "clerk_url": "https://www.cookcountyclerkil.gov/service/assumed-name-business-registration", "fee": 25},
        {"county": "DuPage", "clerk_url": "https://www.dupagecounty.gov/county_clerk/", "fee": 25},
    ],
    "PA": [
        {"county": "Philadelphia", "clerk_url": "https://www.phila.gov/services/business-self-employment/business-taxes/get-a-fictitious-name-registration/", "fee": 70},
        {"county": "Allegheny", "clerk_url": "https://www.alleghenycounty.us/real-estate/fictitious-names.aspx", "fee": 70},
    ],
    "AZ": [
        {"county": "Maricopa", "clerk_url": "https://recorder.maricopa.gov/", "fee": 10},
    ],
    "NV": [
        {"county": "Clark", "clerk_url": "https://www.clarkcountynv.gov/government/departments/county_clerk/index.php", "fee": 25},
    ],
    "GA": [
        {"county": "Fulton", "clerk_url": "https://www.fultoncountyga.gov/services/clerk-of-superior-court", "fee": 25},
    ],
    "OH": [
        {"county": "Cuyahoga", "clerk_url": "https://fiscalofficer.cuyahogacounty.us/", "fee": 39},
        {"county": "Franklin", "clerk_url": "https://recorder.franklincountyohio.gov/", "fee": 39},
    ],
}

# States requiring newspaper publication for DBA
PUBLICATION_STATES = {
    "AZ": {
        "required": True,
        "duration": "3 consecutive weeks",
        "timing": "After filing with county recorder",
    },
    "CA": {
        "required": True,
        "duration": "4 consecutive weeks",
        "timing": "Within 30 days of filing. Must file proof of publication with county clerk.",
    },
    "GA": {
        "required": True,
        "duration": "2 consecutive weeks",
        "timing": "Publish in county legal organ.",
    },
    "IL": {
        "required": True,
        "duration": "3 consecutive weeks",
        "timing": "After filing with county clerk.",
    },
    "NE": {
        "required": True,
        "duration": "3 consecutive weeks",
        "timing": "Publish in legal newspaper in county.",
    },
    "NY": {
        "required": True,
        "duration": "6 consecutive weeks",
        "timing": "Within 120 days of formation. 2 newspapers (1 daily, 1 weekly) designated by county clerk.",
    },
    "PA": {
        "required": True,
        "duration": "1 time in 2 newspapers",
        "timing": "1 newspaper of general circulation + 1 legal journal in county.",
    },
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_dba_tables():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dba_filings (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            business_name TEXT NOT NULL,
            dba_name TEXT NOT NULL,
            state TEXT NOT NULL,
            county TEXT,
            city TEXT,
            filed_at_level TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            filing_fee INTEGER DEFAULT 0,
            platform_fee INTEGER DEFAULT 2900,
            portal_url TEXT,
            form_data TEXT,
            publication_required INTEGER DEFAULT 0,
            publication_status TEXT,
            publication_newspaper TEXT,
            publication_text TEXT,
            filing_confirmation TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS dba_status_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dba_filing_id TEXT NOT NULL REFERENCES dba_filings(id),
            status TEXT NOT NULL,
            message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_dba_filings_email ON dba_filings(email);
        CREATE INDEX IF NOT EXISTS idx_dba_filings_status ON dba_filings(status);
    """)
    conn.commit()
    conn.close()


_init_dba_tables()


class DBAFiler:
    """Handles DBA/Fictitious Business Name filings."""

    def __init__(self):
        self.dba_data = self._load_dba_requirements()
        self.receipts_dir = BASE_DIR / "filing_receipts"
        self.receipts_dir.mkdir(exist_ok=True)

    def _load_dba_requirements(self) -> dict:
        path = DATA_DIR / "dba_requirements.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    def create_filing(
        self,
        email: str,
        business_name: str,
        dba_name: str,
        state: str,
        county: str = "",
        city: str = "",
    ) -> dict:
        """Create a new DBA filing order."""
        state = state.upper()
        filing_id = f"DBA-{uuid.uuid4().hex[:12].upper()}"

        reqs = self.dba_data.get("dba_requirements", {}).get(state, {})
        filed_at = reqs.get("filed_at", "county")
        filing_fee = reqs.get("filing_fee", 25)
        pub_required = reqs.get("publication_required", False)
        portal_url = reqs.get("portal_url", "")

        conn = _get_db()
        conn.execute("""
            INSERT INTO dba_filings (id, email, business_name, dba_name, state, county, city,
                                     filed_at_level, filing_fee, portal_url, publication_required)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (filing_id, email, business_name, dba_name, state, county, city,
              filed_at, filing_fee * 100, portal_url, 1 if pub_required else 0))
        conn.execute(
            "INSERT INTO dba_status_updates (dba_filing_id, status, message) VALUES (?, ?, ?)",
            (filing_id, DBA_STATUS_PENDING, "DBA filing created. Awaiting payment.")
        )
        conn.commit()
        conn.close()

        return {
            "filing_id": filing_id,
            "dba_name": dba_name,
            "state": state,
            "state_name": reqs.get("state_name", state),
            "filed_at_level": filed_at,
            "filing_fee": filing_fee,
            "platform_fee": 29,
            "total": filing_fee + 29,
            "publication_required": pub_required,
            "publication_details": reqs.get("publication_details", ""),
            "portal_url": portal_url,
            "status": DBA_STATUS_PENDING,
        }

    def get_filing_status(self, filing_id: str) -> Optional[dict]:
        """Get status of a DBA filing."""
        conn = _get_db()
        filing = conn.execute("SELECT * FROM dba_filings WHERE id = ?", (filing_id,)).fetchone()
        if not filing:
            conn.close()
            return None

        updates = conn.execute(
            "SELECT status, message, created_at FROM dba_status_updates WHERE dba_filing_id = ? ORDER BY created_at",
            (filing_id,)
        ).fetchall()
        conn.close()

        return {
            "filing_id": filing["id"],
            "dba_name": filing["dba_name"],
            "business_name": filing["business_name"],
            "state": filing["state"],
            "county": filing["county"],
            "status": filing["status"],
            "filing_fee": filing["filing_fee"] / 100,
            "platform_fee": filing["platform_fee"] / 100,
            "publication_required": bool(filing["publication_required"]),
            "publication_status": filing["publication_status"],
            "timeline": [dict(u) for u in updates],
            "created_at": filing["created_at"],
        }

    async def process_filing(self, filing_id: str) -> dict:
        """Process a DBA filing — automation or manual prep."""
        conn = _get_db()
        filing = conn.execute("SELECT * FROM dba_filings WHERE id = ?", (filing_id,)).fetchone()
        conn.close()

        if not filing:
            return {"success": False, "error": "Filing not found"}

        state = filing["state"]
        reqs = self.dba_data.get("dba_requirements", {}).get(state, {})

        self._update_status(filing_id, DBA_STATUS_PREPARING, "Preparing DBA filing documents...")

        # State-level online filing
        if reqs.get("online_filing") and reqs.get("portal_url"):
            result = await self._file_online(filing, reqs)
        # County-level — check if we have automation for this county
        elif reqs.get("filed_at") in ("county", "city"):
            result = await self._file_county(filing, reqs)
        else:
            result = self._prepare_manual(filing, reqs)

        # Handle publication if required
        if reqs.get("publication_required"):
            pub_info = self._prepare_publication(filing, state)
            result["publication"] = pub_info

        return result

    async def _file_online(self, filing: dict, reqs: dict) -> dict:
        """Attempt online DBA filing via Playwright."""
        state = filing["state"]
        portal = reqs.get("portal_url", "")
        self._update_status(filing["id"], DBA_STATUS_FILING, f"Filing DBA online via {portal}")

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await (await browser.new_context()).new_page()
                try:
                    await page.goto(portal, timeout=30000)
                    ss = self.receipts_dir / f"{filing['id']}_{state}_dba_screenshot.png"
                    await page.screenshot(path=str(ss))

                    # State-specific flows would go here
                    # For now, queue for human review with screenshot
                    self._update_status(filing["id"], DBA_STATUS_HUMAN_REVIEW,
                                       f"Portal accessed. DBA data prepared for filing at {portal}")
                    return {
                        "success": False,
                        "status": DBA_STATUS_HUMAN_REVIEW,
                        "portal_url": portal,
                        "screenshot": str(ss),
                        "message": f"Online DBA portal for {state} accessed. Data prepared for human completion.",
                    }
                finally:
                    await browser.close()

        except (ImportError, Exception) as e:
            logger.error(f"Online DBA filing error for {state}: {e}")
            return self._prepare_manual(filing, reqs)

    async def _file_county(self, filing: dict, reqs: dict) -> dict:
        """Handle county-level DBA filing."""
        state = filing["state"]
        county = filing["county"]

        # Check if we have automation for this specific county
        state_counties = TOP_25_COUNTIES.get(state, [])
        county_info = None
        for c in state_counties:
            if c["county"].lower() == county.lower():
                county_info = c
                break

        if county_info and county_info.get("clerk_url"):
            # Try automated filing for known counties
            try:
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await (await browser.new_context()).new_page()
                    try:
                        await page.goto(county_info["clerk_url"], timeout=30000)
                        ss = self.receipts_dir / f"{filing['id']}_{state}_{county}_dba.png"
                        await page.screenshot(path=str(ss))
                        self._update_status(filing["id"], DBA_STATUS_HUMAN_REVIEW,
                                           f"County clerk portal accessed for {county} County, {state}")
                        return {
                            "success": False,
                            "status": DBA_STATUS_HUMAN_REVIEW,
                            "county": county,
                            "clerk_url": county_info["clerk_url"],
                            "fee": county_info.get("fee", reqs.get("filing_fee", 25)),
                            "screenshot": str(ss),
                        }
                    finally:
                        await browser.close()
            except (ImportError, Exception):
                pass

        # No automation — generate pre-filled forms
        return self._prepare_manual(filing, reqs)

    def _prepare_manual(self, filing: dict, reqs: dict) -> dict:
        """Generate pre-filled PDF and mailing instructions."""
        state = filing["state"]
        county = filing["county"] or "your county"
        filed_at = reqs.get("filed_at", "county")

        self._update_status(filing["id"], DBA_STATUS_HUMAN_REVIEW,
                           f"Preparing pre-filled DBA forms for {county}, {state}")

        # Generate PDF
        pdf_path = self._generate_dba_pdf(filing, reqs)

        mailing_info = {
            "filed_at": filed_at,
            "office": f"{county} County Clerk" if filed_at == "county" else f"{state} Secretary of State",
            "fee": reqs.get("filing_fee", 25),
            "check_payable_to": f"{county} County Clerk" if filed_at == "county" else reqs.get("state_name", state),
            "notes": reqs.get("notes", ""),
        }

        return {
            "success": True,
            "status": DBA_STATUS_HUMAN_REVIEW,
            "filing_method": "mail",
            "pdf_path": str(pdf_path) if pdf_path else None,
            "mailing_info": mailing_info,
            "message": f"Pre-filled DBA forms generated. Print, sign, and mail with a check for ${reqs.get('filing_fee', 25)}.",
        }

    def _generate_dba_pdf(self, filing: dict, reqs: dict) -> Optional[Path]:
        """Generate pre-filled DBA filing PDF."""
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import inch

            pdf_path = self.receipts_dir / f"{filing['id']}_dba_form.pdf"
            c = canvas.Canvas(str(pdf_path), pagesize=letter)
            w, h = letter

            c.setFont("Helvetica-Bold", 18)
            c.drawString(1 * inch, h - 1 * inch, "DBA / Fictitious Business Name Filing")

            c.setFont("Helvetica", 12)
            c.drawString(1 * inch, h - 1.4 * inch, f"State: {reqs.get('state_name', filing['state'])}")
            c.drawString(1 * inch, h - 1.65 * inch, f"Filing ID: {filing['id']}")
            c.drawString(1 * inch, h - 1.9 * inch, f"Date: {datetime.utcnow().strftime('%B %d, %Y')}")

            y = h - 2.5 * inch
            c.setFont("Helvetica-Bold", 14)
            c.drawString(1 * inch, y, "Business Information")
            y -= 0.35 * inch
            c.setFont("Helvetica", 11)

            fields = [
                ("Legal Business Name", filing["business_name"]),
                ("DBA / Fictitious Name", filing["dba_name"]),
                ("State", filing["state"]),
                ("County", filing.get("county", "N/A")),
                ("City", filing.get("city", "N/A")),
            ]

            for label, value in fields:
                c.drawString(1 * inch, y, f"{label}: {value}")
                y -= 0.25 * inch

            # Filing instructions
            y -= 0.4 * inch
            c.setFont("Helvetica-Bold", 14)
            c.drawString(1 * inch, y, "Filing Instructions")
            y -= 0.35 * inch
            c.setFont("Helvetica", 11)

            filed_at = reqs.get("filed_at", "county")
            c.drawString(1 * inch, y, f"File with: {filing.get('county', '')} {filed_at.title()} Clerk")
            y -= 0.25 * inch
            c.drawString(1 * inch, y, f"Filing fee: ${reqs.get('filing_fee', 25)}")
            y -= 0.25 * inch
            if reqs.get("notes"):
                c.drawString(1 * inch, y, f"Notes: {reqs['notes']}")
                y -= 0.25 * inch

            if reqs.get("publication_required"):
                y -= 0.3 * inch
                c.setFont("Helvetica-Bold", 12)
                c.drawString(1 * inch, y, "⚠ PUBLICATION REQUIRED")
                y -= 0.25 * inch
                c.setFont("Helvetica", 10)
                c.drawString(1 * inch, y, reqs.get("publication_details", "Check with your county clerk for publication requirements."))

            c.save()
            return pdf_path

        except ImportError:
            logger.warning("reportlab not available for DBA PDF generation")
            return None
        except Exception as e:
            logger.error(f"DBA PDF generation error: {e}")
            return None

    def _prepare_publication(self, filing: dict, state: str) -> dict:
        """Prepare newspaper publication info for states that require it."""
        pub_info = PUBLICATION_STATES.get(state)
        if not pub_info:
            return {"required": False}

        dba_name = filing["dba_name"]
        business_name = filing["business_name"]
        county = filing.get("county", "")
        city = filing.get("city", "")

        # Generate publication text
        pub_text = self._generate_publication_text(dba_name, business_name, state, county, city)

        # Find local legal newspapers (common ones per state)
        newspapers = self._find_legal_newspapers(state, county)

        return {
            "required": True,
            "state": state,
            "duration": pub_info["duration"],
            "timing": pub_info["timing"],
            "publication_text": pub_text,
            "suggested_newspapers": newspapers,
            "instructions": (
                f"1. Contact one of the suggested newspapers below.\n"
                f"2. Request to publish the following notice for {pub_info['duration']}.\n"
                f"3. After publication completes, obtain a Proof of Publication affidavit.\n"
                f"4. File the Proof of Publication with your county clerk."
            ),
        }

    def _generate_publication_text(self, dba_name: str, business_name: str,
                                    state: str, county: str, city: str) -> str:
        """Generate standard DBA publication notice text."""
        location = f"{city}, {county} County, {state}" if county else f"{city}, {state}"
        return (
            f"FICTITIOUS BUSINESS NAME STATEMENT\n\n"
            f"The following person(s) is/are doing business as:\n"
            f"{dba_name}\n"
            f"Located at: {location}\n\n"
            f"This business is conducted by: {business_name}\n\n"
            f"This statement was filed with the County Clerk of {county} County "
            f"on {datetime.utcnow().strftime('%B %d, %Y')}.\n\n"
            f"(Filed by SOSFiler on behalf of the registrant)"
        )

    def _find_legal_newspapers(self, state: str, county: str) -> list:
        """Return suggested legal newspapers for the state/county."""
        # Common legal newspapers by state (expandable)
        newspapers = {
            "CA": [
                {"name": "Daily Journal", "url": "https://www.dailyjournal.com/", "type": "daily"},
                {"name": "Los Angeles Daily News", "url": "https://www.dailynews.com/", "type": "daily", "counties": ["Los Angeles"]},
                {"name": "San Diego Union-Tribune", "url": "https://www.sandiegouniontribune.com/", "type": "daily", "counties": ["San Diego"]},
                {"name": "San Francisco Chronicle", "url": "https://www.sfchronicle.com/", "type": "daily", "counties": ["San Francisco"]},
            ],
            "NY": [
                {"name": "New York Law Journal", "url": "https://www.law.com/newyorklawjournal/", "type": "daily"},
                {"name": "The Chief-Leader", "url": "https://thechiefleader.com/", "type": "weekly"},
                {"name": "New York Post", "url": "https://nypost.com/", "type": "daily"},
            ],
            "IL": [
                {"name": "Chicago Daily Law Bulletin", "url": "https://www.chicagolawbulletin.com/", "type": "daily"},
            ],
            "GA": [
                {"name": "Daily Report", "url": "https://www.law.com/dailyreportonline/", "type": "daily", "counties": ["Fulton"]},
            ],
            "AZ": [
                {"name": "Arizona Capitol Times", "url": "https://azcapitoltimes.com/", "type": "weekly"},
            ],
            "NE": [
                {"name": "Daily Record", "url": "https://www.omahadailyrecord.com/", "type": "daily"},
            ],
            "PA": [
                {"name": "The Legal Intelligencer", "url": "https://www.law.com/thelegalintelligencer/", "type": "daily"},
            ],
        }

        state_papers = newspapers.get(state, [])
        if county:
            # Filter to county-specific if available, otherwise return all
            county_specific = [p for p in state_papers if not p.get("counties") or county in p.get("counties", [])]
            return county_specific if county_specific else state_papers
        return state_papers

    def _update_status(self, filing_id: str, status: str, message: str = ""):
        conn = _get_db()
        conn.execute(
            "INSERT INTO dba_status_updates (dba_filing_id, status, message) VALUES (?, ?, ?)",
            (filing_id, status, message)
        )
        conn.execute(
            "UPDATE dba_filings SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, filing_id)
        )
        conn.commit()
        conn.close()
