"""
Billing module — Pydantic request / response schemas.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.modules.billing.models import (
    InvoiceStatus, PlanSlug, SubscriptionStatus,
    WalletTxEvent, WalletTxType,
)


# ─── Plans ────────────────────────────────────────────────────────────────────

class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                    uuid.UUID
    slug:                  PlanSlug
    name:                  str
    monthly_fee_kes:       Decimal
    notification_fee_kes:  Decimal
    wallet_minimum_kes:    Decimal
    max_branches:          int
    max_psps:              int
    requires_custom_quote: bool
    features:              Optional[Dict[str, Any]]


class PlanListResponse(BaseModel):
    items: List[PlanResponse]


# ─── Subscription ─────────────────────────────────────────────────────────────

class SubscribeRequest(BaseModel):
    plan_slug: PlanSlug


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                   uuid.UUID
    collection_id:        uuid.UUID
    plan:                 PlanResponse
    status:               SubscriptionStatus
    trial_ends_at:        Optional[datetime]
    current_period_start: Optional[datetime]
    current_period_end:   Optional[datetime]
    grace_ends_at:        Optional[datetime]
    suspended_at:         Optional[datetime]
    notification_count:   int
    created_at:           datetime


# ─── Wallet ───────────────────────────────────────────────────────────────────

class WalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                    uuid.UUID
    collection_id:         uuid.UUID
    balance_kes:           Decimal
    lifetime_topup:        Decimal
    is_auto_deduct_enabled: bool
    last_topup_at:         Optional[datetime]
    created_at:            datetime


class TopupRequest(BaseModel):
    amount_kes: Decimal = Field(..., gt=0, description="Amount to top up in KES (minimum 100)")
    email:      str     = Field(..., description="Email address for Paystack checkout")
    callback_url: Optional[str] = Field(None, description="URL Paystack redirects to after payment")


class TopupInitResponse(BaseModel):
    payment_url:  str
    reference:    str
    amount_kes:   Decimal


class TopupVerifyRequest(BaseModel):
    reference: str


class TopupVerifyResponse(BaseModel):
    success:        bool
    amount_credited: Decimal
    balance_kes:    Decimal
    message:        str


# ─── Wallet Transactions ──────────────────────────────────────────────────────

class WalletTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:             uuid.UUID
    tx_type:        WalletTxType
    event_type:     WalletTxEvent
    amount_kes:     Decimal
    balance_after:  Decimal
    description:    Optional[str]
    reference:      Optional[str]
    meta:           Optional[Dict[str, Any]]
    created_at:     datetime


class WalletTransactionListResponse(BaseModel):
    total:       int
    balance_kes: Decimal
    items:       List[WalletTransactionResponse]


# ─── Invoices ─────────────────────────────────────────────────────────────────

class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                         uuid.UUID
    invoice_number:             str
    period_start:               datetime
    period_end:                 datetime
    subscription_fee_kes:       Decimal
    notification_count:         int
    notification_fee_total_kes: Decimal
    total_amount_kes:           Decimal
    status:                     InvoiceStatus
    paystack_payment_link:      Optional[str]
    paid_at:                    Optional[datetime]
    sent_at:                    Optional[datetime]
    created_at:                 datetime


class InvoiceListResponse(BaseModel):
    total: int
    items: List[InvoiceResponse]


# ─── Dashboard summary ────────────────────────────────────────────────────────

class BillingSummaryResponse(BaseModel):
    subscription:      SubscriptionResponse
    wallet:            WalletResponse
    subscription_fee:  Decimal   # monthly platform fee for the current plan
    notification_est:  Decimal   # per-notification charges so far this month
    invoice_est:       Decimal   # per-invoice/obligation charges so far this month
    cp_rent_est:       Decimal   # collection point monthly rent (active CPs × fee)
    current_month_est: Decimal   # total estimated charges so far this month
