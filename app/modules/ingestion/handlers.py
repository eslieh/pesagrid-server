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

    # Ingestion already publishes a payment.received event, which triggers 
    # reconciliation via handle_payment_received. Removing the direct call 
    # here to prevent double processing.
    pass


    logger.info(f"💳 Reconciling transaction {transaction_id}")
    await reconcile_transaction(transaction_id)


async def handle_payment_received(envelope: MessageEnvelope) -> None:
    """Reconcile an already-ingested transaction."""
    from app.modules.ingestion.reconciliation import reconcile_transaction

    transaction_id = envelope.payload.get("transaction_id")
    if not transaction_id:
        logger.error(f"payment.received missing transaction_id: {envelope.payload}")
        return
    logger.info(f"💳 Reconciling transaction {transaction_id}")
    await reconcile_transaction(transaction_id)


async def handle_bank_k_till(envelope: MessageEnvelope) -> None:
    """Resolve collection → normalize → ingest → reconcile Bank-K Till."""
    from app.core.dependancies import SessionLocal
    from app.modules.accounts.models import PSPConfig
    from app.modules.ingestion.services import IngestionService
    from app.modules.ingestion.normalizers.bank import normalize_bank_k_till
    
    raw = envelope.payload.get("raw", {})
    collection_id = envelope.payload.get("collection_id")
    
    if not collection_id:
        logger.error(f"❌ Bank-K Till failed: Missing collection_id in payload")
        return

    db = SessionLocal()
    try:
        normalized = normalize_bank_k_till(raw)
        service = IngestionService(db=db, collection_id=uuid.UUID(collection_id))
        await service.ingest_normalized(normalized, psp_type="kcb")
        logger.info(f"📥 Bank-K Till ingested for collection {collection_id}")
    except Exception as e:
        logger.error(f"Ingestion failed for Bank-K Till: {e}")
    finally:
        db.close()


async def handle_bank_k_account(envelope: MessageEnvelope) -> None:
    """Resolve collection → normalize → ingest → reconcile Bank-K Account."""
    from app.core.dependancies import SessionLocal
    from app.modules.accounts.models import PSPConfig
    from app.modules.ingestion.services import IngestionService
    from app.modules.ingestion.normalizers.bank import normalize_bank_k_account
    
    raw = envelope.payload.get("raw", {})
    collection_id = envelope.payload.get("collection_id")
    
    if not collection_id:
        logger.error(f"❌ Bank-K Account failed: Missing collection_id in payload")
        return

    db = SessionLocal()
    try:
        normalized = normalize_bank_k_account(raw)
        service = IngestionService(db=db, collection_id=uuid.UUID(collection_id))
        await service.ingest_normalized(normalized, psp_type="kcb")
        logger.info(f"📥 Bank-K Account ingested for collection {collection_id}")
    except Exception as e:
        logger.error(f"Ingestion failed for Bank-K Account: {e}")
    finally:
        db.close()


async def handle_transaction_match(envelope: MessageEnvelope) -> None:
    """Manually match or re-assign a transaction to an obligation or collection point."""
    from app.core.dependancies import SessionLocal
    from app.modules.ingestion.reconciliation import ReconciliationService, reconcile_transaction

    payload = envelope.payload
    transaction_id = payload.get("transaction_id")
    obligation_id = payload.get("obligation_id")
    collection_point_id = payload.get("collection_point_id")

    if not transaction_id:
        logger.error("payment.manual.match missing transaction_id")
        return

    db = SessionLocal()
    try:
        service = ReconciliationService(db=db)
        service.manual_match_transaction(
            transaction_id=uuid.UUID(transaction_id),
            obligation_id=uuid.UUID(obligation_id) if obligation_id else None,
            collection_point_id=uuid.UUID(collection_point_id) if collection_point_id else None
        )
        logger.info(f"🎯 Transaction {transaction_id} manually matched (ob={obligation_id}, cp={collection_point_id})")

        # After matching, we trigger the normal notification/billing cycle
        await reconcile_transaction(transaction_id, force_notify=True)

    except Exception as e:
        logger.error(f"Manual match failed for {transaction_id}: {e}")
        db.rollback()
    finally:
        db.close()

