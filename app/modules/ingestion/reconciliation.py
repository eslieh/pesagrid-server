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
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.dependancies import SessionLocal
from app.modules.ingestion.models import Transaction, TransactionStatus, CollectionPoint, TransactionAllocation
from app.modules.obligations.models import Obligation, ObligationStatus, Payer
from app.core.timezone import now_nairobi

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

    def _find_collection_point(self, collection_id: uuid.UUID, account_no: str) -> Optional[CollectionPoint]:
        """Look up a virtual account target (bus, campaign, etc.)."""
        return (
            self.db.query(CollectionPoint)
            .filter(
                CollectionPoint.collection_id == collection_id,
                CollectionPoint.account_no == account_no,
                CollectionPoint.is_active.is_(True),
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
        current_balance = Decimal(str(obligation.balance))
        overflow = Decimal("0")
        
        if amount > current_balance:
            overflow = amount - current_balance
            amount_to_apply = current_balance
        else:
            amount_to_apply = amount

        obligation.amount_paid = Decimal(str(obligation.amount_paid)) + amount_to_apply
        obligation.balance = Decimal(str(obligation.amount_due)) - Decimal(str(obligation.amount_paid))

        if obligation.balance <= 0:
            obligation.status = ObligationStatus.SETTLED
            obligation.balance = Decimal("0")
        else:
            obligation.status = ObligationStatus.PARTIAL

        # Handle overflow: add to payer's credit balance
        if overflow > 0 and obligation.payer:
            obligation.payer.credit_balance = Decimal(str(obligation.payer.credit_balance or 0)) + overflow
            logger.info(f"💰 Overflow of {overflow} added to Payer {obligation.payer.id} credit balance")

        obligation.updated_at = now_nairobi()

        # Link the transaction
        transaction.matched_obligation_id = obligation.id
        transaction.matched_at = now_nairobi()
        transaction.status = TransactionStatus.MATCHED

        # Record allocation for perfect rollback later
        allocation = TransactionAllocation(
            transaction_id=transaction.id,
            obligation_id=obligation.id,
            payer_id=obligation.payer_id,
            amount_applied=amount_to_apply,
            amount_credited=overflow
        )
        self.db.add(allocation)

        self.db.commit()

        logger.info(
            f"✅ Matched txn {transaction.id} → obligation {obligation.id} "
            f"| paid={obligation.amount_paid} balance={obligation.balance} "
            f"status={obligation.status.value}"
        )

    def reconcile(self, transaction_id: uuid.UUID) -> str:
        """
        Main reconciliation entry point. Reads the transaction from DB,
        finds the best obligation, or falls back to a CollectionPoint.

        Returns: "ALREADY_PROCESSED", "MATCHED", "CATEGORIZED", or "UNMATCHED"
        """
        txn = self.db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            logger.warning(f"Reconcile: transaction {transaction_id} not found")
            return "ALREADY_PROCESSED"

        if txn.status in (TransactionStatus.MATCHED, TransactionStatus.CATEGORIZED, TransactionStatus.DUPLICATE):
            logger.info(f"Reconcile: transaction {transaction_id} already in state {txn.status.value}")
            if txn.status == TransactionStatus.MATCHED: return "ALREADY_MATCHED"
            if txn.status == TransactionStatus.CATEGORIZED: return "ALREADY_CATEGORIZED"
            return "ALREADY_PROCESSED"


        if not txn.account_no and not txn.narration:
            logger.warning(f"Reconcile: transaction {transaction_id} has no account_no or narration — marking UNMATCHED")
            txn.status = TransactionStatus.UNMATCHED
            self.db.commit()
            return "UNMATCHED"

        collection_id = txn.collection_id
        # Prioritize narration for matching, fallback to account_no (BillRefNumber)
        reference = (txn.narration or txn.account_no).strip().upper()
        amount = Decimal(str(txn.amount))

        # 1. High-Volume Path: Try Fleet/Campaign (CollectionPoint) first
        cp = self._find_collection_point(collection_id, reference)
        if cp:
            txn.collection_point_id = cp.id
            txn.status = TransactionStatus.CATEGORIZED
            self.db.commit()
            logger.info(f"📁 Fleet Matched: txn {txn.id} → CP {cp.name}")
            return "CATEGORIZED"




        # 2. Invoicing Path: Try to find a specific Obligation
        payer = self._find_payer(collection_id, reference)
        if payer:
            obligation = self._find_best_obligation(payer.id, amount)
            if obligation:
                self._apply_payment(obligation, amount, txn)
                return "MATCHED"

        # 3. No match found
        logger.warning(
            f"Reconcile: no match for reference='{reference}' "
            f"in collection {collection_id} — marking UNMATCHED"
        )
        txn.status = TransactionStatus.UNMATCHED
        self.db.commit()
        return "UNMATCHED"

    def rollback_payment(self, transaction_id: uuid.UUID) -> bool:
        """
        Reverse the effects of a matched transaction.
        Uses TransactionAllocation to perfectly undo amount_paid and credit_balance.
        """
        txn = self.db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn or txn.status not in (TransactionStatus.MATCHED, TransactionStatus.CATEGORIZED):
            return False

        # If it was CATEGORIZED (CollectionPoint), just clear the link
        if txn.status == TransactionStatus.CATEGORIZED:
            txn.collection_point_id = None
            txn.status = TransactionStatus.UNMATCHED
            self.db.commit()
            return True

        # If it was MATCHED, find the allocation
        allocation = (
            self.db.query(TransactionAllocation)
            .filter(TransactionAllocation.transaction_id == txn.id)
            .first()
        )
        if not allocation:
            logger.warning(f"Rollback: No allocation found for matched txn {txn.id}")
            # Fallback: if no allocation, we can't safely rollback? 
            # For now, let's just clear the txn status as a best effort
            txn.matched_obligation_id = None
            txn.status = TransactionStatus.UNMATCHED
            self.db.commit()
            return True

        # 1. Reverse amount_paid on Obligation
        if allocation.obligation_id:
            ob = self.db.query(Obligation).filter(Obligation.id == allocation.obligation_id).first()
            if ob:
                ob.amount_paid = Decimal(str(ob.amount_paid)) - Decimal(str(allocation.amount_applied))
                ob.balance = Decimal(str(ob.amount_due)) - Decimal(str(ob.amount_paid))
                
                # Recalculate status
                if ob.amount_paid <= 0:
                    ob.status = ObligationStatus.PENDING
                    ob.amount_paid = 0
                else:
                    ob.status = ObligationStatus.PARTIAL
                
                logger.info(f"🔄 Rolled back {allocation.amount_applied} from Obligation {ob.id}")

        # 2. Reverse credit_balance on Payer
        if allocation.payer_id and allocation.amount_credited > 0:
            payer = self.db.query(Payer).filter(Payer.id == allocation.payer_id).first()
            if payer:
                payer.credit_balance = Decimal(str(payer.credit_balance or 0)) - Decimal(str(allocation.amount_credited))
                logger.info(f"🔄 Rolled back {allocation.amount_credited} overflow credit from Payer {payer.id}")

        # 3. Clean up
        self.db.delete(allocation)
        txn.matched_obligation_id = None
        txn.matched_at = None
        txn.status = TransactionStatus.UNMATCHED
        
        self.db.commit()
        return True

    def manual_match_transaction(
        self, 
        transaction_id: uuid.UUID, 
        obligation_id: Optional[uuid.UUID] = None, 
        collection_point_id: Optional[uuid.UUID] = None
    ) -> str:
        """
        Force a transaction to match a specific target.
        If already matched, it rolls back first.
        """
        txn = self.db.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not txn:
            raise ValueError("Transaction not found")

        # 1. Rollback if already matched/categorized
        if txn.status in (TransactionStatus.MATCHED, TransactionStatus.CATEGORIZED):
            self.rollback_payment(transaction_id)

        # 2. Apply new match
        if collection_point_id:
            cp = self.db.query(CollectionPoint).filter(CollectionPoint.id == collection_point_id).first()
            if not cp:
                raise ValueError("Collection point not found")
            txn.collection_point_id = cp.id
            txn.status = TransactionStatus.CATEGORIZED
            self.db.commit()
            return "CATEGORIZED"

        if obligation_id:
            ob = self.db.query(Obligation).filter(Obligation.id == obligation_id).first()
            if not ob:
                raise ValueError("Obligation not found")
            self._apply_payment(ob, Decimal(str(txn.amount)), txn)
            return "MATCHED"

        return "UNMATCHED"




async def reconcile_transaction(transaction_id: str, force_notify: bool = False):
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
        result_status = await asyncio.to_thread(service.reconcile, uuid.UUID(transaction_id))

        # 1. Handle already processed transactions
        if result_status == "ALREADY_PROCESSED":
            return True

        if result_status in ("ALREADY_MATCHED", "ALREADY_CATEGORIZED"):
            if not force_notify:
                return True
            # If forcing, we strip the prefix to proceed with notification logic
            result_status = result_status.replace("ALREADY_", "")

        # 2. Proceed with notifications (Categorized vs Matched)
        if result_status == "CATEGORIZED":
            from app.modules.ingestion.models import Transaction as Txn
            from app.modules.ingestion.models import CollectionPoint as CP
            txn = db.query(Txn).filter(Txn.id == uuid.UUID(transaction_id)).first()
            if txn and txn.collection_point_id and txn.phone:
                cp = db.query(CP).filter(CP.id == txn.collection_point_id).first()
                if cp and cp.sms_acknowledgement:
                    payload = {
                        "transaction_id":        str(txn.id),
                        "collection_id":         str(txn.collection_id),
                        "collection_point_name": cp.name,
                        "account_no":            txn.account_no or "",
                        "amount":                float(txn.amount),
                        "currency":              txn.currency,
                        "phone":                 txn.phone,
                        "payer_name":            txn.payer_name or "",
                        "psp_ref":               txn.psp_ref or "",
                        "psp_type":              txn.psp_type or "",
                        "ingested_at":           txn.ingested_at.isoformat() if txn.ingested_at else "",
                    }
                    try:
                        await _publisher.publish_event(
                            event_type=EventType.PAYMENT_CATEGORIZED,
                            payload=payload,
                            priority=Priority.MEDIUM,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to publish PAYMENT_CATEGORIZED event: {e}")
            return True

        # Re-read for event payload
        from app.modules.ingestion.models import Transaction as Txn
        txn = db.query(Txn).filter(Txn.id == uuid.UUID(transaction_id)).first()
        if not txn:
            return False

        # Build event payload (picked up by notification worker)
        payload = {
            "transaction_id":    str(txn.id),
            "collection_id":     str(txn.collection_id),
            "account_no":        txn.account_no or "",
            "amount":            float(txn.amount),
            "currency":          txn.currency,
            "phone":             txn.phone or "",
            "payer_name":        txn.payer_name or "",
            "psp_type":          txn.psp_type or "",
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
                    "due_date":          ob.due_date.isoformat() if ob.due_date else "",
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
                if ob and ob.status == ObligationStatus.SETTLED
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

        # Billing hook: deduct per-reconciliation fee on a successful match
        if result_status == "MATCHED":
            try:
                await _publisher.publish_event(
                    event_type=EventType.BILLING_RECON_DONE,
                    payload={
                        "collection_id": str(txn.collection_id),
                        "count": 1,
                        "meta": {"transaction_id": str(txn.id)},
                    },
                    priority=Priority.LOW,
                )
            except Exception as e:
                logger.debug(f"billing recon event publish failed (non-critical): {e}")

        return result_status == "MATCHED"

    except Exception as e:
        logger.error(f"❌ Reconciliation failed for transaction {transaction_id}: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()


