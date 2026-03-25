from datetime import datetime
from typing import Optional
from enum import Enum
from sqlalchemy import (
    Boolean, Column, DateTime, String, ForeignKey,
    Numeric, Text, Integer, Enum as SQLEnum, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from app.core.db_types import UUID, uuid4
from sqlalchemy.orm import relationship
from app.core.base import Base
from app.core.timezone import now_nairobi

# ─── Enums ────────────────────────────────────────────────────────────────────

class GroupType(str, Enum):
    """Categorises the kind of payer group for a tenant workspace."""
    APARTMENT_BLOCK = "apartment_block"   # Rental — Block A, Block B
    UNIT            = "unit"              # Rental — individual unit grouping
    SCHOOL_CLASS    = "school_class"      # School — Grade 7A, Form 2
    BUS_ROUTE       = "bus_route"         # SACCO — Route 12, CBD circle
    INVOICE_GROUP   = "invoice_group"     # B2B — Distributors, Retailers
    CHAMA_GROUP     = "chama_group"       # Investment group / chama
    UTILITY_ZONE    = "utility_zone"      # Utilities — Water zone, meter group
    CUSTOM          = "custom"            # Anything else


class ObligationStatus(str, Enum):
    PENDING   = "pending"
    PARTIAL   = "partial"
    PAID      = "paid"
    OVERDUE   = "overdue"
    CANCELLED = "cancelled"


class RecurrenceType(str, Enum):
    MONTHLY = "monthly"
    WEEKLY  = "weekly"
    TERM    = "term"    # school term (3x/year)
    CUSTOM  = "custom"  # interval_days


class TemplateChannel(str, Enum):
    SMS       = "sms"
    WHATSAPP  = "whatsapp"
    EMAIL     = "email"
    ALL       = "all"


class TemplateType(str, Enum):
    PAYMENT_REMINDER     = "payment_reminder"    # e.g. "Hi {{payer_name}}, your rent is due"
    OVERDUE_NOTICE       = "overdue_notice"      # late payment notice
    PAYMENT_RECEIPT      = "payment_receipt"     # partial payment — still has a balance
    PAYMENT_RECEIPT_FULL = "payment_receipt_full"  # full payment — obligation fully settled
    OBLIGATION_CREATED   = "obligation_created"  # new bill created
    OBLIGATION_CANCELLED = "obligation_cancelled"  # bill cancelled/removed
    STATEMENT            = "statement"           # monthly/term statement
    CUSTOM               = "custom"


# ─── PayerGroup — organises payers into logical cohorts ───────────────────────

class PayerGroup(Base):
    """
    A named cohort of payers within a collection workspace.

    Examples:
      - "Block A" (apartment complex)
      - "Grade 7" (school)
      - "Bus Route 12" (SACCO)
      - "Q1 2025 Retailers" (B2B distributors)
    """
    __tablename__ = "payer_groups"

    id            = Column(UUID, primary_key=True, default=uuid4)
    collection_id = Column(UUID, nullable=False, index=True)
    name          = Column(Text, nullable=False)
    description   = Column(Text, nullable=True)
    group_type    = Column(SQLEnum(GroupType), default=GroupType.CUSTOM, nullable=False, index=True)
    # Arbitrary tenant-defined fields: e.g. {"floor": 2, "block": "A", "capacity": 30}
    meta          = Column(JSONB, nullable=True, default=dict)
    created_by    = Column(UUID, nullable=False)
    created_at    = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at    = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    # Relationships
    payers = relationship("Payer", back_populates="group", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_payer_groups_collection_type", "collection_id", "group_type"),
        {"schema": "obligations"},
    )


# ─── Payer — the individual customer/member paying obligations ─────────────────

class Payer(Base):
    """
    An individual who owes money to the collection workspace.

    - Tenant → account_no = "UNIT-3B"  (maps to M-PESA sub-account)
    - Student → identifier = "STU-2024-045"
    - Bus     → identifier = "BUS-07"
    - Member  → identifier = "MEM-012"
    """
    __tablename__ = "payers"

    id            = Column(UUID, primary_key=True, default=uuid4)
    collection_id = Column(UUID, nullable=False, index=True)
    group_id      = Column(UUID, ForeignKey("obligations.payer_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    name          = Column(Text, nullable=False)
    phone         = Column(Text, nullable=True, index=True)
    email         = Column(Text, nullable=True)
    account_no    = Column(Text, nullable=True, index=True)   # M-PESA sub-account this payer uses
    identifier    = Column(Text, nullable=True)               # business-specific ID (student no, member no)
    notes         = Column(Text, nullable=True)
    is_active     = Column(Boolean, default=True, nullable=False, index=True)
    # Tenant-defined custom fields: e.g. {"grade": "7A", "guardian": "John Doe", "credit_limit": 50000}
    meta          = Column(JSONB, nullable=True, default=dict)
    created_by    = Column(UUID, nullable=False)
    created_at    = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at    = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    # Relationships
    group       = relationship("PayerGroup", back_populates="payers")
    obligations = relationship("Obligation", back_populates="payer", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_payers_collection_active",   "collection_id", "is_active"),
        Index("idx_payers_collection_group",    "collection_id", "group_id"),
        Index("idx_payers_account_no",          "collection_id", "account_no"),
        {"schema": "obligations"},
    )


# ─── Obligation ────────────────────────────────────────────────────────────────

class Obligation(Base):
    """
    A single expected payment owed by a Payer to the collection.

    - One-off: due_date set, is_recurring=False
    - Recurring: is_recurring=True, linked RecurringConfig drives auto-generation
      of the next cycle obligation when this one is settled.

    amount_due may change over time (e.g. rent reviewed upward) — just update and
    the balance recalculates. A full audit trail lives in payment records.
    """
    __tablename__ = "obligations"

    id             = Column(UUID, primary_key=True, default=uuid4)
    collection_id  = Column(UUID, nullable=False, index=True)
    payer_id       = Column(UUID, ForeignKey("obligations.payers.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_no     = Column(Text, nullable=False, index=True)     # M-PESA routing key
    description    = Column(Text, nullable=True)                   # e.g. "April 2025 Rent"
    amount_due     = Column(Numeric(18, 2), nullable=False)
    amount_paid    = Column(Numeric(18, 2), default=0, nullable=False)
    balance        = Column(Numeric(18, 2), default=0, nullable=False)
    currency       = Column(String(3), default="KES", nullable=False)
    due_date       = Column(DateTime(timezone=True), nullable=True)
    status         = Column(SQLEnum(ObligationStatus), default=ObligationStatus.PENDING, nullable=False, index=True)
    is_recurring   = Column(Boolean, default=False, nullable=False, index=True)
    # Tenant-defined custom fields: e.g. {"invoice_no": "INV-001", "penalty": 500, "term": "Term 1 2025"}
    meta           = Column(JSONB, nullable=True, default=dict)
    created_by     = Column(UUID, nullable=False, index=True)
    created_at     = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at     = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    # Relationships
    payer = relationship("Payer", back_populates="obligations")
    recurring_config = relationship(
        "RecurringConfig",
        back_populates="obligation",
        uselist=False,
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_obligations_collection_status",   "collection_id", "status"),
        Index("idx_obligations_collection_account",  "collection_id", "account_no"),
        Index("idx_obligations_collection_due_date", "collection_id", "due_date"),
        Index("idx_obligations_payer_status",        "payer_id", "status"),
        Index("idx_obligations_created_at_desc",     "created_at", postgresql_using="btree"),
        {"schema": "obligations"},
    )


# ─── RecurringConfig ──────────────────────────────────────────────────────────

class RecurringConfig(Base):
    """
    Recurrence schedule for obligations with is_recurring=True.
    When a recurring obligation is settled, the engine uses this to spawn
    the next cycle obligation automatically.
    """
    __tablename__ = "recurring_configs"

    id               = Column(UUID, primary_key=True, default=uuid4)
    obligation_id    = Column(
        UUID,
        ForeignKey("obligations.obligations.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    recurrence_type   = Column(SQLEnum(RecurrenceType), nullable=False)
    interval_days     = Column(Integer, nullable=True)   # CUSTOM only
    day_of_month      = Column(Integer, nullable=True)   # 1-28, MONTHLY
    day_of_week       = Column(Integer, nullable=True)   # 0=Mon…6=Sun, WEEKLY
    start_date        = Column(DateTime(timezone=True), nullable=False)
    end_date          = Column(DateTime(timezone=True), nullable=True)
    next_due_date     = Column(DateTime(timezone=True), nullable=True, index=True)
    grace_period_days = Column(Integer, default=0, nullable=False)
    auto_generate     = Column(Boolean, default=True, nullable=False)
    created_at        = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)

    # Relationships
    obligation = relationship("Obligation", back_populates="recurring_config")

    __table_args__ = (
        Index("idx_recurring_next_due", "next_due_date"),
        {"schema": "obligations"},
    )


# ─── NotificationTemplate ─────────────────────────────────────────────────────

class NotificationTemplate(Base):
    """
    Reusable message templates that a business prepares for automated
    notifications. Supports variable substitution using {{variable}} syntax.

    Available variables: {{payer_name}}, {{amount_due}}, {{amount_paid}},
    {{balance}}, {{due_date}}, {{account_no}}, {{collection_name}}, {{description}},
    {{paybill}}, {{shortcode}}
    """
    __tablename__ = "notification_templates"

    id            = Column(UUID, primary_key=True, default=uuid4)
    collection_id = Column(UUID, nullable=False, index=True)
    name          = Column(Text, nullable=False)                              # e.g. "Rent Reminder"
    template_type = Column(SQLEnum(TemplateType), nullable=False, index=True)
    channel       = Column(SQLEnum(TemplateChannel), nullable=False, index=True)
    subject       = Column(Text, nullable=True)                              # for email
    body          = Column(Text, nullable=False)                             # message body with {{variables}}
    is_active     = Column(Boolean, default=True, nullable=False)
    is_default    = Column(Boolean, default=False, nullable=False)           # default template for this type+channel
    created_by    = Column(UUID, nullable=False)
    created_at    = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at    = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    __table_args__ = (
        Index("idx_notif_templates_collection_type",    "collection_id", "template_type"),
        Index("idx_notif_templates_collection_channel", "collection_id", "channel"),
        {"schema": "obligations"},
    )
