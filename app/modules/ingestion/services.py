from datetime import datetime
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import uuid
import logging

from app.modules.accounts.models import PSPConfig, PSPType
from app.modules.ingestion.models import Transaction, TransactionStatus
from app.modules.ingestion.normalizers.mpesa import NormalizedPayment
from app.modules.ingestion.schema import ManualPaymentCreate
from app.rabbitmq.publisher import BasePublisher
from app.rabbitmq.types import EventType, Priority

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
        cfg.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def delete_psp(self, psp_id: uuid.UUID) -> None:
        cfg = self._get_or_404(psp_id)
        self.db.delete(cfg)
        self.db.commit()


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
                existing.status = TransactionStatus.DUPLICATE
                self.db.commit()
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
        total = q.count()
        items = q.order_by(Transaction.ingested_at.desc()).offset(skip).limit(limit).all()
        return total, items

    def get_transaction(self, txn_id: uuid.UUID) -> Transaction:
        txn = (
            self.db.query(Transaction)
            .filter(Transaction.id == txn_id, Transaction.collection_id == self.collection_id)
            .first()
        )
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")
        return txn
