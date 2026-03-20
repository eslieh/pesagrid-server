"""
NotificationDispatcher — the central notification engine.

Given an event_type and a context payload, it:
  1. Looks up the matching NotificationTemplate for the tenant
  2. Renders the body with {{variable}} substitution
  3. Sends via SMS and/or email depending on recipient contact info
  4. Writes a NotificationLog row for every send attempt
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.modules.notifications.models import NotificationLog, NotifChannel, NotifStatus
from app.modules.notifications.services.renderer import render, build_context
from app.modules.notifications.services.send_sms import send_sms
from app.modules.notifications.services.send_email import send_email
from app.modules.obligations.models import NotificationTemplate, TemplateChannel

logger = logging.getLogger(__name__)


# ─── Default fallback templates (used when tenant hasn't created one) ──────────

_DEFAULT_TEMPLATES = {
    "obligation.created": {
        "sms":   "Hi {{payer_name}}, a payment obligation of {{currency}} {{amount_due}} has been created for account {{account_no}}. Due: {{due_date}}.",
        "email": "<p>Hi <b>{{payer_name}}</b>,</p><p>A payment obligation of <b>{{currency}} {{amount_due}}</b> has been created for account <b>{{account_no}}</b>. Due date: {{due_date}}.</p>",
    },
    "payment.matched": {
        "sms":   "Hi {{payer_name}}, we received {{currency}} {{amount_paid}} for {{account_no}}. Balance: {{currency}} {{balance}}. Ref: {{psp_ref}}. Thank you!",
        "email": "<p>Hi <b>{{payer_name}}</b>,</p><p>Payment of <b>{{currency}} {{amount_paid}}</b> received for <b>{{account_no}}</b>. Outstanding balance: <b>{{currency}} {{balance}}</b>. Ref: {{psp_ref}}.</p>",
    },
    "payment.partial": {
        "sms":   "Hi {{payer_name}}, we received {{currency}} {{amount_paid}} for {{account_no}}. Outstanding: {{currency}} {{balance}}. Ref: {{psp_ref}}.",
        "email": "<p>Hi <b>{{payer_name}}</b>,</p><p>Partial payment of <b>{{currency}} {{amount_paid}}</b> received for <b>{{account_no}}</b>. Outstanding: <b>{{currency}} {{balance}}</b>.</p>",
    },
    "auth.welcome": {
        "sms":   "Welcome to PesaGrid! Your account is ready. Log in at {{login_url}}.",
        "email": "<h2>Welcome to PesaGrid!</h2><p>Your business account is ready. <a href='{{login_url}}'>Log in here</a>.</p>",
    },
    "auth.password_reset": {
        "sms":   "Your PesaGrid password reset code is {{otp}}. Expires in 15 minutes.",
        "email": "<p>Your password reset link: <a href='{{reset_url}}'>Click here</a>. Expires in 15 minutes.</p>",
    },
}


class NotificationDispatcher:

    def __init__(self, db: Session):
        self.db = db

    def _resolve_template(
        self, collection_id: uuid.UUID, event_type: str, channel: str
    ) -> Optional[str]:
        """
        Look up the tenant's custom NotificationTemplate for this event+channel.
        Falls back to the built-in default if none exists.
        """
        # Map event_type to TemplateType loosely (best-effort lookup)
        tmpl = (
            self.db.query(NotificationTemplate)
            .filter(
                NotificationTemplate.collection_id == collection_id,
                NotificationTemplate.channel == channel.upper(),
                NotificationTemplate.is_active.is_(True),
                NotificationTemplate.is_default.is_(True),
            )
            .first()
        )
        if tmpl:
            return tmpl.body, getattr(tmpl, "subject", None), tmpl.id

        # Use built-in fallback
        fallback = _DEFAULT_TEMPLATES.get(event_type, {}).get(channel)
        return fallback, None, None

    def _log(
        self,
        collection_id: uuid.UUID,
        payer_id: Optional[uuid.UUID],
        channel: NotifChannel,
        recipient: str,
        event_type: str,
        body: str,
        subject: Optional[str],
        status: NotifStatus,
        template_id: Optional[uuid.UUID] = None,
        provider_ref: Optional[str] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        log = NotificationLog(
            collection_id=collection_id,
            payer_id=payer_id,
            channel=channel,
            recipient=recipient,
            event_type=event_type,
            template_id=template_id,
            subject=subject,
            body=body,
            status=status,
            provider_ref=provider_ref,
            error_msg=error_msg,
            sent_at=datetime.utcnow() if status == NotifStatus.SENT else None,
        )
        self.db.add(log)
        self.db.commit()

    async def _send_sms(
        self,
        phone: str,
        body: str,
        collection_id: uuid.UUID,
        payer_id: Optional[uuid.UUID],
        event_type: str,
        subject: Optional[str],
        template_id: Optional[uuid.UUID],
    ) -> None:
        try:
            result = await send_sms(phone, body)
            err = result.get("error")
            self._log(
                collection_id=collection_id,
                payer_id=payer_id,
                channel=NotifChannel.SMS,
                recipient=phone,
                event_type=event_type,
                body=body,
                subject=subject,
                status=NotifStatus.FAILED if err else NotifStatus.SENT,
                template_id=template_id,
                error_msg=err,
            )
        except Exception as e:
            self._log(
                collection_id=collection_id,
                payer_id=payer_id,
                channel=NotifChannel.SMS,
                recipient=phone,
                event_type=event_type,
                body=body,
                subject=subject,
                status=NotifStatus.FAILED,
                template_id=template_id,
                error_msg=str(e),
            )

    async def _send_email(
        self,
        email: str,
        subject: str,
        body: str,
        collection_id: uuid.UUID,
        payer_id: Optional[uuid.UUID],
        event_type: str,
        template_id: Optional[uuid.UUID],
    ) -> None:
        try:
            result = await send_email(email, subject, body)
            self._log(
                collection_id=collection_id,
                payer_id=payer_id,
                channel=NotifChannel.EMAIL,
                recipient=email,
                event_type=event_type,
                body=body,
                subject=subject,
                status=NotifStatus.SENT if not result.get("skipped") else NotifStatus.SKIPPED,
                template_id=template_id,
                provider_ref=result.get("id"),
            )
        except Exception as e:
            self._log(
                collection_id=collection_id,
                payer_id=payer_id,
                channel=NotifChannel.EMAIL,
                recipient=email,
                event_type=event_type,
                body=body,
                subject=subject,
                status=NotifStatus.FAILED,
                template_id=template_id,
                error_msg=str(e),
            )

    async def dispatch(
        self,
        event_type: str,
        collection_id: uuid.UUID,
        context: dict,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        payer_id: Optional[uuid.UUID] = None,
        email_subject: Optional[str] = None,
    ) -> None:
        """
        Main dispatch entry point.

        - Resolves template (custom → default fallback)
        - Renders body with context
        - Sends SMS if phone provided
        - Sends email if email provided
        - Logs every attempt
        """
        if not phone and not email:
            logger.debug(f"dispatch({event_type}): no contact info — skipping")
            return

        # SMS
        if phone:
            body_tpl, subject, tmpl_id = self._resolve_template(collection_id, event_type, "sms")
            if body_tpl:
                rendered_body = render(body_tpl, context)
                await self._send_sms(
                    phone=phone,
                    body=rendered_body,
                    collection_id=collection_id,
                    payer_id=payer_id,
                    event_type=event_type,
                    subject=subject,
                    template_id=tmpl_id,
                )

        # Email
        if email:
            body_tpl, subject, tmpl_id = self._resolve_template(collection_id, event_type, "email")
            if body_tpl:
                rendered_body = render(body_tpl, context)
                subject = email_subject or (render(subject, context) if subject else event_type.replace(".", " ").title())
                await self._send_email(
                    email=email,
                    subject=subject,
                    body=rendered_body,
                    collection_id=collection_id,
                    payer_id=payer_id,
                    event_type=event_type,
                    template_id=tmpl_id,
                )
