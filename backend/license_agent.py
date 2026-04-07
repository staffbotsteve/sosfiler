"""
SOSFiler — AI-Powered License Agent
Handles DBA filings, business licenses, and specialty license lookups.
Uses OpenAI GPT-4o + cached SQLite results.
"""

import os
import json
import sqlite3
import hashlib
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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Response types
ONLINE_FILING = "ONLINE_FILING"
MAIL_FILING = "MAIL_FILING"
GUIDANCE = "GUIDANCE"

# License types we support
SUPPORTED_LICENSE_TYPES = [
    "dba",
    "general_business_license",
    "liquor_license",
    "food_beverage",
    "str_license",
    "professional_license",
    "home_occupation",
    "sales_tax_permit",
    "health_department",
    "building_zoning",
    "signage_permit",
    "special_event",
    "cannabis_license",
    "childcare_license",
    "auto_dealer",
]

# Pricing
PRICING = {
    "dba": 2900,             # $29
    "guidance": 0,           # Free
    "license_filing": 4900,  # $49
    "specialty": 9900,       # $99
    "report": 0,             # Free
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_license_tables():
    """Create license_cache and license_filings tables if not exist."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS license_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT UNIQUE NOT NULL,
            city TEXT,
            county TEXT,
            state TEXT NOT NULL,
            business_type TEXT,
            license_type TEXT NOT NULL,
            response_type TEXT NOT NULL,
            response_data TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS license_filings (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            license_type TEXT NOT NULL,
            city TEXT,
            county TEXT,
            state TEXT NOT NULL,
            business_type TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            response_type TEXT,
            response_data TEXT,
            stripe_payment_intent TEXT,
            fee_cents INTEGER DEFAULT 0,
            platform_fee_cents INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_license_cache_key ON license_cache(cache_key);
        CREATE INDEX IF NOT EXISTS idx_license_filings_email ON license_filings(email);
        CREATE INDEX IF NOT EXISTS idx_license_filings_status ON license_filings(status);
    """)
    conn.commit()
    conn.close()


_init_license_tables()


def _cache_key(city: str, county: str, state: str, business_type: str, license_type: str) -> str:
    """Generate a deterministic cache key."""
    raw = f"{city.lower().strip()}|{county.lower().strip()}|{state.upper().strip()}|{business_type.lower().strip()}|{license_type.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _get_cached(city: str, county: str, state: str, business_type: str, license_type: str) -> Optional[dict]:
    """Check cache for existing lookup result."""
    key = _cache_key(city, county, state, business_type, license_type)
    conn = _get_db()
    row = conn.execute("SELECT * FROM license_cache WHERE cache_key = ?", (key,)).fetchone()
    conn.close()
    if row:
        return {
            "response_type": row["response_type"],
            "data": json.loads(row["response_data"]),
            "cached": True,
            "cached_at": row["updated_at"],
        }
    return None


def _save_cache(city: str, county: str, state: str, business_type: str,
                license_type: str, response_type: str, response_data: dict):
    """Save lookup result to cache."""
    key = _cache_key(city, county, state, business_type, license_type)
    conn = _get_db()
    conn.execute("""
        INSERT INTO license_cache (cache_key, city, county, state, business_type, license_type, response_type, response_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            response_type = excluded.response_type,
            response_data = excluded.response_data,
            updated_at = datetime('now')
    """, (key, city, county, state, business_type, license_type, response_type, json.dumps(response_data)))
    conn.commit()
    conn.close()


class LicenseAgent:
    """AI-powered licensing assistant."""

    def __init__(self):
        self.license_types_data = self._load_license_types()
        self.dba_data = self._load_dba_requirements()

    def _load_license_types(self) -> dict:
        path = DATA_DIR / "license_types.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    def _load_dba_requirements(self) -> dict:
        path = DATA_DIR / "dba_requirements.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}

    async def check_license(
        self,
        city: str,
        county: str,
        state: str,
        business_type: str,
        license_type: str,
    ) -> dict:
        """
        Main entry point: check license requirements for a jurisdiction.
        Returns one of: ONLINE_FILING, MAIL_FILING, GUIDANCE
        """
        state = state.upper().strip()
        city = city.strip()
        county = county.strip()
        business_type = business_type.strip()
        license_type = license_type.lower().strip()

        # Check cache first
        cached = _get_cached(city, county, state, business_type, license_type)
        if cached:
            logger.info(f"Cache hit for {license_type} in {city}, {state}")
            return cached

        # For DBA, use our structured data first
        if license_type == "dba":
            result = self._check_dba(city, county, state)
            _save_cache(city, county, state, business_type, license_type, result["response_type"], result["data"])
            return result

        # For other licenses, use AI lookup
        result = await self._ai_license_lookup(city, county, state, business_type, license_type)
        _save_cache(city, county, state, business_type, license_type, result["response_type"], result["data"])
        return result

    def _check_dba(self, city: str, county: str, state: str) -> dict:
        """Check DBA requirements using structured data."""
        dba_reqs = self.dba_data.get("dba_requirements", {}).get(state)
        if not dba_reqs:
            return {
                "response_type": GUIDANCE,
                "data": {
                    "license_type": "DBA / Fictitious Business Name",
                    "state": state,
                    "message": f"DBA requirements for {state} not found in our database. Please check with your local county clerk or Secretary of State.",
                    "our_fee": PRICING["dba"] / 100,
                },
            }

        if dba_reqs.get("online_filing") and dba_reqs.get("portal_url"):
            return {
                "response_type": ONLINE_FILING,
                "data": {
                    "license_type": "DBA / Fictitious Business Name",
                    "state": state,
                    "state_name": dba_reqs["state_name"],
                    "filed_at": dba_reqs["filed_at"],
                    "filing_fee": dba_reqs["filing_fee"],
                    "portal_url": dba_reqs["portal_url"],
                    "form_name": dba_reqs.get("form_name", ""),
                    "publication_required": dba_reqs.get("publication_required", False),
                    "publication_details": dba_reqs.get("publication_details", ""),
                    "renewal_period": dba_reqs.get("renewal_period", "varies"),
                    "our_fee": PRICING["dba"] / 100,
                    "total_estimated": dba_reqs["filing_fee"] + PRICING["dba"] / 100,
                    "can_automate": True,
                },
            }

        if dba_reqs["filed_at"] in ("county", "city"):
            return {
                "response_type": MAIL_FILING,
                "data": {
                    "license_type": "DBA / Fictitious Business Name",
                    "state": state,
                    "state_name": dba_reqs["state_name"],
                    "filed_at": dba_reqs["filed_at"],
                    "filing_fee": dba_reqs["filing_fee"],
                    "publication_required": dba_reqs.get("publication_required", False),
                    "publication_details": dba_reqs.get("publication_details", ""),
                    "renewal_period": dba_reqs.get("renewal_period", "varies"),
                    "notes": dba_reqs.get("notes", ""),
                    "our_fee": PRICING["dba"] / 100,
                    "total_estimated": dba_reqs["filing_fee"] + PRICING["dba"] / 100,
                    "message": f"DBA in {dba_reqs['state_name']} is filed at the {dba_reqs['filed_at']} level. We'll prepare pre-filled forms and mailing instructions for your specific {dba_reqs['filed_at']}.",
                    "can_automate": False,
                },
            }

        return {
            "response_type": GUIDANCE,
            "data": {
                "license_type": "DBA / Fictitious Business Name",
                "state": state,
                "state_name": dba_reqs["state_name"],
                "filed_at": dba_reqs["filed_at"],
                "filing_fee": dba_reqs["filing_fee"],
                "notes": dba_reqs.get("notes", ""),
                "our_fee": PRICING["dba"] / 100,
            },
        }

    async def _ai_license_lookup(
        self, city: str, county: str, state: str, business_type: str, license_type: str
    ) -> dict:
        """Use OpenAI GPT-4o to look up jurisdiction-specific license requirements."""
        if not OPENAI_API_KEY:
            return self._fallback_license_info(city, county, state, business_type, license_type)

        try:
            import openai
            client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

            # Find license type display name
            lt_info = None
            for lt in self.license_types_data.get("license_types", []):
                if lt["id"] == license_type:
                    lt_info = lt
                    break
            lt_name = lt_info["name"] if lt_info else license_type

            location = f"{city}, {county} County, {state}" if county else f"{city}, {state}"

            prompt = f"""You are a business licensing expert. Look up the specific requirements for obtaining a {lt_name} in {location} for a {business_type} business.

Return a JSON object with these fields:
{{
  "license_name": "official name of this license/permit",
  "issuing_authority": "exact name of the issuing department/agency",
  "authority_level": "state" or "county" or "city",
  "filing_method": "online" or "mail" or "in_person" or "mixed",
  "portal_url": "URL if online filing is available, else null",
  "form_name": "name/number of the application form",
  "filing_fee": estimated fee as number,
  "processing_time": "typical processing time",
  "requirements": ["list", "of", "specific", "requirements"],
  "documents_needed": ["list", "of", "required", "documents"],
  "office_address": "physical address of the issuing office",
  "phone": "office phone number",
  "hours": "office hours",
  "additional_notes": "any important notes, restrictions, or special requirements",
  "renewal_required": true/false,
  "renewal_period": "renewal period if applicable",
  "inspection_required": true/false
}}

Be specific to the exact jurisdiction. Use real URLs and contact info where possible. If you're uncertain about specific details, indicate that in additional_notes."""

            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a business licensing expert. Return only valid JSON. No markdown code fences."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1500,
            )

            content = response.choices[0].message.content.strip()
            # Strip potential markdown fences
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            info = json.loads(content)

            # Determine response type
            if info.get("filing_method") == "online" and info.get("portal_url"):
                resp_type = ONLINE_FILING
            elif info.get("filing_method") in ("mail", "in_person"):
                resp_type = MAIL_FILING
            else:
                resp_type = GUIDANCE

            # Determine our fee
            specialty_types = {"liquor_license", "food_beverage", "str_license", "cannabis_license",
                              "childcare_license", "auto_dealer", "professional_license"}
            if license_type in specialty_types:
                our_fee = PRICING["specialty"] / 100
            elif license_type == "dba":
                our_fee = PRICING["dba"] / 100
            else:
                our_fee = PRICING["license_filing"] / 100 if resp_type != GUIDANCE else 0

            return {
                "response_type": resp_type,
                "data": {
                    "license_type": lt_name,
                    "location": location,
                    "state": state,
                    **info,
                    "our_fee": our_fee,
                    "total_estimated": (info.get("filing_fee") or 0) + our_fee,
                },
            }

        except json.JSONDecodeError as e:
            logger.error(f"AI response parse error: {e}")
            return self._fallback_license_info(city, county, state, business_type, license_type)
        except Exception as e:
            logger.error(f"AI license lookup error: {e}")
            return self._fallback_license_info(city, county, state, business_type, license_type)

    def _fallback_license_info(self, city: str, county: str, state: str,
                                business_type: str, license_type: str) -> dict:
        """Fallback when AI is unavailable — return general guidance."""
        lt_info = None
        for lt in self.license_types_data.get("license_types", []):
            if lt["id"] == license_type:
                lt_info = lt
                break

        location = f"{city}, {county} County, {state}" if county else f"{city}, {state}"

        return {
            "response_type": GUIDANCE,
            "data": {
                "license_type": lt_info["name"] if lt_info else license_type,
                "location": location,
                "state": state,
                "authority_level": lt_info.get("authority_level", "varies") if lt_info else "varies",
                "typical_fee_range": lt_info.get("typical_fee_range", [0, 500]) if lt_info else [0, 500],
                "typical_processing_time": lt_info.get("typical_processing_time", "varies") if lt_info else "varies",
                "common_requirements": lt_info.get("common_requirements", []) if lt_info else [],
                "message": f"We couldn't perform a real-time lookup for {license_type} in {location}. Here's general information based on typical requirements. Contact your local government for exact details.",
                "our_fee": 0,
            },
        }

    async def what_licenses_do_i_need(self, city: str, state: str, business_type: str) -> dict:
        """
        AI-powered wizard: given a city, state, and business type,
        return a comprehensive list of likely required licenses.
        """
        if not OPENAI_API_KEY:
            return self._fallback_license_list(city, state, business_type)

        try:
            import openai
            client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)

            prompt = f"""You are a business licensing expert. For a {business_type} business in {city}, {state}, list ALL licenses and permits that are likely required.

For each, return a JSON array of objects:
[
  {{
    "license_type_id": "one of: dba, general_business_license, liquor_license, food_beverage, str_license, professional_license, home_occupation, sales_tax_permit, health_department, building_zoning, signage_permit, special_event, cannabis_license, childcare_license, auto_dealer, or 'other'",
    "license_name": "specific name",
    "required": true/false (true if definitely required, false if situationally required),
    "issuing_authority": "who issues it",
    "estimated_fee": number,
    "priority": "high" or "medium" or "low",
    "notes": "brief explanation"
  }}
]

Include federal, state, county, and city level requirements. Be thorough but realistic for the specific business type."""

            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a business licensing expert. Return only valid JSON array. No markdown."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=2000,
            )

            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            licenses = json.loads(content)

            # Enrich with our pricing / automation info
            for lic in licenses:
                lid = lic.get("license_type_id", "other")
                if lid == "dba":
                    lic["our_service"] = "We file this for you"
                    lic["our_fee"] = 29
                elif lid in {"liquor_license", "food_beverage", "str_license", "cannabis_license",
                            "childcare_license", "auto_dealer", "professional_license"}:
                    lic["our_service"] = "Specialty license assistance"
                    lic["our_fee"] = 99
                elif lid == "general_business_license":
                    lic["our_service"] = "We can file or guide you"
                    lic["our_fee"] = 49
                else:
                    lic["our_service"] = "Guidance provided"
                    lic["our_fee"] = 0

            return {
                "city": city,
                "state": state,
                "business_type": business_type,
                "licenses": licenses,
                "total_estimated_government_fees": sum(l.get("estimated_fee", 0) for l in licenses),
                "generated_at": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"License list AI error: {e}")
            return self._fallback_license_list(city, state, business_type)

    def _fallback_license_list(self, city: str, state: str, business_type: str) -> dict:
        """Fallback license list when AI is unavailable."""
        common = [
            {"license_type_id": "general_business_license", "license_name": "General Business License",
             "required": True, "issuing_authority": f"{city} City Clerk", "estimated_fee": 100,
             "priority": "high", "our_service": "We can file or guide you", "our_fee": 49,
             "notes": "Most cities require a general business license or tax certificate."},
            {"license_type_id": "sales_tax_permit", "license_name": "Sales Tax Permit",
             "required": True, "issuing_authority": f"{state} Department of Revenue", "estimated_fee": 0,
             "priority": "high", "our_service": "Guidance provided", "our_fee": 0,
             "notes": "Required if selling taxable goods or services."},
        ]
        return {
            "city": city, "state": state, "business_type": business_type,
            "licenses": common,
            "total_estimated_government_fees": sum(l.get("estimated_fee", 0) for l in common),
            "generated_at": datetime.utcnow().isoformat(),
            "note": "This is a basic list. For a comprehensive lookup, ensure OPENAI_API_KEY is configured.",
        }

    def get_license_types(self) -> list:
        """Return all supported license types with info."""
        return self.license_types_data.get("license_types", [])

    def get_pricing(self) -> dict:
        """Return pricing for license services."""
        return {
            "dba_filing": {"amount": 29, "description": "DBA filing service (+ government fees)"},
            "business_license_guidance": {"amount": 0, "description": "Free license requirements lookup"},
            "business_license_filing": {"amount": 49, "description": "Business license filing (+ government fees)"},
            "specialty_license_assistance": {"amount": 99, "description": "Specialty license assistance (+ government fees)"},
            "license_needs_report": {"amount": 0, "description": "Free 'What licenses do I need?' report"},
        }
