"""SMTP send service for Gmail (SSL on 465, with optional STARTTLS on 587)."""
from __future__ import annotations

import logging
import mimetypes
import smtplib
import socket
import ssl
import uuid
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path
from typing import Iterable, Optional

from ..config import SMTP_HOST, SMTP_SSL_PORT, SMTP_STARTTLS_PORT, SMTP_TIMEOUT_SEC
from ..crypto import decrypt_password

logger = logging.getLogger(__name__)


class SMTPSendError(Exception):
    pass


def _split_recipients(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def build_message(
    *,
    from_email: str,
    from_name: Optional[str],
    to_list: Iterable[str],
    cc_list: Iterable[str],
    bcc_list: Iterable[str],
    subject: str,
    body_text: Optional[str],
    body_html: Optional[str],
    attachments: Iterable[Path] = (),
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> EmailMessage:
    msg = EmailMessage()
    sender_display = formataddr((from_name or "", from_email))
    msg["From"] = sender_display
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject or ""
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@{from_email.split('@')[-1]}>"

    # Threading headers so replies are grouped into the original conversation
    # by the recipient's mail client.
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    elif references:
        msg["References"] = references

    if not body_text and not body_html:
        body_text = ""
    if body_text is None and body_html:
        body_text = "This message contains HTML content."

    msg.set_content(body_text or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    for path in attachments:
        p = Path(path)
        if not p.is_file():
            continue
        ctype, encoding = mimetypes.guess_type(p.name)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with p.open("rb") as f:
            data = f.read()
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=p.name)

    return msg


def send_email(
    *,
    from_email: str,
    encrypted_password: str,
    from_name: Optional[str],
    to: str,
    cc: Optional[str],
    bcc: Optional[str],
    subject: str,
    body_text: Optional[str],
    body_html: Optional[str],
    attachments: Iterable[Path] = (),
    use_ssl: bool = True,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> str:
    """Send an email and return its Message-ID."""
    try:
        password = decrypt_password(encrypted_password)
    except ValueError as exc:
        raise SMTPSendError("Failed to decrypt stored credentials") from exc

    to_list = _split_recipients(to)
    cc_list = _split_recipients(cc)
    bcc_list = _split_recipients(bcc)
    if not to_list and not cc_list and not bcc_list:
        raise SMTPSendError("At least one recipient is required")

    msg = build_message(
        from_email=from_email,
        from_name=from_name,
        to_list=to_list,
        cc_list=cc_list,
        bcc_list=bcc_list,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        attachments=attachments,
        in_reply_to=in_reply_to,
        references=references,
    )
    recipients = to_list + cc_list + bcc_list

    context = ssl.create_default_context()
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(
                SMTP_HOST, SMTP_SSL_PORT, timeout=SMTP_TIMEOUT_SEC, context=context
            ) as server:
                server.login(from_email, password)
                server.send_message(msg, from_addr=from_email, to_addrs=recipients)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_STARTTLS_PORT, timeout=SMTP_TIMEOUT_SEC) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(from_email, password)
                server.send_message(msg, from_addr=from_email, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as exc:
        raise SMTPSendError("SMTP authentication failed") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise SMTPSendError("All recipients were refused") from exc
    except (smtplib.SMTPException, socket.timeout, socket.gaierror, OSError) as exc:
        raise SMTPSendError(f"SMTP error: {exc.__class__.__name__}") from exc

    return msg["Message-ID"]
