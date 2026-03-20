from datetime import datetime
from enum import Enum
from typing import Optional
from sqlalchemy import Boolean, Column, DateTime, Text, Enum as SQLEnum, Index
from sqlalchemy.dialects.postgresql import JSONB
from app.core.db_types import UUID, uuid4
from app.core.base import Base


class PSPType(str, Enum):
    MPESA      = "mpesa"
    KCB        = "kcb"
    EQUITY     = "equity"
    COOP       = "coop"
    INTASEND   = "intasend"
    SMARTPAY   = "smartpay"
    SWIFT      = "swift"
    CUSTOM     = "custom"


class PSPConfig(Base):
    """
    A business's configured payment service provider (payment channel).

    One business can have multiple PSP configs — e.g. M-PESA paybill for rent
    AND a separate KCB account for deposits.

    On creation the system computes a unique webhook_url the business
    registers with the PSP:
        {BASE_URL}/api/v1/ingest/{collection_id}/{psp_type}/callback
    """
    __tablename__ = "psp_configs"

    id            = Column(UUID, primary_key=True, default=uuid4)
    collection_id = Column(UUID, nullable=False, index=True)
    psp_type      = Column(SQLEnum(PSPType), nullable=False, index=True)
    display_name  = Column(Text, nullable=False)              # e.g. "Safaricom M-PESA Paybill"
    paybill       = Column(Text, nullable=True)               # paybill / shortcode / account no at PSP
    webhook_url   = Column(Text, nullable=False)              # generated URL to register with PSP
    # JSONB credentials — stored here, redacted in read responses.
    # MPESA: {consumer_key, consumer_secret, passkey, environment}
    # KCB / others: {api_key, ...}
    credentials   = Column(JSONB, nullable=True, default=dict)
    # any extra PSP-specific settings: {till_number, store_number, ...}
    meta          = Column(JSONB, nullable=True, default=dict)
    is_active     = Column(Boolean, default=True, nullable=False, index=True)
    created_by    = Column(UUID, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_psp_configs_collection_type",   "collection_id", "psp_type"),
        Index("idx_psp_configs_collection_active", "collection_id", "is_active"),
        {"schema": "accounts"},
    )
