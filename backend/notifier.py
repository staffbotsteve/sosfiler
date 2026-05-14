"""
SOSFiler — Email Notification System
Sends transactional emails via SendGrid.
"""

import os
import logging
import re
import base64
import html
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
AUTH_SENDER = os.getenv("SENDGRID_AUTH_SENDER") or "info@sosfiler.com"
FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL") or AUTH_SENDER
FROM_NAME = os.getenv("SENDGRID_FROM_NAME") or "SOSFiler"
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL") or "admin@sosfiler.com"
REPLY_TO_EMAIL = os.getenv("SENDGRID_REPLY_TO") or "support@sosfiler.com"
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://sosfiler.com/dashboard.html")


@dataclass
class EmailSendResult:
    ok: bool
    status: str
    message: str
    provider: str = "sendgrid"
    status_code: Optional[int] = None
    to_email: str = ""
    subject: str = ""
    live_send: bool = False

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value not in (None, "")}


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _runtime_delivery_mode() -> str:
    explicit = (os.getenv("EMAIL_DELIVERY_MODE") or "").strip().lower()
    aliases = {
        "off": "disabled",
        "disable": "disabled",
        "disabled": "disabled",
        "mock": "noop",
        "no-op": "noop",
        "noop": "noop",
        "test": "noop",
        "sendgrid": "sendgrid",
        "live": "sendgrid",
    }
    if explicit in aliases:
        return aliases[explicit]
    if _truthy(os.getenv("SOSFILER_EMAIL_LIVE", "")):
        return "sendgrid"
    return "noop"


def _parse_status_code(exc: Exception) -> Optional[int]:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None) or getattr(response, "status", None)
        if isinstance(value, int):
            return value
    match = re.search(r"HTTP Error\s+(\d+)", str(exc))
    if match:
        return int(match.group(1))
    return None


def _classify_sendgrid_exception(exc: Exception) -> tuple[str, str, Optional[int]]:
    status_code = _parse_status_code(exc)
    if status_code == 401:
        return (
            "sendgrid_unauthorized",
            "SendGrid rejected the API key with 401 Unauthorized. Create a valid key with Mail Send access and update SENDGRID_API_KEY.",
            status_code,
        )
    if status_code == 403:
        return (
            "sendgrid_forbidden",
            "SendGrid returned 403 Forbidden. The key may lack Mail Send permission, or the sender identity/domain is not verified.",
            status_code,
        )
    if status_code:
        return (
            "sendgrid_http_error",
            f"SendGrid returned HTTP {status_code}.",
            status_code,
        )
    return ("sendgrid_error", f"SendGrid send failed: {exc}", None)


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?is)<(br|/p|/div|/li|/h[1-6])[^>]*>", "\n", value or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


class Notifier:
    """Send transactional emails via SendGrid."""

    def __init__(self):
        self.api_key = os.getenv("SENDGRID_API_KEY", "")
        self.delivery_mode = _runtime_delivery_mode()
        self.from_email = os.getenv("SENDGRID_FROM_EMAIL") or os.getenv("SENDGRID_AUTH_SENDER") or FROM_EMAIL
        self.from_name = os.getenv("SENDGRID_FROM_NAME") or FROM_NAME
        self.admin_email = os.getenv("ADMIN_EMAIL") or ADMIN_EMAIL
        self.reply_to_email = os.getenv("SENDGRID_REPLY_TO") or REPLY_TO_EMAIL
        self.dashboard_url = os.getenv("DASHBOARD_URL") or DASHBOARD_URL

    def config_status(self) -> dict:
        """Return a safe, non-live email delivery diagnostic."""
        try:
            import sendgrid  # noqa: F401
            from sendgrid.helpers.mail import Mail  # noqa: F401
            sendgrid_import_ok = True
        except Exception:
            sendgrid_import_ok = False

        api_key_configured = bool(self.api_key)
        sender_configured = bool(self.from_email)
        mode_configured = bool((os.getenv("EMAIL_DELIVERY_MODE") or "").strip())
        if self.delivery_mode in {"noop", "disabled"}:
            ok = True
            status = self.delivery_mode
            message = (
                "Email delivery is in noop mode; sends are logged as successful without contacting SendGrid."
                if self.delivery_mode == "noop"
                else "Email delivery is disabled."
            )
        else:
            ok = bool(api_key_configured and sender_configured and sendgrid_import_ok)
            status = "configured_not_live_verified" if ok else "misconfigured"
            message = (
                "SendGrid is configured for live delivery. Use the admin test endpoint to verify the key and sender."
                if ok
                else "SendGrid live delivery is selected but key, sender, or package configuration is missing."
            )

        return {
            "ok": ok,
            "status": status,
            "message": message,
            "mode": self.delivery_mode,
            "explicit_mode_configured": mode_configured,
            "api_key_configured": api_key_configured,
            "sender_email": self.from_email,
            "from_name": self.from_name,
            "admin_email": self.admin_email,
            "reply_to_email": self.reply_to_email,
            "dashboard_url": self.dashboard_url,
            "sendgrid_package_available": sendgrid_import_ok,
            "live_send_performed": False,
        }

    async def send_test_email(self, to_email: str = "", subject: str = "SOSFiler SendGrid diagnostic") -> dict:
        recipient = to_email or os.getenv("SENDGRID_TEST_RECIPIENT") or self.admin_email
        html = self._base_template(
            """
<div class="card">
  <h2>SOSFiler email delivery test</h2>
  <p>This message confirms that the SOSFiler application can send authenticated transactional email from sosfiler.com.</p>
  <p>Reply-to is configured for support@sosfiler.com.</p>
</div>"""
        )
        text = (
            "SOSFiler email delivery test\n\n"
            "This message confirms that the SOSFiler application can send authenticated transactional email from sosfiler.com.\n"
            "Reply-to is configured for support@sosfiler.com."
        )
        result = await self._send_email_result(recipient, subject, html, text)
        return result.to_dict()

    async def _send_email(self, to_email: str, subject: str, html_content: str, text_content: str = "", attachments: list = None):
        """Send an email via SendGrid."""
        result = await self._send_email_result(to_email, subject, html_content, text_content, attachments)
        return result.ok

    async def _send_email_result(self, to_email: str, subject: str, html_content: str, text_content: str = "", attachments: list = None) -> EmailSendResult:
        if not to_email:
            return EmailSendResult(False, "missing_recipient", "Email recipient is required.", to_email=to_email, subject=subject)

        if self.delivery_mode in {"noop", "disabled"}:
            logger.info("Email delivery %s. Would send to %s: %s", self.delivery_mode, to_email, subject)
            return EmailSendResult(
                True,
                self.delivery_mode,
                "Email delivery is not live; send was recorded as a noop.",
                provider=self.delivery_mode,
                to_email=to_email,
                subject=subject,
                live_send=False,
            )

        if not self.api_key:
            logger.warning("SendGrid live delivery selected but SENDGRID_API_KEY is missing. Would send to %s: %s", to_email, subject)
            return EmailSendResult(False, "sendgrid_not_configured", "SENDGRID_API_KEY is required for live email delivery.", to_email=to_email, subject=subject)

        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment, FileContent, FileName, FileType, Disposition

            text_content = text_content or _html_to_text(html_content)
            message = Mail(
                from_email=Email(self.from_email, self.from_name),
                to_emails=To(to_email),
                subject=subject,
                html_content=Content("text/html", html_content)
            )
            if self.reply_to_email:
                message.reply_to = Email(self.reply_to_email, self.from_name)

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

            sg = SendGridAPIClient(self.api_key)
            response = sg.send(message)

            status_code = int(getattr(response, "status_code", 0) or 0)
            ok = status_code in (200, 201, 202)
            logger.info("Email sent to %s: %s (status: %s)", to_email, subject, status_code)
            return EmailSendResult(
                ok,
                "sent" if ok else "sendgrid_unexpected_status",
                "Email accepted by SendGrid." if ok else f"SendGrid returned status {status_code}.",
                status_code=status_code,
                to_email=to_email,
                subject=subject,
                live_send=True,
            )

        except Exception as e:
            status, message, status_code = _classify_sendgrid_exception(e)
            logger.error("Failed to send email to %s: %s", to_email, message)
            return EmailSendResult(False, status, message, status_code=status_code, to_email=to_email, subject=subject, live_send=True)

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
    <div class="tagline">Automated preparation. Verified filing evidence.</div>
  </div>
  {content}
  <div class="footer">
    <p>SOSFiler — Honest pricing, no hidden fees.<br>
    <a href="{self.dashboard_url}">View Dashboard</a> ·
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
  <a href="{self.dashboard_url}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">View Dashboard →</a>
</div>"""

        html = self._base_template(content)
        # Notify Customer
        await self._send_email(order.get("email", ""), f"✅ Order Confirmed — {order.get('business_name', '')}", html)
        # Notify Admin
        await self._send_email(self.admin_email, f"ADMN: New Order Paid — {order.get('business_name', '')}", html)

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

    async def send_login_code(self, email: str, code: str, destination: str = "SOSFiler"):
        """Send a short-lived one-time login code."""
        safe_destination = html.escape(destination or "SOSFiler")
        safe_code = html.escape(code)
        content = f"""
<div class="card">
  <h2>Your SOSFiler verification code</h2>
  <p>Use this code to finish signing in to the {safe_destination}. It expires in 10 minutes.</p>
  <p style="font-size:28px;font-weight:800;letter-spacing:6px;margin:24px 0;">{safe_code}</p>
  <p>If you did not request this code, you can ignore this email.</p>
</div>"""
        text = (
            f"Your SOSFiler verification code is {code}.\n\n"
            f"Use it to finish signing in to the {destination or 'SOSFiler'}. It expires in 10 minutes.\n"
            "If you did not request this code, you can ignore this email."
        )
        return await self._send_email(email, "Your SOSFiler verification code", self._base_template(content), text)

    async def send_oauth_recovery_guidance(self, email: str, provider: str):
        """Tell OAuth users to recover through their identity provider."""
        content = f"""
<div class="card">
  <h2>Use {provider.title()} to sign in</h2>
  <p>Your SOSFiler account uses {provider.title()} sign-in, so SOSFiler does not store a password for this account.</p>
  <p>Please recover access through {provider.title()}, then return to your SOSFiler dashboard.</p>
  <a href="{self.dashboard_url}" class="btn">Open Dashboard →</a>
</div>"""
        return await self._send_email(email, "SOSFiler sign-in recovery", self._base_template(content))

    async def send_order_token_recovery(self, email: str, orders: list[dict]):
        """Send dashboard links for every order associated with an email."""
        links = "".join(
            f"""
            <li>
              <strong>{order.get('business_name', 'Company')}</strong><br>
              {order.get('entity_type', '')} · {order.get('state', '')} · Status: {order.get('status', '')}<br>
              <a href="{self.dashboard_url}?order_id={order.get('id', '')}&token={order.get('token', '')}">Open dashboard</a>
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
  <a href="{self.dashboard_url}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">Track Status →</a>
</div>"""

        html = self._base_template(content)
        attachments = []
        if receipt_path and os.path.exists(receipt_path):
            attachments.append({'path': receipt_path, 'name': f"Filing_Receipt_{order.get('business_name', 'LLC')}.pdf"})

        # Notify Customer
        await self._send_email(order.get("email", ""), f"📋 Filing Submitted — {order.get('business_name', '')}", html, attachments=attachments)
        # Notify Admin
        await self._send_email(self.admin_email, f"ADMN: Filing Submitted — {order.get('business_name', '')}", html, attachments=attachments)
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
        await self._send_email(self.admin_email, f"ADMN ONLY: Filing Submitted — {order.get('business_name', '')}", html, attachments=attachments)

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
        await self._send_email(self.admin_email, f"ACTION: Manual Filing Ready — {order.get('business_name', '')}", html)

    async def send_formation_approved(self, order: dict, formation_data: dict, approved_docs: list = None):
        """Send notification when formation is approved and Statement of Organizer is ready."""
        content = f"""
<div class="card">
  <h2>✅ Your LLC is Approved!</h2>
  <p><span class="status-badge status-approved">Approved</span></p>
  <p><strong>{order.get('business_name', '')}</strong> has been officially approved by the {order.get('state', '')} Secretary of State.</p>

  <p>A <strong>Statement of Organizer</strong> has been generated and placed in your Document Vault. This document formally transfers all organizational rights and authority from SOSFiler (as Organizer) to you as the Member(s) of the LLC.</p>

  <a href="{self.dashboard_url}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">View Document Vault →</a>
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
        await self._send_email(self.admin_email, f"ADMN: LLC Approved — {order.get('business_name', '')}", html, attachments=attachments)

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
        await self._send_email(self.admin_email, f"ADMN ONLY: Formation Approved — {order.get('business_name', '')}", html, attachments=attachments)

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

  <a href="{self.dashboard_url}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">Download Documents →</a>
</div>"""

        html = self._base_template(content)
        # Notify Customer
        await self._send_email(order.get("email", ""), f"🎉 Your LLC is Formed — {order.get('business_name', '')}", html)
        # Notify Admin
        await self._send_email(self.admin_email, f"ADMN: Order Complete — {order.get('business_name', '')}", html)

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
  <a href="{self.dashboard_url}?order_id={order.get('id', '')}&token={order.get('token', '')}" class="btn">Download EIN Letter →</a>
</div>"""

        html = self._base_template(content)
        attachments = []
        if ein_letter_path:
            attachments.append({'path': ein_letter_path, 'name': f"EIN_Confirmation_{order.get('business_name', 'LLC')}.pdf"})

        # Notify Customer
        await self._send_email(order.get("email", ""), f"🔢 EIN Received — {order.get('business_name', '')}", html, attachments=attachments)
        # Notify Admin
        await self._send_email(self.admin_email, f"ADMN: EIN Received — {order.get('business_name', '')}", html, attachments=attachments)

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
        await self._send_email(self.admin_email, f"⚠️ STALE ORDER: {order.get('business_name', '')}", html)

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
        await self._send_email(self.admin_email, f"⚠️ SOSFiler Error — Order {order_id}", html)
