"""
Resend email sender — async-compatible.
https://resend.com/docs/send-with-python
"""
import asyncio
import logging
from typing import List, Optional

import resend

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_sync(
    to: str,
    subject: str,
    html_body: str,
    from_email: Optional[str] = None,
) -> dict:
    """Synchronous Resend send — runs in a thread pool from the async caller."""
    resend.api_key = settings.RESEND_API_KEY
    params = resend.Emails.SendParams(
        from_=from_email or settings.RESEND_FROM_EMAIL,
        to=[to],
        subject=subject,
        html=html_body,
    )
    return resend.Emails.send(params)


async def send_email(
    to: str,
    subject: str,
    html_body: str,
    from_email: Optional[str] = None,
) -> dict:
    """
    Send an email via Resend.
    Runs the blocking SDK call in a thread pool so the async event loop
    is not blocked.

    Returns the Resend response dict (contains 'id' on success).
    Raises on HTTP error.
    """
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email send")
        return {"id": None, "skipped": True}

    try:
        result = await asyncio.to_thread(_send_sync, to, subject, html_body, from_email)
        logger.info(f"📧 Email sent to {to} | id={result.get('id')}")
        return result
    except Exception as e:
        logger.error(f"❌ Email send failed to {to}: {e}")
        raise
