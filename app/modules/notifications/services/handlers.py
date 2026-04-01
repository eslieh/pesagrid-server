"""
Notification event handlers — called by the RabbitMQ worker.

Handles:
  payment.matched    → send payment receipt to payer
  payment.partial    → send partial payment notice to payer
  payment.unmatched  → alert business owner
  obligation.created → notify payer of new obligation
"""
import uuid
import logging
from app.rabbitmq import MessageEnvelope

logger = logging.getLogger(__name__)


async def _dispatch(event_type: str, payload: dict) -> None:
    """Open a DB session and dispatch via NotificationDispatcher."""
    from app.core.dependancies import SessionLocal
    from app.modules.notifications.services.dispatcher import NotificationDispatcher
    from app.modules.notifications.services.renderer import build_context

    db = SessionLocal()
    try:
        dispatcher = NotificationDispatcher(db)
        collection_id = uuid.UUID(payload.get("collection_id", ""))
        payer_id_raw = payload.get("payer_id")
        payer_id = uuid.UUID(payer_id_raw) if payer_id_raw else None
        phone = payload.get("phone") or None
        email = payload.get("email") or None

        context = build_context(
            payer_name=payload.get("payer_name", ""),
            amount_due=payload.get("amount_due", 0),
            amount_paid=payload.get("amount", 0),      # The specific transaction amount
            total_paid=payload.get("amount_paid", 0), # The cumulative total paid so far
            balance=payload.get("balance", 0),
            due_date=payload.get("due_date", ""),
            account_no=payload.get("account_no", ""),
            description=payload.get("description", ""),
            currency=payload.get("currency", "KES"),
            psp_ref=payload.get("psp_ref", ""),
            transaction_date=payload.get("ingested_at", ""),
            settled_by=payload.get("psp_type", ""),
            phone=phone or "",
            login_url=payload.get("login_url", ""),
            otp=payload.get("otp", ""),
            reset_url=payload.get("reset_url", ""),
            is_rollover=payload.get("is_rollover", False),
            previous_arrears=payload.get("previous_arrears", 0.0),
            penalty=payload.get("penalty", 0.0),
            collection_point_name=payload.get("collection_point_name", ""),
        )


        await dispatcher.dispatch(
            event_type=event_type,
            collection_id=collection_id,
            context=context,
            phone=phone,
            email=email,
            payer_id=payer_id,
        )
    except Exception as e:
        logger.error(f"❌ Dispatch failed for {event_type}: {e}")
    finally:
        db.close()


async def _notify_business(event_type: str, payload: dict) -> None:
    """Notify the business owner if they have opted in."""
    from app.core.dependancies import SessionLocal
    from app.modules.accounts.models import BusinessProfile
    from app.modules.notifications.services.send_email import send_email
    from app.modules.notifications.services.send_sms import send_sms
    from app.modules.notifications.services.renderer import wrap_in_template
    import uuid

    db = SessionLocal()
    try:
        collection_id_raw = payload.get("collection_id")
        if not collection_id_raw:
            return
        collection_id = uuid.UUID(collection_id_raw)
        profile = db.query(BusinessProfile).filter(BusinessProfile.collection_id == collection_id).first()
        if not profile or getattr(profile, "meta", None) is None:
            return

        if not profile.meta.get("payment_notifications_enabled"):
            return

        channels = profile.meta.get("payment_notification_channels", ["email"])
        
        amount = payload.get("amount", 0)
        currency = payload.get("currency", "KES")
        payer_name = payload.get("payer_name", "Unknown")
        account_no = payload.get("account_no", "")
        
        message_body = f"New payment received: {currency} {amount} from {payer_name} (Acc: {account_no}). Status: {event_type.split('.')[-1]}"
        subject = f"Payment Received - {currency} {amount}"

        if "email" in channels and profile.email:
            try:
                # Always send platform-to-business notifications from PesaGrid
                from app.modules.notifications.services.dispatcher import _build_from_email
                from app.core.config import settings
                email_from = _build_from_email("PesaGrid", settings.RESEND_FROM_EMAIL or "mails.ryfty.net", platform=True)

                wrapped_body = wrap_in_template(message_body, business_name="PesaGrid")
                await send_email(profile.email, subject, wrapped_body, from_email=email_from)
                logger.info(f"📧 Sent payment notification email to business {profile.email}")
            except Exception as e:
                logger.error(f"Failed to email business {profile.email}: {e}")
                
        if "sms" in channels and profile.phone:
            try:
                # Assuming send_sms(to, text_body)
                await send_sms(profile.phone, message_body)
                logger.info(f"📱 Sent payment notification SMS to business {profile.phone}")
            except Exception as e:
                logger.error(f"Failed to SMS business {profile.phone}: {e}")

    except Exception as e:
        logger.error(f"❌ Business notification failed for {event_type}: {e}")
    finally:
        db.close()


async def handle_payment_matched(envelope: MessageEnvelope) -> None:
    logger.info("✅ payment.matched — sending receipt to payer")
    await _dispatch("payment.matched", envelope.payload)
    await _notify_business("payment.matched", envelope.payload)


async def handle_payment_partial(envelope: MessageEnvelope) -> None:
    logger.info("⚡ payment.partial — sending partial notice to payer")
    await _dispatch("payment.partial", envelope.payload)
    await _notify_business("payment.partial", envelope.payload)


async def handle_payment_unmatched(envelope: MessageEnvelope) -> None:
    logger.info("⚠️  payment.unmatched — alerting business owner")
    await _dispatch("payment.unmatched", envelope.payload)
    await _notify_business("payment.unmatched", envelope.payload)


async def handle_obligation_created(envelope: MessageEnvelope) -> None:
    logger.info("📋 obligation.created — notifying payer")
    await _dispatch("obligation.created", envelope.payload)


async def handle_obligation_due(envelope: MessageEnvelope) -> None:
    logger.info("⏱️ obligation.due — sending payment reminder to payer")
    await _dispatch("obligation.due", envelope.payload)


async def handle_obligation_cancelled(envelope: MessageEnvelope) -> None:
    logger.info("🗑️ obligation.cancelled — notifying payer of cancellation")
    await _dispatch("obligation.cancelled", envelope.payload)


async def handle_payment_categorized(envelope: MessageEnvelope) -> None:
    """Send acknowledgement SMS for a collection point payment (opt-in)."""
    logger.info("📁 payment.categorized — sending acknowledgement SMS to payer")
    # The payload already contains phone + collection_point_name from reconciliation.
    # We inject collection_point_name into the context so templates / fallback can use it.
    payload = envelope.payload
    payload.setdefault("payer_name", payload.get("payer_name", "Customer"))
    await _dispatch("payment.categorized", payload)
    await _notify_business("payment.categorized", payload)
