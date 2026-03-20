"""
Reconciliation Engine

Consumes payment.received events from RabbitMQ and matches
transactions to open obligations.

Matching logic (in priority order):
  1. Find payer by (collection_id, account_no)
  2. Find the oldest PENDING/PARTIAL obligation for that payer
     where amount matches or is a partial payment
  3. Apply payment: update amount_paid, balance, and status
  4. If no obligation found → mark transaction UNMATCHED
"""
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.core.dependancies import SessionLocal
from app.modules.ingestion.models import Transaction, TransactionStatus
from app.modules.obligations.models import Obligation, ObligationStatus, Payer

logger = logging.getLogger(__name__)


class ReconciliationService:
    def __init__(self, db: Session):
        self.db = db

    def _find_payer(self, collection_id: uuid.UUID, account_no: str) -> Optional[Payer]:
        """Look up an active payer by their account number within the tenant workspace."""
        return (
            self.db.query(Payer)
            .filter(
                Payer.collection_id == collection_id,
                Payer.account_no == account_no,
                Payer.is_active.is_(True),
            )
            .first()
        )

    def _find_best_obligation(self, payer_id: uuid.UUID, amount: Decimal) -> Optional[Obligation]:
        """
        Find the best open obligation to match against.

        Priority:
        1. Exact amount match (PENDING) — most confident match
        2. Oldest PARTIAL with remaining balance ≥ amount
        3. Oldest PENDING (even if amount differs — partial payment)
        """
        open_statuses = [ObligationStatus.PENDING, ObligationStatus.PARTIAL, ObligationStatus.OVERDUE]

        candidates = (
            self.db.query(Obligation)
            .filter(
                Obligation.payer_id == payer_id,
                Obligation.status.in_(open_statuses),
            )
            .order_by(Obligation.due_date.asc().nullslast(), Obligation.created_at.asc())
            .all()
        )

        if not candidates:
            return None

        # 1. Exact amount match on a PENDING obligation
        for ob in candidates:
            if ob.status == ObligationStatus.PENDING and Decimal(str(ob.balance)) == amount:
                return ob

        # 2. Obligation where this is a partial payment (amount < balance)
        for ob in candidates:
            if Decimal(str(ob.balance)) >= amount:
                return ob

        # 3. Fall back to oldest open obligation regardless
        return candidates[0]

    def _apply_payment(self, obligation: Obligation, amount: Decimal, transaction: Transaction):
        """Apply the payment amount to the obligation and update its status."""
        obligation.amount_paid = Decimal(str(obligation.amount_paid)) + amount
        obligation.balance = Decimal(str(obligation.amount_due)) - Decimal(str(obligation.amount_paid))

        if obligation.balance <= 0:
            obligation.status = ObligationStatus.PAID
            obligation.balance = Decimal("0")  # clamp — don't go negative
        else:
            obligation.status = ObligationStatus.PARTIAL

        obligation.updated_at = datetime.utcnow()

        # Link the transaction
        transaction.matched_obligation_id = obligation.id
        transaction.matched_at = datetime.utcnow()
        transaction.status = TransactionStatus.MATCHED

        self.db.commit()

        logger.info(
            f"✅ Matched txn {transaction.id} → obligation {obligation.id} "
            f"| paid={obligation.amount_paid} balance={obligation.balance} "
            f"status={obligation.status.value}"
        )

    def reconcile(self, transaction_id: uuid.UUID) -> bool:
        """
        Main reconciliation entry point. Reads the transaction from DB,
        finds the best obligation, and applies the payment.

        Returns True if matched, False if unmatched.
        """
        txn = self.db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            logger.warning(f"Reconcile: transaction {transaction_id} not found")
            return False

        if txn.status in (TransactionStatus.MATCHED, TransactionStatus.DUPLICATE):
            logger.info(f"Reconcile: skipping already-{txn.status.value} transaction {transaction_id}")
            return True

        if not txn.account_no:
            logger.warning(f"Reconcile: transaction {transaction_id} has no account_no — marking UNMATCHED")
            txn.status = TransactionStatus.UNMATCHED
            self.db.commit()
            return False

        collection_id = txn.collection_id
        account_no = txn.account_no
        amount = Decimal(str(txn.amount))

        # 1. Find payer
        payer = self._find_payer(collection_id, account_no)
        if not payer:
            logger.warning(
                f"Reconcile: no active payer for account_no='{account_no}' "
                f"in collection {collection_id} — marking UNMATCHED"
            )
            txn.status = TransactionStatus.UNMATCHED
            self.db.commit()
            return False

        # 2. Find best obligation
        obligation = self._find_best_obligation(payer.id, amount)
        if not obligation:
            logger.warning(
                f"Reconcile: payer {payer.id} ({payer.name}) has no open obligations "
                f"— marking UNMATCHED"
            )
            txn.status = TransactionStatus.UNMATCHED
            self.db.commit()
            return False

        # 3. Apply payment
        self._apply_payment(obligation, amount, txn)
        return True


async def reconcile_transaction(transaction_id: str):
    """
    Async wrapper called by the worker and the webhook BackgroundTask.
    Opens its own DB session, runs reconciliation, then publishes the
    appropriate notification event.
    """
    from app.rabbitmq.publisher import BasePublisher
    from app.rabbitmq.types import EventType, Priority

    _publisher = BasePublisher(service_name="reconciliation-service")

    db = SessionLocal()
    try:
        service = ReconciliationService(db)
        # Re-fetch transaction after reconcile to get updated state
        matched = service.reconcile(uuid.UUID(transaction_id))

        # Re-read for event payload
        from app.modules.ingestion.models import Transaction as Txn
        txn = db.query(Txn).filter(Txn.id == uuid.UUID(transaction_id)).first()
        if not txn:
            return matched

        # Build event payload (picked up by notification worker)
        payload = {
            "transaction_id":    str(txn.id),
            "collection_id":     str(txn.collection_id),
            "account_no":        txn.account_no or "",
            "amount":            float(txn.amount),
            "currency":          txn.currency,
            "phone":             txn.phone or "",
            "payer_name":        txn.payer_name or "",
            "psp_ref":           txn.psp_ref or "",
            "ingested_at":       txn.ingested_at.isoformat() if txn.ingested_at else "",
        }

        if txn.matched_obligation_id:
            from app.modules.obligations.models import Obligation as Ob, Payer as P
            ob = db.query(Ob).filter(Ob.id == txn.matched_obligation_id).first()
            payer = db.query(P).filter(P.id == ob.payer_id).first() if ob else None
            if ob:
                payload.update({
                    "obligation_id":  str(ob.id),
                    "amount_due":     float(ob.amount_due),
                    "amount_paid":    float(ob.amount_paid),
                    "balance":        float(ob.balance),
                    "description":    ob.description or "",
                    "obligation_status": ob.status.value,
                })
            if payer:
                payload.update({
                    "payer_id":   str(payer.id),
                    "payer_name": payer.name,
                    "phone":      payer.phone or txn.phone or "",
                    "email":      payer.email or "",
                })

            event = (
                EventType.PAYMENT_MATCHED
                if ob and ob.status.value == "paid"
                else EventType.PAYMENT_PARTIAL
            )
        else:
            event = EventType.PAYMENT_UNMATCHED

        try:
            await _publisher.publish_event(
                event_type=event,
                payload=payload,
                priority=Priority.HIGH,
            )
        except Exception as e:
            logger.warning(f"Failed to publish {event.value} event: {e}")

        return matched

    except Exception as e:
        logger.error(f"❌ Reconciliation failed for transaction {transaction_id}: {e}")
        db.rollback()
    finally:
        db.close()

