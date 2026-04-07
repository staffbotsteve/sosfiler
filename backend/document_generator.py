"""
SOSFiler — AI-Powered Document Generator
Generates all formation documents from wizard data.
Uses templates + OpenAI GPT-4o for customization.
Outputs PDF (via ReportLab) and Markdown.
"""

import os
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "data" / "templates"
DOCS_DIR = BASE_DIR / "generated_docs"
DOCS_DIR.mkdir(exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


class DocumentGenerator:
    """Generate all formation documents from wizard data."""

    def __init__(self):
        self.templates_dir = TEMPLATES_DIR
        self.docs_dir = DOCS_DIR

    async def generate_all(self, order_id: str, data: dict, skip_articles: bool = True) -> list[dict]:
        """Generate all internal documents for an order. Returns list of doc metadata.
        
        Note: Articles of Organization are NOT generated — the state returns those.
        We only generate internal company documents:
        - Operating Agreement
        - Initial Resolutions
        - Meeting Minutes
        - Member Certificates
        - Statement of Organizer
        - EIN Application Data (SS-4)
        """
        order_dir = self.docs_dir / order_id
        order_dir.mkdir(exist_ok=True)

        docs = []

        # 1. Operating Agreement
        oa = await self.generate_operating_agreement(order_id, data)
        docs.extend(oa)

        # 2. Initial Resolutions
        resolutions = await self.generate_initial_resolutions(order_id, data)
        docs.extend(resolutions)

        # 3. Meeting Minutes
        minutes = await self.generate_meeting_minutes(order_id, data)
        docs.extend(minutes)

        # 4. Member Certificates
        certs = await self.generate_member_certificates(order_id, data)
        docs.extend(certs)

        # 5. EIN Application Data (SS-4)
        ss4 = await self.generate_ss4_data(order_id, data)
        docs.extend(ss4)

        return docs

    async def generate_statement_of_organizer(self, order_id: str, data: dict, filing_confirmation: str = "") -> list[dict]:
        """Generate Statement of Organizer transferring authority to Members."""
        template = self._load_template("statement_of_organizer.md")
        context = self._build_context(data)
        context["filing_confirmation"] = filing_confirmation or "On file with state"
        content = self._render_template(template, context)

        docs = []
        md_path = self.docs_dir / order_id / "statement_of_organizer.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content)
        docs.append({
            "type": "statement_of_organizer",
            "filename": "statement_of_organizer.md",
            "path": str(md_path),
            "format": "markdown"
        })

        pdf_path = self.docs_dir / order_id / "statement_of_organizer.pdf"
        self._markdown_to_pdf(content, str(pdf_path), f"Statement of Organizer — {context['llc_name']}")
        docs.append({
            "type": "statement_of_organizer",
            "filename": "statement_of_organizer.pdf",
            "path": str(pdf_path),
            "format": "pdf"
        })

        return docs

    def _load_template(self, template_name: str) -> str:
        path = self.templates_dir / template_name
        if not path.exists():
            return ""
        return path.read_text()

    def _render_template(self, template: str, context: dict) -> str:
        """Simple template rendering with {{variable}} replacement and basic conditionals."""
        result = template

        # Handle simple {{#if var}}...{{else}}...{{/if}} blocks
        def replace_if(match):
            var = match.group(1)
            true_block = match.group(2)
            else_block = match.group(3) if match.group(3) else ""
            val = context.get(var)
            if val:
                return true_block
            return else_block

        # Process if/else blocks
        if_pattern = r'\{\{#if (\w+)\}\}(.*?)(?:\{\{else\}\}(.*?))?\{\{/if\}\}'
        result = re.sub(if_pattern, replace_if, result, flags=re.DOTALL)

        # Handle {{#each items}}...{{/each}} blocks
        def replace_each(match):
            var = match.group(1)
            block = match.group(2)
            items = context.get(var, [])
            output = []
            for item in items:
                item_block = block
                if isinstance(item, dict):
                    for k, v in item.items():
                        item_block = item_block.replace(f"{{{{{k}}}}}", str(v) if v else "")
                else:
                    item_block = item_block.replace("{{this}}", str(item))
                # Handle parent context references {{../var}}
                for k, v in context.items():
                    item_block = item_block.replace(f"{{{{../{k}}}}}", str(v) if v else "")
                output.append(item_block)
            return "\n".join(output)

        each_pattern = r'\{\{#each (\w+)\}\}(.*?)\{\{/each\}\}'
        result = re.sub(each_pattern, replace_each, result, flags=re.DOTALL)

        # Replace simple variables
        for key, value in context.items():
            if isinstance(value, (str, int, float)):
                result = result.replace(f"{{{{{key}}}}}", str(value) if value else "")

        return result

    def _build_context(self, data: dict) -> dict:
        """Build template context from formation data."""
        members = data.get("members", [])
        state = data.get("state", "")
        
        # State names mapping
        state_names = {
            "CA": "California", "TX": "Texas", "FL": "Florida", "NY": "New York",
            "DE": "Delaware", "WY": "Wyoming", "NV": "Nevada", "WA": "Washington",
            "IL": "Illinois", "GA": "Georgia"
        }
        
        filing_offices = {
            "CA": "California Secretary of State", "TX": "Texas Secretary of State",
            "FL": "Florida Division of Corporations", "NY": "New York Department of State",
            "DE": "Delaware Division of Corporations", "WY": "Wyoming Secretary of State",
            "NV": "Nevada Secretary of State", "WA": "Washington Secretary of State",
            "IL": "Illinois Secretary of State", "GA": "Georgia Secretary of State"
        }

        primary_member = members[0] if members else {}
        
        context = {
            "llc_name": data.get("business_name", ""),
            "state": state,
            "state_name": state_names.get(state, state),
            "filing_office": filing_offices.get(state, f"{state} Secretary of State"),
            "purpose": data.get("purpose", "Any lawful purpose"),
            "principal_address": f"{data.get('principal_address', '')}, {data.get('principal_city', '')}, {data.get('principal_state', '')} {data.get('principal_zip', '')}",
            "mailing_address": data.get("mailing_address") or f"{data.get('principal_address', '')}, {data.get('principal_city', '')}, {data.get('principal_state', '')} {data.get('principal_zip', '')}",
            "registered_agent_name": data.get("ra_name") or "SOSFiler Registered Agent Services",
            "registered_agent_address": (
                f"{data.get('ra_address', '')}, {data.get('ra_city', '')}, {data.get('ra_state', '')} {data.get('ra_zip', '')}"
                if data.get("ra_choice") == "self" and data.get("ra_address")
                else "Registered Agent Address — Assigned Upon Filing"
            ),
            "registered_agent_street": data.get("ra_address") or "To Be Assigned",
            "registered_agent_city": data.get("ra_city") or "To Be Assigned",
            "registered_agent_zip": data.get("ra_zip") or "00000",
            "registered_agent_state": data.get("ra_state") or state,
            "registered_agent_mailing": (
                f"{data.get('ra_address', '')}, {data.get('ra_city', '')}, {data.get('ra_state', '')} {data.get('ra_zip', '')}"
                if data.get("ra_choice") == "self" and data.get("ra_address")
                else "To Be Assigned"
            ),
            "management_type": data.get("management_type", "member-managed"),
            "manager_managed": data.get("management_type") == "manager-managed",
            "filing_date": datetime.now().strftime("%B %d, %Y"),
            "effective_date": datetime.now().strftime("%B %d, %Y"),
            "organizer_name": "SOSFiler Document Services",
            "organizer_address": "Registered Agent Address — SOSFiler Document Services",
            "member_name": primary_member.get("name", ""),
            "member_address": f"{primary_member.get('address', '')}, {primary_member.get('city', '')}, {primary_member.get('state', '')} {primary_member.get('zip_code', '')}",
            "county": data.get("county", data.get("principal_city", "")),  # TODO: proper city-to-county lookup
            "additional_provisions": "None.",
            "members": [
                {
                    "name": m.get("name", ""),
                    "address": f"{m.get('address', '')}, {m.get('city', '')}, {m.get('state', '')} {m.get('zip_code', '')}",
                    "contribution": f"${m.get('initial_contribution', 'To Be Determined')}",
                    "ownership_pct": m.get("ownership_pct", 0),
                    "title": "Member"
                }
                for m in members
            ],
            "managers": data.get("managers", []),
            "initial_contribution": "To Be Determined",
            "successor_name": "To Be Designated",
            "successor_relationship": "To Be Designated",
            "accounting_method": "cash",
            "tax_classification": "partnership" if len(members) > 1 else "disregarded entity",
            "s_corp_election": data.get("entity_type") in ("S-Corp",),
            "bank_name": "To Be Selected",
            "authorized_signers": [
                {"name": m.get("name", ""), "title": "Member"} for m in members
            ],
            "signers": [
                {"name": m.get("name", ""), "title": "Member"} for m in members
            ],
            "attendees": [
                {"name": m.get("name", ""), "title": "Member", "ownership_pct": m.get("ownership_pct", 0)} for m in members
            ],
            "meeting_time": "10:00 AM",
            "meeting_location": data.get("principal_address", "Principal Office"),
            "chairperson_name": primary_member.get("name", ""),
            "secretary_name": primary_member.get("name", ""),
            "adjournment_time": "10:30 AM",
            "additional_business": "No additional business was raised.",
            "major_decision_threshold": data.get("major_decision_threshold", 10000),
            "major_decision_vote": "unanimous" if data.get("dissolution_terms") == "unanimous" else "majority",
            "manager_compensation": "To Be Determined by Member Vote",
            "manager_removal_vote_pct": 66,
            "transfer_approval_pct": 100 if data.get("transfer_restrictions") else 51,
            "rofr_days": 30,
            "valuation_method": "mutual agreement or independent appraisal",
            "withdrawal_notice_days": 90,
            "buyout_payment_terms": "in equal monthly installments over 24 months, or as otherwise agreed",
            "dissolution_vote_pct": 100 if data.get("dissolution_terms") == "unanimous" else 66,
            "amendment_vote_pct": 100 if data.get("dissolution_terms") == "unanimous" else 66,
            "non_compete": data.get("non_compete", False),
            "non_compete_months": 12,
            "non_compete_geography": "50-mile radius of the Company's principal office",
            "mediation_location": data.get("principal_city", "the Company's principal office"),
            "arbitration_location": data.get("principal_city", "the Company's principal office"),
            "tax_distributions": data.get("tax_distributions", True),
            "custom_allocation": False,
            "custom_distribution": False,
            "meeting_frequency": "Annual",
            "meeting_notice_days": 10,
            "profit_distribution": data.get("profit_distribution", "pro-rata"),
        }
        
        return context

    async def generate_articles(self, order_id: str, data: dict) -> list[dict]:
        """Generate Articles of Organization."""
        state = data.get("state", "")
        template_name = f"articles_of_organization_{state}.md"
        template = self._load_template(template_name)
        
        if not template:
            # Fallback: generate from first available template and customize
            template = self._load_template("articles_of_organization_CA.md")
        
        context = self._build_context(data)
        content = self._render_template(template, context)
        
        # Optionally enhance with AI
        if OPENAI_API_KEY:
            content = await self._ai_enhance_document(content, "articles of organization", data)
        
        docs = []
        
        # Save as Markdown
        md_path = self.docs_dir / order_id / f"articles_of_organization_{state}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content)
        docs.append({
            "type": "articles_of_organization",
            "filename": f"articles_of_organization_{state}.md",
            "path": str(md_path),
            "format": "markdown"
        })
        
        # Generate PDF
        pdf_path = self.docs_dir / order_id / f"articles_of_organization_{state}.pdf"
        self._markdown_to_pdf(content, str(pdf_path), f"Articles of Organization — {context['llc_name']}")
        docs.append({
            "type": "articles_of_organization",
            "filename": f"articles_of_organization_{state}.pdf",
            "path": str(pdf_path),
            "format": "pdf"
        })
        
        return docs

    async def generate_operating_agreement(self, order_id: str, data: dict) -> list[dict]:
        """Generate Operating Agreement."""
        members = data.get("members", [])
        is_single = len(members) <= 1
        template_name = "operating_agreement_single.md" if is_single else "operating_agreement_multi.md"
        template = self._load_template(template_name)
        
        context = self._build_context(data)
        content = self._render_template(template, context)
        
        if OPENAI_API_KEY:
            content = await self._ai_enhance_document(content, "operating agreement", data)
        
        docs = []
        oa_type = "single_member" if is_single else "multi_member"
        
        md_path = self.docs_dir / order_id / f"operating_agreement_{oa_type}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content)
        docs.append({
            "type": "operating_agreement",
            "filename": f"operating_agreement_{oa_type}.md",
            "path": str(md_path),
            "format": "markdown"
        })
        
        pdf_path = self.docs_dir / order_id / f"operating_agreement_{oa_type}.pdf"
        self._markdown_to_pdf(content, str(pdf_path), f"Operating Agreement — {context['llc_name']}")
        docs.append({
            "type": "operating_agreement",
            "filename": f"operating_agreement_{oa_type}.pdf",
            "path": str(pdf_path),
            "format": "pdf"
        })
        
        return docs

    async def generate_initial_resolutions(self, order_id: str, data: dict) -> list[dict]:
        """Generate Initial Resolutions."""
        template = self._load_template("initial_resolutions.md")
        context = self._build_context(data)
        content = self._render_template(template, context)
        
        docs = []
        md_path = self.docs_dir / order_id / "initial_resolutions.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content)
        docs.append({"type": "initial_resolutions", "filename": "initial_resolutions.md", "path": str(md_path), "format": "markdown"})
        
        pdf_path = self.docs_dir / order_id / "initial_resolutions.pdf"
        self._markdown_to_pdf(content, str(pdf_path), f"Initial Resolutions — {context['llc_name']}")
        docs.append({"type": "initial_resolutions", "filename": "initial_resolutions.pdf", "path": str(pdf_path), "format": "pdf"})
        
        return docs

    async def generate_meeting_minutes(self, order_id: str, data: dict) -> list[dict]:
        """Generate Organizational Meeting Minutes."""
        template = self._load_template("meeting_minutes.md")
        context = self._build_context(data)
        content = self._render_template(template, context)
        
        docs = []
        md_path = self.docs_dir / order_id / "organizational_meeting_minutes.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content)
        docs.append({"type": "meeting_minutes", "filename": "organizational_meeting_minutes.md", "path": str(md_path), "format": "markdown"})
        
        pdf_path = self.docs_dir / order_id / "organizational_meeting_minutes.pdf"
        self._markdown_to_pdf(content, str(pdf_path), f"Meeting Minutes — {context['llc_name']}")
        docs.append({"type": "meeting_minutes", "filename": "organizational_meeting_minutes.pdf", "path": str(pdf_path), "format": "pdf"})
        
        return docs

    async def generate_member_certificates(self, order_id: str, data: dict) -> list[dict]:
        """Generate Member/Shareholder Certificates for each member."""
        template = self._load_template("member_certificate.md")
        context = self._build_context(data)
        members = data.get("members", [])
        
        docs = []
        for i, member in enumerate(members):
            cert_context = {**context}
            cert_context["certificate_number"] = f"MC-{str(i+1).zfill(3)}"
            cert_context["member_name"] = member.get("name", "")
            cert_context["ownership_pct"] = member.get("ownership_pct", 0)
            cert_context["issue_date"] = datetime.now().strftime("%B %d, %Y")
            cert_context["authorized_signer_name"] = members[0].get("name", "") if members else ""
            cert_context["authorized_signer_title"] = "Managing Member"
            
            content = self._render_template(template, cert_context)
            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', member.get("name", f"member_{i+1}"))
            
            md_path = self.docs_dir / order_id / f"member_certificate_{safe_name}.md"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(content)
            docs.append({"type": "member_certificate", "filename": f"member_certificate_{safe_name}.md", "path": str(md_path), "format": "markdown"})
            
            pdf_path = self.docs_dir / order_id / f"member_certificate_{safe_name}.pdf"
            self._markdown_to_pdf(content, str(pdf_path), f"Membership Certificate — {member.get('name', '')}")
            docs.append({"type": "member_certificate", "filename": f"member_certificate_{safe_name}.pdf", "path": str(pdf_path), "format": "pdf"})
        
        return docs

    async def generate_ss4_data(self, order_id: str, data: dict) -> list[dict]:
        """Generate IRS Form SS-4 data for EIN application."""
        members = data.get("members", [])
        responsible = next((m for m in members if m.get("is_responsible_party")), members[0] if members else {})
        
        ss4_data = {
            "form": "SS-4",
            "line_1_legal_name": data.get("business_name", ""),
            "line_2_trade_name": "",
            "line_3_executor_name": responsible.get("name", ""),
            "line_4a_mailing_address": data.get("principal_address", ""),
            "line_4b_city_state_zip": f"{data.get('principal_city', '')}, {data.get('principal_state', '')} {data.get('principal_zip', '')}",
            "line_5a_street_address": data.get("principal_address", ""),
            "line_5b_city_state_zip": f"{data.get('principal_city', '')}, {data.get('principal_state', '')} {data.get('principal_zip', '')}",
            "line_6_county_state": f"{data.get('principal_city', '')} County, {data.get('principal_state', '')}",
            "line_7a_responsible_party_name": responsible.get("name", ""),
            "line_7b_ssn_itin": data.get("responsible_party_ssn", "XXX-XX-XXXX"),
            "line_8a_is_llc": data.get("entity_type") == "LLC",
            "line_8b_num_members": len(members),
            "line_8c_organized_in_us": True,
            "line_9a_type_of_entity": data.get("entity_type", "LLC"),
            "line_9b_state_of_incorporation": data.get("state", ""),
            "line_10_reason": "Started new business",
            "line_11_date_business_started": datetime.now().strftime("%m/%d/%Y"),
            "line_12_closing_month": data.get("fiscal_year_end", "December"),
            "line_13_highest_employees_expected": 0,
            "line_14_employment_tax_liability": "N/A",
            "line_15_first_wages_date": "N/A",
            "line_16_principal_activity": data.get("purpose", "Any lawful purpose"),
            "line_17_principal_product": "",
            "line_18_applied_before": False
        }
        
        content = f"""# IRS Form SS-4 — Application for Employer Identification Number
## Data Sheet for: {data.get('business_name', '')}

**This data will be used to complete your EIN application with the IRS.**

---

### Entity Information
- **Legal Name:** {ss4_data['line_1_legal_name']}
- **Trade Name (DBA):** {ss4_data['line_2_trade_name'] or 'N/A'}
- **Entity Type:** {ss4_data['line_9a_type_of_entity']}
- **State of Formation:** {ss4_data['line_9b_state_of_incorporation']}
- **Number of Members:** {ss4_data['line_8b_num_members']}

### Responsible Party
- **Name:** {ss4_data['line_7a_responsible_party_name']}
- **SSN/ITIN:** {ss4_data['line_7b_ssn_itin']}

### Address
- **Mailing Address:** {ss4_data['line_4a_mailing_address']}
- **City, State, ZIP:** {ss4_data['line_4b_city_state_zip']}

### Business Details
- **Date Started:** {ss4_data['line_11_date_business_started']}
- **Fiscal Year End:** {ss4_data['line_12_closing_month']}
- **Principal Activity:** {ss4_data['line_16_principal_activity']}
- **Reason for Applying:** {ss4_data['line_10_reason']}

---

*This document is prepared by SOSFiler for filing with the IRS.*
"""
        
        docs = []
        md_path = self.docs_dir / order_id / "ein_ss4_data.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(content)
        docs.append({"type": "ein_ss4_data", "filename": "ein_ss4_data.md", "path": str(md_path), "format": "markdown"})
        
        # Also save raw JSON
        json_path = self.docs_dir / order_id / "ein_ss4_data.json"
        json_path.write_text(json.dumps(ss4_data, indent=2))
        docs.append({"type": "ein_ss4_data", "filename": "ein_ss4_data.json", "path": str(json_path), "format": "json"})
        
        return docs

    async def _ai_enhance_document(self, content: str, doc_type: str, data: dict) -> str:
        """Use OpenAI to enhance/customize a document."""
        try:
            import openai
            client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
            
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a legal document specialist. Review the following "
                            f"{doc_type} document and ensure it is complete, properly formatted, "
                            "and appropriate for the state jurisdiction. Fix any template "
                            "placeholders that weren't filled in. Do NOT change the legal substance. "
                            "Return the complete document with any improvements."
                        )
                    },
                    {
                        "role": "user",
                        "content": f"State: {data.get('state')}\nEntity Type: {data.get('entity_type')}\n\nDocument:\n{content}"
                    }
                ],
                max_tokens=4096,
                temperature=0.1
            )
            
            enhanced = response.choices[0].message.content
            if enhanced and len(enhanced) > len(content) * 0.5:
                return enhanced
        except Exception:
            pass  # Fall back to template-only version
        
        return content

    def _markdown_to_pdf(self, markdown_content: str, output_path: str, title: str = ""):
        """Convert Markdown to PDF using ReportLab."""
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            from reportlab.lib.colors import HexColor
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
            from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

            doc = SimpleDocTemplate(
                output_path,
                pagesize=letter,
                rightMargin=72, leftMargin=72,
                topMargin=72, bottomMargin=72
            )
            
            styles = getSampleStyleSheet()
            
            # Custom styles
            styles.add(ParagraphStyle(
                name='DocTitle',
                parent=styles['Title'],
                fontSize=16,
                spaceAfter=6,
                textColor=HexColor('#1a1a2e')
            ))
            styles.add(ParagraphStyle(
                name='DocSubtitle',
                parent=styles['Heading2'],
                fontSize=13,
                spaceAfter=4,
                textColor=HexColor('#16213e')
            ))
            styles.add(ParagraphStyle(
                name='DocBody',
                parent=styles['Normal'],
                fontSize=11,
                leading=15,
                alignment=TA_JUSTIFY,
                spaceAfter=6
            ))
            styles.add(ParagraphStyle(
                name='DocBold',
                parent=styles['Normal'],
                fontSize=11,
                leading=15,
                alignment=TA_LEFT,
                spaceAfter=6,
                fontName='Helvetica-Bold'
            ))
            styles.add(ParagraphStyle(
                name='SectionHead',
                parent=styles['Heading3'],
                fontSize=12,
                spaceBefore=16,
                spaceAfter=8,
                textColor=HexColor('#0f3460')
            ))

            story = []

            # Parse markdown into reportlab elements
            lines = markdown_content.split('\n')
            for line in lines:
                line = line.rstrip()
                
                if line.startswith('# '):
                    story.append(Paragraph(self._escape_xml(line[2:]), styles['DocTitle']))
                    story.append(Spacer(1, 4))
                elif line.startswith('## '):
                    story.append(Paragraph(self._escape_xml(line[3:]), styles['DocSubtitle']))
                    story.append(Spacer(1, 4))
                elif line.startswith('### '):
                    story.append(Paragraph(self._escape_xml(line[4:]), styles['SectionHead']))
                elif line.startswith('**') and line.endswith('**'):
                    story.append(Paragraph(self._escape_xml(line.strip('*')), styles['DocBold']))
                elif line.startswith('- '):
                    bullet_text = line[2:]
                    bullet_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', bullet_text)
                    story.append(Paragraph(f"• {bullet_text}", styles['DocBody']))
                elif line.startswith('---'):
                    story.append(Spacer(1, 12))
                elif line.startswith('|'):
                    # Skip markdown tables (simplified)
                    content_line = line.strip('|').strip()
                    if content_line and not content_line.startswith('-'):
                        story.append(Paragraph(self._escape_xml(content_line.replace('|', ' — ')), styles['DocBody']))
                elif line.strip() == '':
                    story.append(Spacer(1, 6))
                else:
                    # Convert bold markdown to XML
                    formatted = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', self._escape_xml(line))
                    story.append(Paragraph(formatted, styles['DocBody']))

            doc.build(story)
            
        except ImportError:
            # ReportLab not available — write a text-based fallback
            Path(output_path).write_text(
                f"PDF generation requires ReportLab. Install with: pip install reportlab\n\n{markdown_content}"
            )

    def _escape_xml(self, text: str) -> str:
        """Escape XML special characters for ReportLab."""
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        return text
