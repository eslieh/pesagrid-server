"""
Ingestion event handlers — called by the RabbitMQ worker.

Handles:
  webhook.mpesa    → normalize → ingest → reconcile
  payment.received → reconcile (for manually ingested transactions)
"""
import uuid
import logging
from app.rabbitmq import MessageEnvelope

logger = logging.getLogger(__name__)


async def handle_webhook_mpesa(envelope: MessageEnvelope) -> None:
    """Normalize → ingest → reconcile a raw M-PESA callback payload."""
    from app.core.dependancies import SessionLocal
    from app.modules.ingestion.services import IngestionService
    from app.modules.ingestion.normalizers.mpesa import normalize_c2b, normalize_stk
    from app.modules.ingestion.reconciliation import reconcile_transaction

    collection_id = envelope.payload.get("collection_id")
    raw = envelope.payload.get("raw", {})
    logger.info(f"📥 webhook.mpesa received for collection {collection_id}")

    # ── Normalize ──────────────────────────────────────────────────────────────
    if "Body" in raw and "stkCallback" in raw.get("Body", {}):
        normalized = normalize_stk(raw)
        if normalized is None:
            logger.info(f"STK push not completed for {collection_id} — skipping")
            return
    elif "TransID" in raw:
        normalized = normalize_c2b(raw)
    else:
        logger.warning(f"Unknown M-PESA shape for {collection_id}: {list(raw.keys())}")
        return

    # ── Ingest ─────────────────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        service = IngestionService(db=db, collection_id=uuid.UUID(collection_id))
        txn, is_new = await service.ingest_normalized(normalized, psp_type="mpesa")
    except Exception as e:
        logger.error(f"Ingestion failed for {collection_id}: {e}")
        return
    finally:
        db.close()

    # ── Reconcile ──────────────────────────────────────────────────────────────
    if is_new:
        await reconcile_transaction(str(txn.id))


async def handle_payment_received(envelope: MessageEnvelope) -> None:
    """Reconcile an already-ingested transaction."""
    from app.modules.ingestion.reconciliation import reconcile_transaction

    transaction_id = envelope.payload.get("transaction_id")
    if not transaction_id:
        logger.error(f"payment.received missing transaction_id: {envelope.payload}")
        return
    logger.info(f"💳 Reconciling transaction {transaction_id}")
    await reconcile_transaction(transaction_id)
