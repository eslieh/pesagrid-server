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
from app.core.timezone import now_nairobi


class TransactionStatus(str, Enum):
    RAW         = "raw"         # ingested, not yet processed
    UNMATCHED   = "unmatched"   # processed but no obligation found
    MATCHED     = "matched"     # matched to an obligation
    CATEGORIZED = "categorized" # linked to a CollectionPoint (bulk flow)
    DUPLICATE   = "duplicate"   # already seen — same psp_ref
    MANUAL      = "manual"      # entered manually by the business


class CollectionPointType(str, Enum):
    """
    Classifies the financial instrument type so analytics and UI can adapt.
    - CAMPAIGN  : time-boxed fundraiser with a goal and deadline
    - FLEET     : vehicle-by-vehicle SACCO tracking (account_no per vehicle)
    - DONATION  : open-ended, recurring donation flow (church, NGO, charity)
    - BRANCH    : geographic / physical branch-level collection
    - EVENT     : one-off event with a specific date window
    - CUSTOM    : any other use case
    """
    CAMPAIGN = "campaign"
    FLEET    = "fleet"
    DONATION = "donation"
    BRANCH   = "branch"
    EVENT    = "event"
    CUSTOM   = "custom"


class CollectionGoalPeriod(str, Enum):
    DAILY   = "daily"
    WEEKLY  = "weekly"
    MONTHLY = "monthly"
    YEARLY  = "yearly"
    CUSTOM  = "custom"   # Relies strictly on start_date / end_date

class CollectionPoint(Base):
    """
    A business product deployed to capture value from a large, anonymous crowd.

    Evolved from a simple virtual account tag into a typed financial instrument
    with goal tracking, timeline, compliance thresholds, and segment analytics.

    The account_no is still the routing key — all transactions where
    BillRefNumber == account_no are categorized here.
    """
    __tablename__ = "collection_points"

    id            = Column(UUID, primary_key=True, default=uuid4)
    collection_id = Column(UUID, nullable=False, index=True)
    name          = Column(Text, nullable=False)              # e.g. "Matatu KAB-123C"
    account_no    = Column(Text, nullable=False, index=True)  # BillRefNumber — routing key
    description   = Column(Text, nullable=True)

    # ── Type and identity ─────────────────────────────────────────────────────
    cp_type       = Column(
        SQLEnum(CollectionPointType, name="collectionpointtype"),
        default=CollectionPointType.CUSTOM,
        nullable=False,
        index=True
    )

    # ── Goal and timeline (for campaigns, events, fundraisers) ────────────────
    goal_amount          = Column(Numeric(18, 2), nullable=True)         # target KES amount
    currency             = Column(String(3), default="KES", nullable=False)
    goal_period          = Column(
        SQLEnum(CollectionGoalPeriod, name="collectiongoalperiod"),
        default=CollectionGoalPeriod.CUSTOM,
        nullable=False
    )
    start_date           = Column(DateTime(timezone=True), nullable=True) # when the window opens
    end_date             = Column(DateTime(timezone=True), nullable=True) # when it closes

    # ── Compliance ────────────────────────────────────────────────────────────
    compliance_threshold = Column(Numeric(18, 2), nullable=True)
    # Single transactions above this amount are surfaced as compliance flags.
    # None = disabled (default).

    # ── Flags ─────────────────────────────────────────────────────────────────
    is_active           = Column(Boolean, default=True, nullable=False)
    sms_acknowledgement = Column(Boolean, default=False, nullable=False)

    # ── Metadata and timestamps ───────────────────────────────────────────────
    meta       = Column(JSONB, nullable=True, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    __table_args__ = (
        UniqueConstraint("collection_id", "account_no", name="uq_collection_point_account"),
        Index("idx_cp_collection_type",   "collection_id", "cp_type"),
        Index("idx_cp_collection_active", "collection_id", "is_active"),
        {"schema": "ingestion"},
    )


class CollectionPointPSP(Base):
    """
    Links a CollectionPoint to one or more PSPConfig records.

    This is analytics metadata — it answers "which payment channels feed
    into this collection point?" and powers the channel breakdown in insights.

    Routing is still account_no-based. This table does NOT change how
    transactions are matched to collection points.

    Example: A campaign receives from M-Pesa paybill AND a bank account.
    Both are registered here so the insights layer can show the blended
    channel picture (81% M-Pesa, 19% bank).
    """
    __tablename__ = "collection_point_psps"

    id                  = Column(UUID, primary_key=True, default=uuid4)
    collection_point_id = Column(
        UUID,
        ForeignKey("ingestion.collection_points.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    psp_config_id       = Column(
        UUID,
        ForeignKey("accounts.psp_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    label               = Column(Text, nullable=True)          # e.g. "Primary M-Pesa", "Bank wire"
    is_active           = Column(Boolean, default=True, nullable=False)
    created_at          = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "collection_point_id", "psp_config_id",
            name="uq_cp_psp"
        ),
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
    matched_at            = Column(DateTime(timezone=True), nullable=True)
    is_manual             = Column(Boolean, default=False, nullable=False)
    ingested_at           = Column(DateTime(timezone=True), default=now_nairobi, nullable=False, index=True)

    __table_args__ = (
        # Core deduplication: same PSP ref cannot be counted twice per tenant
        UniqueConstraint("collection_id", "psp_ref", name="uq_transaction_collection_psp_ref"),
        Index("idx_transactions_collection_status",    "collection_id", "status"),
        Index("idx_transactions_collection_account",   "collection_id", "account_no"),
        Index("idx_transactions_collection_ingested",  "collection_id", "ingested_at"),
        Index("idx_transactions_collection_point",     "collection_id", "collection_point_id"),
        Index("idx_transactions_psp_type_status",      "psp_type", "status"),
        # Supports combined date-range + amount-range filter queries
        Index("idx_transactions_collection_date_amount", "collection_id", "ingested_at", "amount"),
        {"schema": "ingestion"},
    )
