"""
SOSFiler — Email Notification System
Sends transactional emails via SendGrid.
"""

import os
import logging
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
# Using verified sender for authentication
AUTH_SENDER = "s.swan@providence.aero"
FROM_EMAIL = "info@sosfiler.com"
FROM_NAME = "SOSFiler"
ADMIN_EMAIL = "admin@swanbill.biz"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://sosfiler.com/dashboard.html")


class Notifier:
    """Send transactional emails via SendGrid."""

    def __init__(self):
        self.from_email = FROM_EMAIL
        self.from_name = FROM_NAME

    async def _send_email(self, to_email: str, subject: str, html_content: str, text_content: str = "", attachments: list = None):
        """Send an email via SendGrid."""
        if not SENDGRID_API_KEY:
            logger.warning(f"SendGrid not configured. Would send to {to_email}: {subject}")
            return False
        
        try:
            import base64
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment, FileContent, FileName, FileType, Disposition
            
            # Note: We use AUTH_SENDER for the 'from' field to satisfy SendGrid verification
            message = Mail(
                from_email=Email(AUTH_SENDER, self.from_name),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content)
            )
            
            if text_content:
                message.add_content(Content("text/plain", text_content))
                
            if attachments:
                for att in attachments:
                    with open(att['path'], 'rb') as f:
                        data = f.read()
                    encoded = base64.b64encode(data).decode()
                    attachment = Attachment(
                        FileContent(encoded),
                        FileName(att['name']),
                        FileType(att.get('type', 'application/pdf')),
                        Disposition('attachment')
                    )
                    message.add_attachment(attachment)
            
            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(message)
            
            logger.info(f"Email sent to {to_email}: {subject} (status: {response.status_code})")
            return response.status_code in (200, 201, 202)
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

    def _base_template(self, content: str) -> str:
        """Wrap content in branded email template."""
        return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 0; background: #0a0a1a; color: #e0e0e0; }}
  .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
  .header {{ text-align: center; padding: 30px 0 20px; }}
  .header h1 {{ color: #00d4ff; font-size: 28px; margin: 0; font-weight: 700; }}
  .header .tagline {{ color: #888; font-size: 14px; margin-top: 4px; }}
  .card {{ background: #12122a; border-radius: 12px; padding: 30px; margin: 20px 0; border: 1px solid #1e1e3a; }}
  .card h2 {{ color: #ffffff; font-size: 20px; margin-top: 0; }}
  .card p {{ color: #c0c0d0; line-height: 1.7; }}
  .status-badge {{ display: inline-block; padding: 6px 16px; border-radius: 20px; font-size: 13px; font-weight: 600; }}
  .status-paid {{ background: #0d3320; color: #34d399; }}
  .status-filing {{ background: #1e293b; color: #60a5fa; }}
  .status-approved {{ background: #0d3320; color: #34d399; }}
  .status-complete {{ background: #0d3320; color: #34d399; }}
  .btn {{ display: inline-block; padding: 14px 32px; background: linear-gradient(135deg, #00d4ff, #0099cc); color: #000 !important; text-decoration: none; border-radius: 8px; font-weight: 700; font-size: 16px; margin: 16px 0; }}
  .detail-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #1e1e3a; }}
  .detail-label {{ color: #888; }}
  .detail-value {{ color: #fff; font-weight: 600; }}
  .footer {{ text-align: center; padding: 30px 0; color: #555; font-size: 12px; }}
  .footer a {{ color: #00d4ff; text-decoration: none; }}
  ul {{ color: #c0c0d0; line-height: 2; }}
  li {{ margin: 4px 0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚡ SOSFiler</h1>
    <div class="tagline">Your LLC. Filed in minutes. Not weeks.</div>
  </div>
  {content}
  <div class="footer">
    <p>SOSFiler — Honest pricing, no hidden fees.<br>
    <a href="{DASHBOARD_URL}">View Dashboard</a> · 
    <a href="mailto:support@sosfiler.com">Contact Support</a></p>
    <p>© {datetime.now().year} SOSFiler. All rights reserved.</p>
  </div>
</div>
</body>
</html>"""

    async def send_order_confirmation(self, order: dict, formation_data: dict):
        """Send order confirmation after payment."""
        state_name = formation_data.get("state", "")
        content = f"""
<div class="card">
  <h2>✅ Payment Confirmed — We're On It!</h2>
  <p>Hi {formation_data.get('members', [{}])[0].get('name', '').split()[0] if formation_data.get('members') else 'there'},</p>
  <p>Your payment has been received and we're starting the formation process for <strong>{order.get('business_name', '')}</strong> right now.</p>
  
  <div style="margin: 20px 0;">
    <div class="detail-row"><span class="detail-label">Order ID</span><span class="detail-value">{order.get('id', '')}</span></div>
    <div class="detail-row"><span class="detail-label">Entity Type</span><span class="detail-value">{order.get('entity_type', 'LLC')}</span></div>
    <div class="detail-row"><span class="detail-label">State</span><span class="detail-value">{state_name}</span></div>
    <div class="detail-row"><span class="detail-label">Business Name</span><span class="detail-value">{order.get('business_name', '')}</span></div>
    <div class="detail-row"><span class="detail-label">Total Paid</span><span class="detail-value">${order.get('total_cents', 0) / 100:.2f}</span></div>
  </div>
  
  <p><strong>What happens next:</strong></p>
  <ul>
    <li>📄 We generate your internal company documents, including your Operating Agreement and initial resolutions</li>
    <li>📋 We prepare the state filing packet and route it for verified submission</li>
    <li>✅ We update your dashboard when submission evidence and state approval documents are on file</li>
    <li>🔢 We prepare EIN data and apply after the state approves the formation</li>
    <li>📅 We set up your compliance calendar</li>
  </ul>
  
  <p>Track everything in real-time:</p>
  <a href="{DASHBOARD_URL}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">View Dashboard →</a>
</div>"""
        
        html = self._base_template(content)
        # Notify Customer
        await self._send_email(order.get("email", ""), f"✅ Order Confirmed — {order.get('business_name', '')}", html)
        # Notify Admin
        await self._send_email(ADMIN_EMAIL, f"ADMN: New Order Paid — {order.get('business_name', '')}", html)

    async def send_password_reset(self, email: str, reset_url: str):
        """Send a password reset link."""
        content = f"""
<div class="card">
  <h2>Reset your SOSFiler password</h2>
  <p>Use the button below to choose a new password for your SOSFiler account. This link expires in 1 hour.</p>
  <a href="{reset_url}" class="btn">Reset Password →</a>
  <p>If you did not request this, you can ignore this email.</p>
</div>"""
        return await self._send_email(email, "Reset your SOSFiler password", self._base_template(content))

    async def send_oauth_recovery_guidance(self, email: str, provider: str):
        """Tell OAuth users to recover through their identity provider."""
        content = f"""
<div class="card">
  <h2>Use {provider.title()} to sign in</h2>
  <p>Your SOSFiler account uses {provider.title()} sign-in, so SOSFiler does not store a password for this account.</p>
  <p>Please recover access through {provider.title()}, then return to your SOSFiler dashboard.</p>
  <a href="{DASHBOARD_URL}" class="btn">Open Dashboard →</a>
</div>"""
        return await self._send_email(email, "SOSFiler sign-in recovery", self._base_template(content))

    async def send_order_token_recovery(self, email: str, orders: list[dict]):
        """Send dashboard links for every order associated with an email."""
        links = "".join(
            f"""
            <li>
              <strong>{order.get('business_name', 'Company')}</strong><br>
              {order.get('entity_type', '')} · {order.get('state', '')} · Status: {order.get('status', '')}<br>
              <a href="{DASHBOARD_URL}?order_id={order.get('id', '')}&token={order.get('token', '')}">Open dashboard</a>
            </li>
            """
            for order in orders
        )
        content = f"""
<div class="card">
  <h2>Your SOSFiler dashboard links</h2>
  <p>We found {len(orders)} order{'s' if len(orders) != 1 else ''} associated with this email.</p>
  <ul>{links}</ul>
  <p>Keep these links private. Anyone with an order link can view that order's dashboard and documents.</p>
</div>"""
        return await self._send_email(email, "Your SOSFiler dashboard links", self._base_template(content))

    async def send_filing_submitted(self, order: dict, formation_data: dict, receipt_path: str = None):
        """Send notification when filing is submitted to the state."""
        if not receipt_path:
            logger.warning("Blocked filing-submitted notification for %s because no receipt/evidence path was provided", order.get("id", ""))
            return False

        content = f"""
<div class="card">
  <h2>📋 Filing Submitted</h2>
  <p><span class="status-badge status-filing">Filing in Progress</span></p>
  <p>We've submitted the formation documents for <strong>{order.get('business_name', '')}</strong> to the {order.get('state', '')} Secretary of State.</p>
  <p>Submission evidence is on file in the SOSFiler operator record. Typical processing time varies by state.</p>
  <a href="{DASHBOARD_URL}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">Track Status →</a>
</div>"""
        
        html = self._base_template(content)
        attachments = []
        if receipt_path and os.path.exists(receipt_path):
            attachments.append({'path': receipt_path, 'name': f"Filing_Receipt_{order.get('business_name', 'LLC')}.pdf"})

        # Notify Customer
        await self._send_email(order.get("email", ""), f"📋 Filing Submitted — {order.get('business_name', '')}", html, attachments=attachments)
        # Notify Admin
        await self._send_email(ADMIN_EMAIL, f"ADMN: Filing Submitted — {order.get('business_name', '')}", html, attachments=attachments)
        return True

    async def send_admin_filing_submitted(self, order: dict, filing_job: dict, receipt_path: str = None, message: str = ""):
        """Send an internal-only state submission notice."""
        content = f"""
<div class="card">
  <h2>Filing Submitted - Admin Only</h2>
  <p><strong>Order:</strong> {order.get('id', '')}</p>
  <p><strong>Business:</strong> {order.get('business_name', '')}</p>
  <p><strong>Customer:</strong> {order.get('email', '')}</p>
  <p><strong>State/Form:</strong> {filing_job.get('state', '')} {filing_job.get('form_name', 'Formation filing')}</p>
  <p><strong>Evidence:</strong> {receipt_path or 'On file'}</p>
  <p>{message or 'The filing was marked submitted with evidence. Customer notification was suppressed.'}</p>
</div>"""
        html = self._base_template(content)
        attachments = []
        if receipt_path and os.path.exists(receipt_path):
            attachments.append({'path': receipt_path, 'name': f"Filing_Receipt_{order.get('business_name', 'LLC')}.pdf"})
        await self._send_email(ADMIN_EMAIL, f"ADMN ONLY: Filing Submitted — {order.get('business_name', '')}", html, attachments=attachments)

    async def send_manual_filing_required(self, order: dict, formation_data: dict, filing_job: dict):
        """Send internal notice when an order is ready for human/evidence-backed filing."""
        content = f"""
<div class="card">
  <h2>Manual Filing Ready</h2>
  <p><strong>Order:</strong> {order.get('id', '')}</p>
  <p><strong>Business:</strong> {order.get('business_name', '')}</p>
  <p><strong>Customer:</strong> {order.get('email', '')}</p>
  <p><strong>State/Form:</strong> {filing_job.get('state', '')} {filing_job.get('form_name', 'Formation filing')}</p>
  <p><strong>Portal:</strong> {filing_job.get('portal_name', '')} — {filing_job.get('portal_url', '')}</p>
  <p><strong>Government total:</strong> ${(filing_job.get('total_government_cents', 0) or 0) / 100:.2f}</p>
  <p>Submit only through the official portal, then attach receipt evidence before changing the customer-facing status.</p>
</div>"""

        html = self._base_template(content)
        await self._send_email(ADMIN_EMAIL, f"ACTION: Manual Filing Ready — {order.get('business_name', '')}", html)

    async def send_formation_approved(self, order: dict, formation_data: dict, approved_docs: list = None):
        """Send notification when formation is approved and Statement of Organizer is ready."""
        content = f"""
<div class="card">
  <h2>✅ Your LLC is Approved!</h2>
  <p><span class="status-badge status-approved">Approved</span></p>
  <p><strong>{order.get('business_name', '')}</strong> has been officially approved by the {order.get('state', '')} Secretary of State.</p>
  
  <p>A <strong>Statement of Organizer</strong> has been generated and placed in your Document Vault. This document formally transfers all organizational rights and authority from SOSFiler (as Organizer) to you as the Member(s) of the LLC.</p>
  
  <a href="{DASHBOARD_URL}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">View Document Vault →</a>
</div>"""
        
        html = self._base_template(content)
        attachments = []
        if approved_docs:
            for doc in approved_docs:
                if os.path.exists(doc['path']):
                    attachments.append({'path': doc['path'], 'name': doc['name']})

        # Notify Customer
        await self._send_email(order.get("email", ""), f"✅ LLC Approved — {order.get('business_name', '')}", html, attachments=attachments)
        # Notify Admin
        await self._send_email(ADMIN_EMAIL, f"ADMN: LLC Approved — {order.get('business_name', '')}", html, attachments=attachments)

    async def send_admin_formation_approved(self, order: dict, filing_job: dict, approved_path: str = None, message: str = ""):
        """Send an internal-only state approval notice."""
        content = f"""
<div class="card">
  <h2>Formation Approved - Admin Only</h2>
  <p><strong>Order:</strong> {order.get('id', '')}</p>
  <p><strong>Business:</strong> {order.get('business_name', '')}</p>
  <p><strong>Customer:</strong> {order.get('email', '')}</p>
  <p><strong>State/Form:</strong> {filing_job.get('state', '')} {filing_job.get('form_name', 'Formation filing')}</p>
  <p><strong>Approval Evidence:</strong> {approved_path or 'On file'}</p>
  <p>{message or 'The filing was marked approved with evidence. Customer notification was suppressed.'}</p>
</div>"""
        html = self._base_template(content)
        attachments = []
        if approved_path and os.path.exists(approved_path):
            attachments.append({'path': approved_path, 'name': f"Approval_{order.get('business_name', 'LLC')}.pdf"})
        await self._send_email(ADMIN_EMAIL, f"ADMN ONLY: Formation Approved — {order.get('business_name', '')}", html, attachments=attachments)

    async def send_documents_ready(self, order: dict, formation_data: dict):
        """Send notification when all documents are ready."""
        content = f"""
<div class="card">
  <h2>🎉 Your LLC is Formed!</h2>
  <p><span class="status-badge status-complete">Complete</span></p>
  <p>Congratulations! <strong>{order.get('business_name', '')}</strong> is officially formed. All your documents are ready for download.</p>
  
  <p><strong>Your documents:</strong></p>
  <ul>
    <li>📄 Articles of Organization (filed & approved)</li>
    <li>📋 Operating Agreement</li>
    <li>📝 Initial Resolutions</li>
    <li>📝 Organizational Meeting Minutes</li>
    <li>🏆 Membership Certificate(s)</li>
    <li>{'🔢 EIN Confirmation Letter' if order.get('ein') else '🔢 EIN (pending — coming within 1-2 business days)'}</li>
  </ul>
  
  <a href="{DASHBOARD_URL}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">Download Documents →</a>
</div>"""
        
        html = self._base_template(content)
        # Notify Customer
        await self._send_email(order.get("email", ""), f"🎉 Your LLC is Formed — {order.get('business_name', '')}", html)
        # Notify Admin
        await self._send_email(ADMIN_EMAIL, f"ADMN: Order Complete — {order.get('business_name', '')}", html)

    async def send_ein_received(self, order: dict, ein: str, ein_letter_path: str = None):
        """Send notification when EIN is received."""
        content = f"""
<div class="card">
  <h2>🔢 EIN Received!</h2>
  <p>Your Employer Identification Number has been assigned by the IRS.</p>
  
  <div style="background: #0d1b2a; padding: 20px; border-radius: 8px; text-align: center; margin: 20px 0;">
    <div style="color: #888; font-size: 14px;">Your EIN</div>
    <div style="color: #00d4ff; font-size: 32px; font-weight: 700; letter-spacing: 2px; margin-top: 8px;">{ein}</div>
  </div>
  
  <p>Download your EIN confirmation letter from your dashboard:</p>
  <a href="{DASHBOARD_URL}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">Download EIN Letter →</a>
</div>"""
        
        html = self._base_template(content)
        attachments = []
        if ein_letter_path:
            attachments.append({'path': ein_letter_path, 'name': f"EIN_Confirmation_{order.get('business_name', 'LLC')}.pdf"})

        # Notify Customer
        await self._send_email(order.get("email", ""), f"🔢 EIN Received — {order.get('business_name', '')}", html, attachments=attachments)
        # Notify Admin
        await self._send_email(ADMIN_EMAIL, f"ADMN: EIN Received — {order.get('business_name', '')}", html, attachments=attachments)

    async def send_stale_order_alert(self, order: dict, hours: int):
        """Send alert for orders stuck in a state for too long."""
        content = f"""
<div class="card">
  <h2>⚠️ Stale Order Alert ({hours}h+)</h2>
  <p><strong>Order:</strong> {order.get('id', '')}</p>
  <p><strong>Business:</strong> {order.get('business_name', '')}</p>
  <p><strong>Current Status:</strong> {order.get('status', '')}</p>
  <p>This order has been in its current state for over {hours} hours without progress.</p>
</div>"""
        html = self._base_template(content)
        await self._send_email(ADMIN_EMAIL, f"⚠️ STALE ORDER: {order.get('business_name', '')}", html)

    async def send_error_alert(self, order_id: str, error: str):
        """Send internal error alert for human review."""
        content = f"""
<div class="card">
  <h2>⚠️ Formation Pipeline Error</h2>
  <p><strong>Order:</strong> {order_id}</p>
  <p><strong>Error:</strong> {error}</p>
  <p>This order requires manual review and intervention.</p>
</div>"""
        
        html = self._base_template(content)
        await self._send_email(ADMIN_EMAIL, f"⚠️ SOSFiler Error — Order {order_id}", html)
