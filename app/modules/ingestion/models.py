from datetime import datetime
from enum import Enum
from typing import Optional, List
from sqlalchemy import (
    Boolean, Column, DateTime, Text, Numeric, String,
    ForeignKey, Enum as SQLEnum, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from app.core.db_types import UUID, uuid4
from app.core.base import Base


class TransactionStatus(str, Enum):
    RAW       = "raw"       # ingested, not yet processed
    UNMATCHED = "unmatched" # processed but no obligation found
    MATCHED   = "matched"   # matched to an obligation
    CATEGORIZED = "categorized" # linked to a CollectionPoint (bulk flow)
    DUPLICATE = "duplicate" # already seen — same psp_ref
    MANUAL    = "manual"    # entered manually by the business



class CollectionPoint(Base):
    """
    Virtual account / point of sale target.
    Used for bulk collections (Matatus, campaigns) where specific
    invoices don't exist, but we want to categorize by account_no.
    """
    __tablename__ = "collection_points"

    id            = Column(UUID, primary_key=True, default=uuid4)
    collection_id = Column(UUID, nullable=False, index=True)
    name          = Column(Text, nullable=False)           # e.g. "Matatu KAB-123C"
    account_no    = Column(Text, nullable=False, index=True) # BillRefNumber
    description   = Column(Text, nullable=True)
    is_active     = Column(Boolean, default=True, nullable=False)
    meta          = Column(JSONB, nullable=True, default=dict)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("collection_id", "account_no", name="uq_collection_point_account"),
        {"schema": "ingestion"},
    )


class Transaction(Base):
    """
    A single incoming payment event — normalized from any PSP callback
    or entered manually.

    Deduplication key: (collection_id, psp_ref) — a PSP may retry the
    webhook, we must not double-count.

    account_no is the reconciliation key — maps to Payer.account_no and
    is used to find the matching open Obligation.
    """
    __tablename__ = "transactions"

    id                   = Column(UUID, primary_key=True, default=uuid4)
    collection_id        = Column(UUID, nullable=False, index=True)
    psp_config_id        = Column(
        UUID,
        ForeignKey("accounts.psp_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    psp_type             = Column(Text, nullable=False, index=True)   # denormalized for fast filter
    psp_ref              = Column(Text, nullable=True, index=True)    # e.g. M-PESA TransID "LGR019G3J4"
    amount               = Column(Numeric(18, 2), nullable=False)
    currency             = Column(String(3), default="KES", nullable=False)
    phone                = Column(Text, nullable=True, index=True)    # normalized 254XXXXXXXXX
    account_no           = Column(Text, nullable=True, index=True)    # BillRefNumber → reconciliation key
    payer_name           = Column(Text, nullable=True)
    raw_payload          = Column(JSONB, nullable=True)               # full original callback, immutable
    status               = Column(
        SQLEnum(TransactionStatus),
        default=TransactionStatus.RAW,
        nullable=False,
        index=True
    )
    matched_obligation_id = Column(
        UUID,
        ForeignKey("obligations.obligations.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    collection_point_id   = Column(
        UUID,
        ForeignKey("ingestion.collection_points.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    matched_at            = Column(DateTime, nullable=True)
    is_manual             = Column(Boolean, default=False, nullable=False)
    ingested_at           = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        # Core deduplication: same PSP ref cannot be counted twice per tenant
        UniqueConstraint("collection_id", "psp_ref", name="uq_transaction_collection_psp_ref"),
        Index("idx_transactions_collection_status",    "collection_id", "status"),
        Index("idx_transactions_collection_account",   "collection_id", "account_no"),
        Index("idx_transactions_collection_ingested",  "collection_id", "ingested_at"),
        Index("idx_transactions_collection_point",     "collection_id", "collection_point_id"),
        Index("idx_transactions_psp_type_status",      "psp_type", "status"),
        {"schema": "ingestion"},
    )
