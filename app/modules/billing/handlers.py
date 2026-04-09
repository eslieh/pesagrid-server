"""
Billing RabbitMQ handlers — consume usage events and deduct from tenant wallets.

Handlers registered in worker.py:
  billing.notification.sent    → deduct per-notification fee
  billing.invoice.created      → deduct per-invoice fee
"""
import logging
import uuid

from app.rabbitmq import MessageEnvelope
from app.modules.billing.models import WalletTxEvent

logger = logging.getLogger(__name__)



async def handle_billing_notification_sent(envelope: MessageEnvelope) -> None:
    """
    Deduct the per-notification fee from the tenant's wallet.

    Expected payload:
      {
        "collection_id": "<uuid>",
        "channel": "sms" | "email",
        "count": 1,
        "meta": {...}          # optional: payer_id, event_type, etc.
      }
    """
    from app.core.dependancies import SessionLocal
    from app.modules.billing.services import BillingService

    collection_id_str = envelope.payload.get("collection_id")
    channel = envelope.payload.get("channel", "sms")
    count = int(envelope.payload.get("count", 1))
    meta = envelope.payload.get("meta", {})

    if not collection_id_str:
        logger.error("billing_notification_sent: missing collection_id")
        return

    event_type = WalletTxEvent.SMS if channel == "sms" else WalletTxEvent.EMAIL

    db = SessionLocal()
    try:
        svc = BillingService(db=db, collection_id=uuid.UUID(collection_id_str))
        svc.deduct_usage(event_type=event_type, count=count, meta={**meta, "channel": channel})
    except Exception as exc:
        logger.error(f"billing_notification_sent failed for {collection_id_str}: {exc}")
    finally:
        db.close()
async def handle_billing_invoice_created(envelope: MessageEnvelope) -> None:
    """
    Deduct the per-invoice/obligation fee from the tenant's wallet.

    Expected payload:
      {
        "collection_id": "<uuid>",
        "obligation_id": "<uuid>",
        "meta": {...}
      }
    """
    from app.core.dependancies import SessionLocal
    from app.modules.billing.services import BillingService

    collection_id_str = envelope.payload.get("collection_id")
    meta = envelope.payload.get("meta", {})

    if not collection_id_str:
        logger.error("billing_invoice_created: missing collection_id")
        return

    db = SessionLocal()
    try:
        svc = BillingService(db=db, collection_id=uuid.UUID(collection_id_str))
        svc.deduct_usage(event_type=WalletTxEvent.INVOICE, count=1, meta=meta)
    except Exception as exc:
        logger.error(f"billing_invoice_created failed for {collection_id_str}: {exc}")
    finally:
        db.close()
