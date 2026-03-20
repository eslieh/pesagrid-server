"""
NotificationDispatcher — the central notification engine.

Given an event_type and a context payload, it:
  1. Looks up the tenant's BusinessProfile (for sender name/email/SMS ID)
  2. Looks up the tenant's NotificationTemplate for this channel+event
  3. If no template found → logs SKIPPED (business is in full control)
  4. Renders the body with {{variable}} substitution
  5. Sends via SMS and/or email
  6. Writes a NotificationLog row for every send attempt

All message content is 100% controlled by the business — no hardcoded
fallbacks. If the business hasn't created a template for an event, the
notification is skipped and logged so they can see what's missing.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.modules.notifications.models import NotificationLog, NotifChannel, NotifStatus
from app.modules.notifications.services.renderer import render
from app.modules.notifications.services.send_sms import send_sms
from app.modules.notifications.services.send_email import send_email
from app.modules.obligations.models import NotificationTemplate, TemplateType
from app.core.config import settings

logger = logging.getLogger(__name__)


# Maps event string → TemplateType for template lookup
_EVENT_TO_TEMPLATE_TYPE = {
    "obligation.created":  TemplateType.PAYMENT_REMINDER,
    "obligation.due":      TemplateType.PAYMENT_REMINDER,
    "payment.matched":     TemplateType.PAYMENT_RECEIPT,
    "payment.partial":     TemplateType.PAYMENT_RECEIPT,
    "payment.unmatched":   TemplateType.CUSTOM,
    "auth.welcome":        TemplateType.CUSTOM,
    "auth.password_reset": TemplateType.CUSTOM,
}


def _build_from_email(display_name: str, domain_or_email: str, platform: bool = False) -> str:
    """
    Build a Resend-compatible from address.

    - platform=True  (auth emails from us)  → pesagrid@mails.ryfty.net
    - platform=False (biz → payer emails)   → skyview-apts@mails.ryfty.net
    - domain_or_email contains '@'          → use it as-is
    """
    if "@" in domain_or_email:
        return domain_or_email
    prefix = "pesagrid" if platform else display_name.lower().strip().replace(" ", "-")[:30]
    return f"{prefix}@{domain_or_email}"


class NotificationDispatcher:

    def __init__(self, db: Session):
        self.db = db

    def _get_business_profile(self, collection_id: uuid.UUID):
        try:
            from app.modules.accounts.models import BusinessProfile
            return (
                self.db.query(BusinessProfile)
                .filter(BusinessProfile.collection_id == collection_id)
                .first()
            )
        except Exception:
            return None

    def _resolve_template(
        self,
        collection_id: uuid.UUID,
        event_type: str,
        channel: str,
    ) -> Tuple[Optional[str], Optional[str], Optional[uuid.UUID]]:
        """
        Look up the tenant's NotificationTemplate for this event + channel.
        Returns (body, subject, template_id) or (None, None, None).
        No hardcoded fallbacks — business is fully in control.
        """
        tmpl_type = _EVENT_TO_TEMPLATE_TYPE.get(event_type)
        q = self.db.query(NotificationTemplate).filter(
            NotificationTemplate.collection_id == collection_id,
            NotificationTemplate.channel == channel.upper(),
            NotificationTemplate.is_active.is_(True),
        )
        if tmpl_type:
            exact = q.filter(
                NotificationTemplate.template_type == tmpl_type,
                NotificationTemplate.is_default.is_(True),
            ).first()
            if exact:
                return exact.body, getattr(exact, "subject", None), exact.id
            any_match = q.filter(NotificationTemplate.template_type == tmpl_type).first()
            if any_match:
                return any_match.body, getattr(any_match, "subject", None), any_match.id
        return None, None, None

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
        Main dispatch. Resolves sender identity from BusinessProfile,
        looks up tenant templates, renders, sends, and logs.
        """
        if not phone and not email:
            return

        profile = self._get_business_profile(collection_id)
        sms_sender  = settings.SMS_SENDER_ID
        sender_name = (profile.display_name  if profile else None) or "PesaGrid"
        email_domain_or_addr = (profile.email_from if profile else None) or settings.RESEND_FROM_EMAIL
        is_platform_email = event_type.startswith("auth.")  # auth emails come from PesaGrid, not the business
        email_from  = _build_from_email(sender_name, email_domain_or_addr, platform=is_platform_email)

        # ── SMS ───────────────────────────────────────────────────────────────
        if phone:
            body_tpl, _, tmpl_id = self._resolve_template(collection_id, event_type, "sms")
            if not body_tpl:
                logger.info(f"No SMS template for '{event_type}' — collection {collection_id} skipped")
                self._log(collection_id, payer_id, NotifChannel.SMS, phone,
                          event_type, "", None, NotifStatus.SKIPPED,
                          error_msg="No template configured")
            else:
                rendered = render(body_tpl, {**context, "sender_name": sender_name})
                try:
                    result  = await send_sms(phone, rendered)
                    err     = result.get("error")
                    self._log(collection_id, payer_id, NotifChannel.SMS, phone,
                              event_type, rendered, None,
                              NotifStatus.FAILED if err else NotifStatus.SENT,
                              tmpl_id, error_msg=err)
                except Exception as e:
                    self._log(collection_id, payer_id, NotifChannel.SMS, phone,
                              event_type, rendered, None, NotifStatus.FAILED,
                              tmpl_id, error_msg=str(e))

        # ── Email ─────────────────────────────────────────────────────────────
        if email:
            body_tpl, subject, tmpl_id = self._resolve_template(collection_id, event_type, "email")
            if not body_tpl:
                logger.info(f"No email template for '{event_type}' — collection {collection_id} skipped")
                self._log(collection_id, payer_id, NotifChannel.EMAIL, email,
                          event_type, "", None, NotifStatus.SKIPPED,
                          error_msg="No template configured")
            else:
                rendered      = render(body_tpl, {**context, "sender_name": sender_name})
                final_subject = (
                    email_subject
                    or (render(subject, context) if subject else event_type.replace(".", " ").title())
                )
                try:
                    result = await send_email(email, final_subject, rendered, from_email=email_from)
                    self._log(collection_id, payer_id, NotifChannel.EMAIL, email,
                              event_type, rendered, final_subject,
                              NotifStatus.SENT if not result.get("skipped") else NotifStatus.SKIPPED,
                              tmpl_id, provider_ref=result.get("id"))
                except Exception as e:
                    self._log(collection_id, payer_id, NotifChannel.EMAIL, email,
                              event_type, rendered, final_subject,
                              NotifStatus.FAILED, tmpl_id, error_msg=str(e))
