from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from sqlalchemy import asc, desc
import uuid
import logging

from app.modules.accounts.models import PSPConfig, PSPType
from app.modules.ingestion.models import (
    Transaction, TransactionStatus, CollectionPoint, CollectionPointPSP, CollectionPointType
)
from app.modules.ingestion.normalizers.mpesa import NormalizedPayment
from app.modules.ingestion.schema import (
    ManualPaymentCreate, CollectionPointCreate, CollectionPointUpdate,
    CollectionPointPSPCreate,
)
from app.rabbitmq import BasePublisher, EventType, Priority
from sqlalchemy import func
from app.core.timezone import now_nairobi


logger = logging.getLogger(__name__)

publisher = BasePublisher(service_name="ingestion-service")


class AccountsService:
    """Manages PSP configuration for a tenant workspace."""

    def __init__(self, db: Session, collection_id: uuid.UUID, current_user_id: uuid.UUID):
        self.db = db
        self.collection_id = collection_id
        self.current_user_id = current_user_id

    def _build_webhook_url(self, psp_type: PSPType, base_url: str) -> str:
        return f"{base_url}/api/v1/ingest/{self.collection_id}/{psp_type.value}/callback"

    def _get_or_404(self, psp_id: uuid.UUID) -> PSPConfig:
        cfg = (
            self.db.query(PSPConfig)
            .filter(PSPConfig.id == psp_id, PSPConfig.collection_id == self.collection_id)
            .first()
        )
        if not cfg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PSP config not found")
        return cfg

    def create_psp(self, data, base_url: str) -> PSPConfig:
        webhook_url = self._build_webhook_url(data.psp_type, base_url)
        cfg = PSPConfig(
            collection_id=self.collection_id,
            psp_type=data.psp_type,
            display_name=data.display_name,
            paybill=data.paybill,
            webhook_url=webhook_url,
            credentials=data.credentials,
            meta=data.meta,
            created_by=self.current_user_id,
        )
        self.db.add(cfg)
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def list_psps(self, skip: int = 0, limit: int = 50) -> Tuple[int, List[PSPConfig]]:
        q = self.db.query(PSPConfig).filter(PSPConfig.collection_id == self.collection_id)
        total = q.count()
        items = q.order_by(PSPConfig.created_at.desc()).offset(skip).limit(limit).all()
        return total, items

    def get_psp(self, psp_id: uuid.UUID) -> PSPConfig:
        return self._get_or_404(psp_id)

    def update_psp(self, psp_id: uuid.UUID, data) -> PSPConfig:
        cfg = self._get_or_404(psp_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(cfg, field, value)
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def delete_psp(self, psp_id: uuid.UUID) -> None:
        cfg = self._get_or_404(psp_id)
        self.db.delete(cfg)
        self.db.commit()


class CollectionPointService:
    """Manages virtual account targets for bulk collections."""

    def __init__(self, db: Session, collection_id: uuid.UUID):
        self.db = db
        self.collection_id = collection_id

    def _get_or_404(self, cp_id: uuid.UUID) -> CollectionPoint:
        cp = (
            self.db.query(CollectionPoint)
            .filter(CollectionPoint.id == cp_id, CollectionPoint.collection_id == self.collection_id)
            .first()
        )
        if not cp:
            raise HTTPException(status_code=404, detail="Collection point not found")
        return cp

    def get_collection_point(self, cp_id: uuid.UUID) -> CollectionPoint:
        return self._get_or_404(cp_id)

    def create_collection_point(self, data: CollectionPointCreate) -> CollectionPoint:
        account_no = data.account_no.strip().upper()

        # Ensure it's not already used as a Payer account (collision with Invoicing)
        from app.modules.obligations.models import Payer
        existing_payer = (
            self.db.query(Payer)
            .filter(Payer.collection_id == self.collection_id, Payer.account_no == account_no)
            .first()
        )
        if existing_payer:
            raise HTTPException(
                status_code=400,
                detail=f"Account number {account_no} is already assigned to a customer (Invoicing flow)"
            )

        cp = CollectionPoint(
            collection_id=self.collection_id,
            name=data.name,
            account_no=account_no,
            description=data.description,
            cp_type=data.cp_type,
            goal_amount=data.goal_amount,
            currency=data.currency,
            start_date=data.start_date,
            end_date=data.end_date,
            compliance_threshold=data.compliance_threshold,
            is_active=data.is_active,
            sms_acknowledgement=data.sms_acknowledgement,
            meta=data.meta or {},
        )
        try:
            self.db.add(cp)
            self.db.commit()
            self.db.refresh(cp)
            return cp
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"Account number {account_no} already assigned to another collection point"
            )

    def list_collection_points(
        self,
        search: Optional[str] = None,
        cp_type: Optional[CollectionPointType] = None,
        is_active: Optional[bool] = None,
        skip: int = 0,
        limit: int = 50
    ) -> Tuple[int, List[CollectionPoint]]:
        from sqlalchemy import or_
        q = self.db.query(CollectionPoint).filter(CollectionPoint.collection_id == self.collection_id)
        
        if search:
            search_str = f"%{search}%"
            q = q.filter(
                or_(
                    CollectionPoint.name.ilike(search_str),
                    CollectionPoint.account_no.ilike(search_str),
                    CollectionPoint.description.ilike(search_str)
                )
            )
        if cp_type:
            q = q.filter(CollectionPoint.cp_type == cp_type)
        if is_active is not None:
            q = q.filter(CollectionPoint.is_active == is_active)
            
        total = q.count()
        items = q.order_by(CollectionPoint.created_at.desc()).offset(skip).limit(limit).all()
        return total, items

    def get_collection_point_totals(self, cp_id: uuid.UUID):
        """Aggregate total volume collected for this point."""
        cp = self._get_or_404(cp_id)
        total = (
            self.db.query(func.sum(Transaction.amount))
            .filter(Transaction.collection_point_id == cp.id)
            .scalar()
        ) or 0
        return {
            "collection_point_id": cp.id,
            "name": cp.name,
            "account_no": cp.account_no,
            "total_collected": float(total),
        }

    def update_collection_point(self, cp_id: uuid.UUID, data: CollectionPointUpdate) -> CollectionPoint:
        cp = self._get_or_404(cp_id)
        update_data = data.model_dump(exclude_unset=True)

        if "account_no" in update_data and update_data["account_no"]:
            account_no = update_data["account_no"].strip().upper()
            update_data["account_no"] = account_no

            from app.modules.obligations.models import Payer
            existing_payer = (
                self.db.query(Payer)
                .filter(Payer.collection_id == self.collection_id, Payer.account_no == account_no)
                .first()
            )
            if existing_payer:
                raise HTTPException(
                    status_code=400,
                    detail=f"Account number {account_no} is already assigned to a customer (Invoicing flow)"
                )

        for field, value in update_data.items():
            setattr(cp, field, value)
        self.db.commit()
        self.db.refresh(cp)
        return cp

    def delete_collection_point(self, cp_id: uuid.UUID) -> None:
        cp = self._get_or_404(cp_id)
        tx_count = self.db.query(Transaction).filter(Transaction.collection_point_id == cp.id).count()
        if tx_count > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete collection point with associated transactions. Use is_active=False instead."
            )
        self.db.delete(cp)
        self.db.commit()

    # ── PSP channel links ─────────────────────────────────────────────────────

    def add_psp(self, cp_id: uuid.UUID, data: CollectionPointPSPCreate) -> CollectionPointPSP:
        """Link a PSP config to this collection point for channel analytics."""
        cp = self._get_or_404(cp_id)

        # Verify the PSP config belongs to the same tenant
        psp = (
            self.db.query(PSPConfig)
            .filter(PSPConfig.id == data.psp_config_id, PSPConfig.collection_id == self.collection_id)
            .first()
        )
        if not psp:
            raise HTTPException(status_code=404, detail="PSP config not found")

        link = CollectionPointPSP(
            collection_point_id=cp.id,
            psp_config_id=data.psp_config_id,
            label=data.label,
        )
        try:
            self.db.add(link)
            self.db.commit()
            self.db.refresh(link)
            return link
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(status_code=409, detail="This PSP is already linked to the collection point")

    def remove_psp(self, cp_id: uuid.UUID, psp_config_id: uuid.UUID) -> None:
        """Unlink a PSP config from this collection point."""
        cp = self._get_or_404(cp_id)
        link = (
            self.db.query(CollectionPointPSP)
            .filter(
                CollectionPointPSP.collection_point_id == cp.id,
                CollectionPointPSP.psp_config_id == psp_config_id,
            )
            .first()
        )
        if not link:
            raise HTTPException(status_code=404, detail="PSP link not found")
        self.db.delete(link)
        self.db.commit()

    def list_psps(self, cp_id: uuid.UUID) -> List[CollectionPointPSP]:
        """Return all PSP configs linked to this collection point."""
        cp = self._get_or_404(cp_id)
        return (
            self.db.query(CollectionPointPSP)
            .filter(CollectionPointPSP.collection_point_id == cp.id)
            .order_by(CollectionPointPSP.created_at.asc())
            .all()
        )






class IngestionService:
    """Handles payment ingestion — from webhooks and manual entry."""

    def __init__(self, db: Session, collection_id: uuid.UUID):
        self.db = db
        self.collection_id = collection_id

    def _resolve_psp_config(self, psp_type: str) -> Optional[PSPConfig]:
        return (
            self.db.query(PSPConfig)
            .filter(
                PSPConfig.collection_id == self.collection_id,
                PSPConfig.psp_type == psp_type,
                PSPConfig.is_active.is_(True),
            )
            .first()
        )

    def _store_transaction(
        self,
        normalized: NormalizedPayment,
        psp_type: str,
        psp_config_id: Optional[uuid.UUID] = None,
        is_manual: bool = False,
    ) -> Tuple[Transaction, bool]:
        """
        Store a normalized payment. Returns (transaction, is_new).
        If the psp_ref is already known → returns existing with is_new=False.
        Manual entries with no psp_ref always create new records.
        """
        # Deduplication check
        if normalized.psp_ref:
            existing = (
                self.db.query(Transaction)
                .filter(
                    Transaction.collection_id == self.collection_id,
                    Transaction.psp_ref == normalized.psp_ref,
                )
                .first()
            )
            if existing:
                logger.info(f"Duplicate transaction ignored: {normalized.psp_ref}")
                return existing, False

        txn = Transaction(
            collection_id=self.collection_id,
            psp_config_id=psp_config_id,
            psp_type=psp_type,
            psp_ref=normalized.psp_ref,
            amount=normalized.amount,
            currency=normalized.currency,
            phone=normalized.phone,
            account_no=normalized.account_no,
            payer_name=normalized.payer_name,
            raw_payload=normalized.raw_payload,
            status=TransactionStatus.MANUAL if is_manual else TransactionStatus.RAW,
            is_manual=is_manual,
        )
        self.db.add(txn)
        self.db.commit()
        self.db.refresh(txn)
        return txn, True

    async def _publish_payment_received(self, txn: Transaction) -> None:
        try:
            await publisher.publish_event(
                event_type=EventType.PAYMENT_RECEIVED,
                payload={
                    "transaction_id":   str(txn.id),
                    "collection_id":    str(txn.collection_id),
                    "psp_type":         txn.psp_type,
                    "psp_ref":          txn.psp_ref,
                    "amount":           float(txn.amount),
                    "currency":         txn.currency,
                    "phone":            txn.phone,
                    "account_no":       txn.account_no,
                    "payer_name":       txn.payer_name,
                },
                priority=Priority.HIGH,
            )
        except Exception as e:
            # Never let RabbitMQ failure block the HTTP response to the PSP
            logger.warning(f"Failed to publish PAYMENT_RECEIVED event: {e}")

    async def ingest_normalized(
        self,
        normalized: NormalizedPayment,
        psp_type: str,
    ) -> Tuple[Transaction, bool]:
        """Shared ingestion path for all webhook types."""
        psp_config = self._resolve_psp_config(psp_type)
        psp_config_id = psp_config.id if psp_config else None

        txn, is_new = self._store_transaction(
            normalized=normalized,
            psp_type=psp_type,
            psp_config_id=psp_config_id,
        )

        if is_new:
            await self._publish_payment_received(txn)

        return txn, is_new

    async def ingest_manual(
        self, data: ManualPaymentCreate, current_user_id: uuid.UUID
    ) -> Transaction:
        from app.modules.ingestion.normalizers.mpesa import NormalizedPayment as NP
        normalized = NP(
            psp_ref=data.psp_ref,
            amount=data.amount,
            currency=data.currency,
            phone=data.phone or "",
            account_no=data.account_no.strip().upper(),
            payer_name=data.payer_name,
            raw_payload={"manual": True, "note": data.note},
        )
        txn, _ = self._store_transaction(
            normalized=normalized,
            psp_type=data.psp_type.value,
            is_manual=True,
        )
        await self._publish_payment_received(txn)
        return txn

    def list_transactions(
        self,
        account_no: Optional[str] = None,
        psp_type: Optional[str] = None,
        txn_status: Optional[TransactionStatus] = None,
        collection_point_id: Optional[uuid.UUID] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
        sort: str = "date_desc",
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[int, List[Transaction]]:
        q = self.db.query(Transaction).filter(Transaction.collection_id == self.collection_id)
        if account_no:
            q = q.filter(Transaction.account_no == account_no.strip().upper())
        if psp_type:
            q = q.filter(Transaction.psp_type == psp_type)
        if txn_status:
            q = q.filter(Transaction.status == txn_status)
        if collection_point_id:
            q = q.filter(Transaction.collection_point_id == collection_point_id)
        if start_date:
            q = q.filter(Transaction.ingested_at >= start_date)
        if end_date:
            q = q.filter(Transaction.ingested_at <= end_date)
        if amount_min is not None:
            q = q.filter(Transaction.amount >= amount_min)
        if amount_max is not None:
            q = q.filter(Transaction.amount <= amount_max)

        sort_map = {
            "date_desc":   Transaction.ingested_at.desc(),
            "date_asc":    Transaction.ingested_at.asc(),
            "amount_desc": Transaction.amount.desc(),
            "amount_asc":  Transaction.amount.asc(),
        }
        q = q.order_by(sort_map.get(sort, Transaction.ingested_at.desc()))

        total = q.count()
        items = q.offset(skip).limit(limit).all()
        return total, items

    def list_transactions_enriched(
        self,
        account_no: Optional[str] = None,
        psp_type: Optional[str] = None,
        txn_status: Optional[TransactionStatus] = None,
        collection_point_id: Optional[uuid.UUID] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
        sort: str = "date_desc",
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[int, List[dict]]:
        """
        Returns transactions with inline match context (payer + obligation).
        Uses a single JOIN query over existing FK matched_obligation_id —
        no new DB columns required.

        match_confidence is computed at read time:
          1.00  exact account_no + exact amount on a PENDING obligation
          0.85  account_no match + partial amount (PARTIAL status result)
          0.70  account_no match, fallback to oldest open obligation
          None  CATEGORIZED / UNMATCHED / MANUAL
        """
        from app.modules.obligations.models import Obligation, Payer

        q = (
            self.db.query(Transaction, Obligation, Payer)
            .outerjoin(Obligation, Transaction.matched_obligation_id == Obligation.id)
            .outerjoin(Payer, Obligation.payer_id == Payer.id)
            .filter(Transaction.collection_id == self.collection_id)
        )

        if account_no:
            q = q.filter(Transaction.account_no == account_no.strip().upper())
        if psp_type:
            q = q.filter(Transaction.psp_type == psp_type)
        if txn_status:
            q = q.filter(Transaction.status == txn_status)
        if collection_point_id:
            q = q.filter(Transaction.collection_point_id == collection_point_id)
        if start_date:
            q = q.filter(Transaction.ingested_at >= start_date)
        if end_date:
            q = q.filter(Transaction.ingested_at <= end_date)
        if amount_min is not None:
            q = q.filter(Transaction.amount >= amount_min)
        if amount_max is not None:
            q = q.filter(Transaction.amount <= amount_max)

        sort_map = {
            "date_desc":   Transaction.ingested_at.desc(),
            "date_asc":    Transaction.ingested_at.asc(),
            "amount_desc": Transaction.amount.desc(),
            "amount_asc":  Transaction.amount.asc(),
        }
        q = q.order_by(sort_map.get(sort, Transaction.ingested_at.desc()))

        total = q.count()
        rows = q.offset(skip).limit(limit).all()

        result = []
        for txn, ob, payer in rows:
            item = {
                "id":                    txn.id,
                "psp_type":              txn.psp_type,
                "psp_ref":               txn.psp_ref,
                "amount":                float(txn.amount),
                "currency":              txn.currency,
                "account_no":            txn.account_no,
                "payer_name":            txn.payer_name,
                "phone":                 txn.phone,
                "status":                txn.status,
                "is_manual":             txn.is_manual,
                "collection_point_id":   txn.collection_point_id,
                "matched_obligation_id": txn.matched_obligation_id,
                "ingested_at":           txn.ingested_at,
                "matched_confidence":    None,
                "match_reasons":         None,
                "matched_payer":         None,
                "matched_obligation":    None,
            }

            if ob and payer:
                # Derive confidence and reasons from the settled state
                reasons = ["account_no_match"]
                txn_amount = Decimal(str(txn.amount))
                ob_due = Decimal(str(ob.amount_due))
                ob_balance_before = Decimal(str(ob.amount_paid)) - txn_amount + Decimal(str(ob.amount_due))

                if txn_amount == ob_due:
                    confidence = 1.00
                    reasons.append("exact_amount")
                elif ob.status.value == "partial":
                    confidence = 0.85
                    reasons.append("partial_payment")
                else:
                    confidence = 0.70
                    reasons.append("fallback_oldest")

                settlement_type = "full" if ob.balance <= 0 else "partial"

                item["matched_confidence"] = confidence
                item["match_reasons"] = reasons
                item["matched_payer"] = {
                    "payer_id":   payer.id,
                    "payer_name": payer.name,
                    "account_no": payer.account_no,
                }
                item["matched_obligation"] = {
                    "obligation_id":   ob.id,
                    "description":     ob.description,
                    "amount_due":      float(ob.amount_due),
                    "balance":         float(ob.balance),
                    "settlement_type": settlement_type,
                }

            result.append(item)

        return total, result

    def get_transaction(self, txn_id: uuid.UUID) -> Transaction:
        txn = (
            self.db.query(Transaction)
            .filter(Transaction.id == txn_id, Transaction.collection_id == self.collection_id)
            .first()
        )
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")
        return txn

