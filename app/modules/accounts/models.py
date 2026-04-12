from datetime import datetime
from enum import Enum
from typing import Optional
from sqlalchemy import Boolean, Column, DateTime, Text, Enum as SQLEnum, Index
from sqlalchemy.dialects.postgresql import JSONB
from app.core.db_types import UUID, uuid4
from app.core.base import Base
from app.core.timezone import now_nairobi


class PSPType(str, Enum):
    MPESA      = "mpesa"
    KCB        = "kcb"
    EQUITY     = "equity"
    COOP       = "coop"
    INTASEND   = "intasend"
    SMARTPAY   = "smartpay"
    SWIFT      = "swift"
    CUSTOM     = "custom"


class BusinessProfile(Base):
    """
    A tenant's identity record on the platform.

    One profile per collection (business workspace). Stores:
      - Business name and display name (used as SMS sender / email from)
      - Contact info (so the business can be reached as the platform operator)
      - Notification sender settings that override the platform defaults
      - meta JSONB for any extra fields (KRA PIN, registration number, etc.)

    This mirrors how PSP providers know the business — the display_name and
    sms_sender_id appear on every message sent to their customers.
    """
    __tablename__ = "business_profiles"

    id              = Column(UUID, primary_key=True, default=uuid4)
    collection_id   = Column(UUID, nullable=False, unique=True, index=True)  # one per workspace
    business_name   = Column(Text, nullable=False)           # legal/registered name
    display_name    = Column(Text, nullable=True)            # short brand name shown to customers
    phone           = Column(Text, nullable=True)            # owner contact phone
    email           = Column(Text, nullable=True)            # owner contact email
    address         = Column(Text, nullable=True)
    logo_url        = Column(Text, nullable=True)
    # Notification sender overrides (email only — SMS sender ID is platform-wide)
    email_from      = Column(Text, nullable=True)            # e.g. "billing@myschool.ac.ke"
    # Any extras: KRA PIN, registration no, county, business type, etc.
    meta            = Column(JSONB, nullable=True, default=dict)
    created_by      = Column(UUID, nullable=False)
    created_at      = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at      = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    __table_args__ = ({"schema": "accounts"},)


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
    
    # ── Bank-specific routing ─────────────────────────────────────────────────
    till_number   = Column(Text, nullable=True)               # Bank till / shortcode
    business_key  = Column(Text, nullable=True)               # notifyBiller key / Account business key
    account_no    = Column(Text, nullable=True)               # Target internal bank account no
    
    # any extra PSP-specific settings: {store_number, ...}
    meta          = Column(JSONB, nullable=True, default=dict)
    is_active     = Column(Boolean, default=True, nullable=False, index=True)
    created_by    = Column(UUID, nullable=False)
    created_at    = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at    = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    __table_args__ = (
        Index("idx_psp_configs_collection_type",   "collection_id", "psp_type"),
        Index("idx_psp_configs_collection_active", "collection_id", "is_active"),
        {"schema": "accounts"},
    )
