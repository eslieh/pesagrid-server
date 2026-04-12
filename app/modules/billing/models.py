"""
Billing module models — subscription plans, wallets, usage tracking, invoices.

PostgreSQL schema: billing
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Index, Integer,
    Numeric, Text, Enum as SQLEnum,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.base import Base
from app.core.db_types import UUID, uuid4
from app.core.timezone import now_nairobi


# ─── Enums ────────────────────────────────────────────────────────────────────

class PlanSlug(str, Enum):
    STARTER    = "starter"
    GROWTH     = "growth"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    TRIAL     = "trial"       # 30-day free trial
    ACTIVE    = "active"      # paying subscriber
    SUSPENDED = "suspended"   # wallet too low / payment failed — grace period active
    BLOCKED   = "blocked"     # grace period expired — hard block on features
    CANCELLED = "cancelled"   # user cancelled


class WalletTxType(str, Enum):
    TOPUP        = "topup"         # user loaded money via Paystack
    DEDUCTION    = "deduction"     # usage fee deducted (SMS / email / invoice)
    REVERSAL     = "reversal"      # refund of an erroneous deduction
    SUBSCRIPTION = "subscription"  # monthly platform fee charged from wallet


class WalletTxEvent(str, Enum):
    # Legacy — kept so existing DB rows with event_type='reconciliation' can still be read.
    # No longer used for new deductions.
    RECONCILIATION   = "reconciliation"
    SMS              = "sms"
    EMAIL            = "email"
    INVOICE          = "invoice"
    SUBSCRIPTION_FEE = "subscription_fee"
    TOPUP            = "topup"
    REVERSAL         = "reversal"
    WELCOME_CREDIT   = "welcome_credit"


class InvoiceStatus(str, Enum):
    DRAFT   = "draft"
    SENT    = "sent"
    PAID    = "paid"
    OVERDUE = "overdue"
    VOID    = "void"


# ─── SubscriptionPlan (seed table: Starter / Growth / Enterprise) ─────────────

class SubscriptionPlan(Base):
    """
    Platform-level plan definitions. Seeded once; not per-tenant.

    Prices in KES (stored as 2-dp Numeric).
    max_branches / max_psps: -1 = unlimited.
    """
    __tablename__ = "subscription_plans"

    id                   = Column(UUID, primary_key=True, default=uuid4)
    slug                 = Column(SQLEnum(PlanSlug, values_callable=lambda obj: [e.value for e in obj]), nullable=False, unique=True, index=True)
    name                 = Column(Text, nullable=False)                # "Starter", "Growth", "Enterprise"
    monthly_fee_kes      = Column(Numeric(12, 2), nullable=False)      # 3000 / 12000 / 0 (custom)
    recon_fee_kes        = Column(Numeric(10, 4), nullable=False)      # per reconciliation
    notification_fee_kes = Column(Numeric(10, 4), nullable=False)      # per SMS or email
    invoice_fee_kes      = Column(Numeric(10, 4), nullable=False, default=0) # per invoice/obligation created
    collection_point_fee_kes = Column(Numeric(12, 2), nullable=False, default=0) # monthly fee per active collection point
    wallet_minimum_kes   = Column(Numeric(12, 2), nullable=False)      # minimum wallet balance required
    max_branches         = Column(Integer, nullable=False, default=1)  # -1 = unlimited
    max_psps             = Column(Integer, nullable=False, default=2)  # -1 = unlimited
    requires_custom_quote = Column(Boolean, default=False, nullable=False)
    # Feature flags stored as JSONB: {real_time_recon, detailed_ledger, ...}
    features             = Column(JSONB, nullable=True, default=dict)
    is_active            = Column(Boolean, default=True, nullable=False)
    created_at           = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at           = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    # Relationships
    subscriptions = relationship("TenantSubscription", back_populates="plan")

    __table_args__ = ({"schema": "billing"},)


# ─── TenantSubscription ───────────────────────────────────────────────────────

class TenantSubscription(Base):
    """
    One active subscription per tenant (collection_id).

    Lifecycle:
      1. TRIAL  (30 days) — created automatically on first login / profile creation
      2. ACTIVE — user picks a plan and wallet is funded
      3. SUSPENDED — wallet drops below minimum (grace period: 7 days)
      4. BLOCKED — grace period expired
      5. CANCELLED — user cancels

    Grace period gives the tenant 7 days to top up before features are blocked.
    """
    __tablename__ = "tenant_subscriptions"

    id                   = Column(UUID, primary_key=True, default=uuid4)
    collection_id        = Column(UUID, nullable=False, unique=True, index=True)
    plan_id              = Column(UUID, ForeignKey("billing.subscription_plans.id"), nullable=False, index=True)
    status               = Column(SQLEnum(SubscriptionStatus, values_callable=lambda obj: [e.value for e in obj]), default=SubscriptionStatus.TRIAL, nullable=False, index=True)
    trial_ends_at        = Column(DateTime(timezone=True), nullable=True)
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end   = Column(DateTime(timezone=True), nullable=True)
    suspended_at         = Column(DateTime(timezone=True), nullable=True)  # when suspension began
    grace_ends_at        = Column(DateTime(timezone=True), nullable=True)  # SUSPENDED → BLOCKED after this
    cancelled_at         = Column(DateTime(timezone=True), nullable=True)
    # Usage counters for current billing period
    recon_count          = Column(Integer, default=0, nullable=False)
    notification_count   = Column(Integer, default=0, nullable=False)
    invoice_count        = Column(Integer, default=0, nullable=False)
    created_at           = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at           = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    # Relationships
    plan   = relationship("SubscriptionPlan", back_populates="subscriptions")
    wallet = relationship("TenantWallet", back_populates="subscription", uselist=False)

    __table_args__ = (
        Index("idx_tenant_subs_status", "status"),
        Index("idx_tenant_subs_collection", "collection_id"),
        {"schema": "billing"},
    )


# ─── TenantWallet ─────────────────────────────────────────────────────────────

class TenantWallet(Base):
    """
    Prepaid wallet per tenant. Loaded via Paystack top-ups.

    Balance is decremented for each:
      - SMS or email sent     (plan.notification_fee_kes)
      - Monthly platform fee  (plan.monthly_fee_kes)

    When balance < plan.wallet_minimum_kes, automated deductions are
    paused and the tenant enters a SUSPENDED state.
    """
    __tablename__ = "tenant_wallets"

    id              = Column(UUID, primary_key=True, default=uuid4)
    collection_id   = Column(UUID, nullable=False, unique=True, index=True)
    subscription_id = Column(UUID, ForeignKey("billing.tenant_subscriptions.id"), nullable=False, unique=True)
    balance_kes     = Column(Numeric(14, 2), default=0, nullable=False)
    lifetime_topup  = Column(Numeric(14, 2), default=0, nullable=False)  # running total deposited
    is_auto_deduct_enabled = Column(Boolean, default=True, nullable=False)
    last_topup_at   = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at      = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    # Relationships
    subscription = relationship("TenantSubscription", back_populates="wallet")
    transactions = relationship("WalletTransaction", back_populates="wallet", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_tenant_wallets_collection", "collection_id"),
        {"schema": "billing"},
    )


# ─── WalletTransaction ────────────────────────────────────────────────────────

class WalletTransaction(Base):
    """
    Immutable ledger of every debit and credit to a TenantWallet.

    One row per event:
      - Top-up via Paystack → type=topup, reference=paystack_ref
      - SMS/email fee       → type=deduction, event_type=sms | email
      - Monthly sub fee     → type=subscription
    """
    __tablename__ = "wallet_transactions"

    id              = Column(UUID, primary_key=True, default=uuid4)
    wallet_id       = Column(UUID, ForeignKey("billing.tenant_wallets.id"), nullable=False, index=True)
    collection_id   = Column(UUID, nullable=False, index=True)
    tx_type         = Column(SQLEnum(WalletTxType, values_callable=lambda obj: [e.value for e in obj]), nullable=False, index=True)
    event_type      = Column(SQLEnum(WalletTxEvent, values_callable=lambda obj: [e.value for e in obj]), nullable=False, index=True)
    amount_kes      = Column(Numeric(12, 4), nullable=False)     # always positive; direction given by tx_type
    balance_after   = Column(Numeric(14, 2), nullable=False)     # wallet balance after this tx
    description     = Column(Text, nullable=True)
    reference       = Column(Text, nullable=True, index=True)    # Paystack payment ref / event UUID
    meta            = Column(JSONB, nullable=True, default=dict) # {payer_id, obligation_id, channel, ...}
    created_at      = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)

    # Relationships
    wallet = relationship("TenantWallet", back_populates="transactions")

    __table_args__ = (
        Index("idx_wallet_txn_wallet_created", "wallet_id", "created_at"),
        Index("idx_wallet_txn_collection_type", "collection_id", "tx_type"),
        Index("idx_wallet_txn_reference", "reference"),
        {"schema": "billing"},
    )


# ─── PlatformInvoice ──────────────────────────────────────────────────────────

class PlatformInvoice(Base):
    """
    Monthly invoice generated by the billing cron on the 1st of each month.

    Covers:
      - Monthly subscription/platform fee
      - Per-notification (SMS + email) usage charges

    Payment is via Paystack (pay invoice link) or auto-deducted from wallet.
    """
    __tablename__ = "platform_invoices"

    id                    = Column(UUID, primary_key=True, default=uuid4)
    collection_id         = Column(UUID, nullable=False, index=True)
    plan_id               = Column(UUID, ForeignKey("billing.subscription_plans.id"), nullable=False)
    invoice_number        = Column(Text, nullable=False, unique=True, index=True)  # INV-2025-04-001
    period_start          = Column(DateTime(timezone=True), nullable=False)
    period_end            = Column(DateTime(timezone=True), nullable=False)
    subscription_fee_kes  = Column(Numeric(12, 2), nullable=False, default=0)
    recon_count           = Column(Integer, nullable=False, default=0)
    recon_fee_total_kes   = Column(Numeric(12, 4), nullable=False, default=0)
    notification_count    = Column(Integer, nullable=False, default=0)
    notification_fee_total_kes = Column(Numeric(12, 4), nullable=False, default=0)
    invoice_count         = Column(Integer, nullable=False, default=0)
    invoice_fee_total_kes = Column(Numeric(12, 4), nullable=False, default=0)
    collection_point_count = Column(Integer, nullable=False, default=0)
    collection_point_fee_total_kes = Column(Numeric(12, 2), nullable=False, default=0)
    total_amount_kes      = Column(Numeric(14, 2), nullable=False, default=0)
    status                = Column(SQLEnum(InvoiceStatus, values_callable=lambda obj: [e.value for e in obj]), default=InvoiceStatus.DRAFT, nullable=False, index=True)
    paystack_payment_link = Column(Text, nullable=True)
    paystack_reference    = Column(Text, nullable=True)
    paid_at               = Column(DateTime(timezone=True), nullable=True)
    sent_at               = Column(DateTime(timezone=True), nullable=True)
    notes                 = Column(Text, nullable=True)
    created_at            = Column(DateTime(timezone=True), default=now_nairobi, nullable=False)
    updated_at            = Column(DateTime(timezone=True), default=now_nairobi, onupdate=now_nairobi, nullable=False)

    # Relationships
    plan = relationship("SubscriptionPlan")

    __table_args__ = (
        Index("idx_platform_invoices_collection", "collection_id"),
        Index("idx_platform_invoices_period",     "period_start", "period_end"),
        Index("idx_platform_invoices_status",     "status"),
        UniqueConstraint("collection_id", "period_start", name="uq_invoice_collection_period"),
        {"schema": "billing"},
    )
