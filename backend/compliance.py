"""
SOSFiler — Compliance Engine
State-specific compliance calendar, deadline tracking, and reminders.
"""

import os
import json
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

with open(DATA_DIR / "state_requirements.json") as f:
    STATE_REQUIREMENTS = json.load(f)


class ComplianceEngine:
    """Generate and manage compliance calendars."""

    def generate_calendar(self, state: str, formation_data: dict, order_id: str) -> list[dict]:
        """
        Generate compliance calendar for a newly formed entity.
        Returns list of deadline dicts: {type, due_date, description}
        """
        state = state.upper()
        deadlines = []
        formation_date = datetime.now()
        
        # Get state-specific requirements
        state_req = STATE_REQUIREMENTS.get(state, {})
        annual_reports = STATE_REQUIREMENTS.get("_all_states_annual_reports", {}).get(state, {})
        
        # 1. Annual Report / Statement of Information
        if annual_reports.get("frequency") not in ("none", None):
            ar_deadlines = self._calculate_annual_report_deadlines(
                state, formation_date, annual_reports
            )
            deadlines.extend(ar_deadlines)
        
        # 2. Franchise Tax
        franchise = state_req.get("franchise_tax", {})
        if franchise.get("required"):
            ft_deadlines = self._calculate_franchise_tax_deadlines(
                state, formation_date, franchise
            )
            deadlines.extend(ft_deadlines)
        
        # 3. Publication Requirements
        if state_req.get("publication_required"):
            pub_deadline = formation_date + timedelta(days=120)
            deadlines.append({
                "type": "publication",
                "due_date": pub_deadline.strftime("%Y-%m-%d"),
                "description": f"Publication requirement — {state_req.get('publication_details', 'Publish notice of formation as required by state law.')}",
                "state": state
            })
        
        # 4. Registered Agent Renewal
        ra_renewal = formation_date + relativedelta(years=1)
        deadlines.append({
            "type": "registered_agent_renewal",
            "due_date": ra_renewal.strftime("%Y-%m-%d"),
            "description": "Registered Agent renewal — $49/yr (SOSFiler). Your first year is included with formation.",
            "state": state
        })
        
        # 5. State-specific items
        state_specific = self._get_state_specific_deadlines(state, formation_date, formation_data)
        deadlines.extend(state_specific)
        
        # 6. General federal deadlines
        deadlines.extend(self._get_federal_deadlines(formation_date, formation_data))
        
        # Sort by due date
        deadlines.sort(key=lambda d: d["due_date"])
        
        return deadlines

    def _calculate_annual_report_deadlines(self, state: str, formation_date: datetime, ar_info: dict) -> list[dict]:
        """Calculate annual/biennial report deadlines."""
        deadlines = []
        frequency = ar_info.get("frequency", "annual")
        fee = ar_info.get("fee", 0)
        due_info = ar_info.get("due", "")
        
        years_ahead = 3  # Generate 3 years of deadlines
        
        for year_offset in range(1, years_ahead + 1):
            if frequency == "biennial" and year_offset % 2 != 0:
                # First biennial report may be due at year 1 or 2 depending on state
                if year_offset > 1:
                    continue
            
            # Calculate due date based on state rules
            due_date = self._parse_due_date(due_info, formation_date, year_offset)
            
            if due_date:
                deadlines.append({
                    "type": "annual_report",
                    "due_date": due_date.strftime("%Y-%m-%d"),
                    "description": f"Annual Report filing — ${fee} state fee. {'(Biennial)' if frequency == 'biennial' else ''} {due_info}",
                    "state": state,
                    "fee": fee
                })
        
        return deadlines

    def _calculate_franchise_tax_deadlines(self, state: str, formation_date: datetime, ft_info: dict) -> list[dict]:
        """Calculate franchise tax deadlines."""
        deadlines = []
        amount = ft_info.get("amount", "Varies")
        due_info = ft_info.get("due", "")
        notes = ft_info.get("notes", "")
        
        for year_offset in range(1, 4):
            due_date = self._parse_due_date(due_info, formation_date, year_offset)
            
            if due_date:
                deadlines.append({
                    "type": "franchise_tax",
                    "due_date": due_date.strftime("%Y-%m-%d"),
                    "description": f"Franchise Tax — ${amount}. {notes}",
                    "state": state
                })
        
        return deadlines

    def _parse_due_date(self, due_string: str, formation_date: datetime, year_offset: int) -> Optional[datetime]:
        """Parse a due date description into an actual date."""
        due_lower = due_string.lower()
        target_year = formation_date.year + year_offset
        
        # "Anniversary month" pattern
        if "anniversary" in due_lower:
            return datetime(target_year, formation_date.month, 1) + relativedelta(months=1) - timedelta(days=1)
        
        # Specific month/day patterns
        month_map = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12
        }
        
        for month_name, month_num in month_map.items():
            if month_name in due_lower:
                # Try to find day
                import re
                day_match = re.search(r'(\d{1,2})', due_string)
                day = int(day_match.group(1)) if day_match else 1
                try:
                    return datetime(target_year, month_num, min(day, 28))
                except ValueError:
                    return datetime(target_year, month_num, 1)
        
        # "Within 90 days" pattern (first filing)
        if "within" in due_lower and year_offset == 1:
            days_match = __import__('re').search(r'(\d+)\s*days', due_string)
            if days_match:
                days = int(days_match.group(1))
                return formation_date + timedelta(days=days)
        
        # Default: anniversary of formation
        return datetime(target_year, formation_date.month, formation_date.day)

    def _get_state_specific_deadlines(self, state: str, formation_date: datetime, data: dict) -> list[dict]:
        """Get state-specific compliance deadlines beyond standard ones."""
        deadlines = []
        
        if state == "CA":
            # Statement of Information due within 90 days
            si_date = formation_date + timedelta(days=90)
            deadlines.append({
                "type": "statement_of_information",
                "due_date": si_date.strftime("%Y-%m-%d"),
                "description": "California Statement of Information (LLC-12) — $20. Due within 90 days of formation, then every 2 years.",
                "state": "CA",
                "fee": 20
            })
        
        elif state == "NY":
            # Publication requirement — 120 days
            pub_date = formation_date + timedelta(days=120)
            deadlines.append({
                "type": "publication",
                "due_date": pub_date.strftime("%Y-%m-%d"),
                "description": "New York LLC Publication Requirement — Must publish in 2 newspapers for 6 consecutive weeks within 120 days. File Certificate of Publication with DOS. Cost varies by county ($300-$1500+).",
                "state": "NY"
            })
        
        elif state == "NV":
            # Business license renewal
            bl_date = formation_date + relativedelta(years=1)
            deadlines.append({
                "type": "business_license",
                "due_date": bl_date.strftime("%Y-%m-%d"),
                "description": "Nevada State Business License renewal — $200/year.",
                "state": "NV",
                "fee": 200
            })
        
        elif state == "TX":
            # No Tax Due report
            next_may = datetime(formation_date.year + 1, 5, 15)
            if next_may < formation_date + timedelta(days=90):
                next_may = datetime(formation_date.year + 2, 5, 15)
            deadlines.append({
                "type": "franchise_tax_report",
                "due_date": next_may.strftime("%Y-%m-%d"),
                "description": "Texas Franchise Tax / No Tax Due Report — Due May 15 annually. No tax due if revenue under $1.23M, but report must still be filed.",
                "state": "TX",
                "fee": 0
            })
        
        elif state == "DE":
            # Annual tax
            next_june = datetime(formation_date.year + 1, 6, 1)
            deadlines.append({
                "type": "annual_tax",
                "due_date": next_june.strftime("%Y-%m-%d"),
                "description": "Delaware Annual LLC Tax — $300 flat fee due June 1 each year.",
                "state": "DE",
                "fee": 300
            })
        
        return deadlines

    def _get_federal_deadlines(self, formation_date: datetime, data: dict) -> list[dict]:
        """Get federal compliance deadlines."""
        deadlines = []
        entity_type = data.get("entity_type", "LLC")
        members = data.get("members", [])
        
        # Tax return deadlines
        if entity_type in ("LLC",) and len(members) > 1:
            # Partnership return (Form 1065) — March 15
            for yr in range(1, 4):
                tax_year = formation_date.year + yr
                deadlines.append({
                    "type": "federal_tax_return",
                    "due_date": f"{tax_year}-03-15",
                    "description": f"Federal Partnership Tax Return (Form 1065) for {tax_year - 1}. K-1s to members by March 15.",
                    "state": "FED"
                })
        elif entity_type in ("S-Corp", "C-Corp"):
            for yr in range(1, 4):
                tax_year = formation_date.year + yr
                due = "03-15" if entity_type == "S-Corp" else "04-15"
                form = "1120-S" if entity_type == "S-Corp" else "1120"
                deadlines.append({
                    "type": "federal_tax_return",
                    "due_date": f"{tax_year}-{due}",
                    "description": f"Federal Corporate Tax Return (Form {form}) for {tax_year - 1}.",
                    "state": "FED"
                })
        
        # BOI Report (Beneficial Ownership Information) — FinCEN
        boi_deadline = formation_date + timedelta(days=90)
        deadlines.append({
            "type": "boi_report",
            "due_date": boi_deadline.strftime("%Y-%m-%d"),
            "description": "FinCEN Beneficial Ownership Information (BOI) Report — Required within 90 days of formation. File at https://boiefiling.fincen.gov. Free to file.",
            "state": "FED"
        })
        
        return deadlines

    def check_upcoming_deadlines(self, order_id: str, days_ahead: int = 60) -> list[dict]:
        """Check for upcoming deadlines within the specified window."""
        import sqlite3
        conn = sqlite3.connect(str(BASE_DIR / "sosfiler.db"))
        conn.row_factory = sqlite3.Row
        
        cutoff = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        
        deadlines = conn.execute("""
            SELECT * FROM compliance_deadlines 
            WHERE order_id = ? AND due_date BETWEEN ? AND ? AND status = 'upcoming'
            ORDER BY due_date ASC
        """, (order_id, today, cutoff)).fetchall()
        
        conn.close()
        return [dict(d) for d in deadlines]

    def get_compliance_status(self, order_id: str) -> dict:
        """Get overall compliance status for an order."""
        import sqlite3
        conn = sqlite3.connect(str(BASE_DIR / "sosfiler.db"))
        conn.row_factory = sqlite3.Row
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        all_deadlines = conn.execute(
            "SELECT * FROM compliance_deadlines WHERE order_id = ? ORDER BY due_date",
            (order_id,)
        ).fetchall()
        
        conn.close()
        
        overdue = [d for d in all_deadlines if d["due_date"] < today and d["status"] == "upcoming"]
        upcoming_30 = [d for d in all_deadlines if today <= d["due_date"] <= (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")]
        
        if overdue:
            status = "red"
            message = f"{len(overdue)} overdue deadline(s)!"
        elif upcoming_30:
            status = "yellow"
            message = f"{len(upcoming_30)} deadline(s) due within 30 days"
        else:
            status = "green"
            message = "All compliance deadlines are current"
        
        return {
            "status": status,
            "message": message,
            "overdue": [dict(d) for d in overdue],
            "upcoming": [dict(d) for d in upcoming_30],
            "total_deadlines": len(all_deadlines)
        }
