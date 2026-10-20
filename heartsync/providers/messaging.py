"""SMS and email delivery adapters."""
from __future__ import annotations

import base64
import logging
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

from .. import db as dbm
from ..config import get_settings

log = logging.getLogger("heartsync.messaging")


class DeliveryError(Exception):
    pass


def _dev_outbox(channel: str, recipient: str, subject: str | None, body: str, conn=None) -> None:
    own = conn is None
    conn = conn or dbm.connect()
    try:
        conn.execute(
            "INSERT INTO dev_outbox (channel, recipient, subject, body, created_at) VALUES (?,?,?,?,?)",
            (channel, recipient, subject, body, dbm.iso()),
        )
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------
def send_sms(to_e164: str, body: str, conn=None) -> None:
    s = get_settings()
    if s.sms_provider == "console":
        if s.is_production:
            raise DeliveryError("Console SMS provider cannot be used in production.")
        # DEVELOPMENT ONLY: nothing is sent. The message is stored in the dev outbox.
        log.warning("[DEV SMS - NOT SENT] to=%s****", to_e164[:4])
        _dev_outbox("sms", to_e164, None, body, conn)
        return
    if s.sms_provider == "twilio":
        if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number):
            raise DeliveryError("Twilio is not configured (TWILIO_* env vars missing).")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json"
        data = urllib.parse.urlencode({"To": to_e164, "From": s.twilio_from_number, "Body": body}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        token = base64.b64encode(f"{s.twilio_account_sid}:{s.twilio_auth_token}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 300:
                    raise DeliveryError("SMS provider rejected the message.")
        except Exception as exc:  # never log the phone number or code
            log.error("SMS delivery failed: %s", type(exc).__name__)
            raise DeliveryError("Could not send SMS right now.") from exc
        return
    raise DeliveryError(f"Unknown SMS provider '{s.sms_provider}'.")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def send_email(to: str, subject: str, body: str, conn=None) -> None:
    s = get_settings()
    if s.email_provider == "console":
        if s.is_production:
            raise DeliveryError("Console email provider cannot be used in production.")
        log.warning("[DEV EMAIL - NOT SENT] subject=%r", subject)
        _dev_outbox("email", to, subject, body, conn)
        return
    if s.email_provider == "smtp":
        if not s.smtp_host:
            raise DeliveryError("SMTP is not configured (SMTP_HOST missing).")
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
        except Exception as exc:
            log.error("Email delivery failed: %s", type(exc).__name__)
            raise DeliveryError("Could not send email right now.") from exc
        return
    raise DeliveryError(f"Unknown email provider '{s.email_provider}'.")
