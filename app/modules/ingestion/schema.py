import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field
from app.modules.ingestion.models import TransactionStatus, CollectionPointType, CollectionGoalPeriod

# ─── M-PESA Raw Payload Schemas (for documentation / validation) ───────────────

class MpesaC2BCallback(BaseModel):
    """Safaricom C2B payload structure."""
    TransID: str
    TransAmount: float
    MSISDN: str
    BillRefNumber: str
    FirstName: Optional[str] = None
    MiddleName: Optional[str] = None
    LastName: Optional[str] = None
    TransTime: str
    BusinessShortCode: str

class MpesaSTKCallback(BaseModel):
    """Simplified STK Push callback structure."""
    Body: Dict[str, Any]

# ─── Transaction Schemas ──────────────────────────────────────────────────────

class TransactionBase(BaseModel):
    psp_type:     str
    psp_ref:      Optional[str] = None
    amount:       float
    currency:     str = "KES"
    phone:        Optional[str] = None
    account_no:   Optional[str] = None
    payer_name:   Optional[str] = None

class ManualPaymentCreate(TransactionBase):
    """Input for manual payment entry."""
    psp_type:   str = Field("manual", example="bank_transfer")
    note:       Optional[str] = None

class TransactionResponse(TransactionBase):
    """Full transaction record including internal metadata."""
    id:                    uuid.UUID
    collection_id:         uuid.UUID
    status:                TransactionStatus
    matched_obligation_id: Optional[uuid.UUID] = None
    collection_point_id:   Optional[uuid.UUID] = None
    psp_config_id:         Optional[uuid.UUID] = None
    ingested_at:           datetime

    class Config:
        from_attributes = True

# ─── Enriched Transaction Response ────────────────────────────────────────────

class MatchedPayerContext(BaseModel):
    """Inline payer details from the obligation match."""
    payer_id:   uuid.UUID
    payer_name: str
    account_no: Optional[str] = None

class MatchedObligationContext(BaseModel):
    """Inline obligation details and settlement result."""
    obligation_id:    uuid.UUID
    description:      Optional[str] = None
    amount_due:       float
    balance:          float
    settlement_type:  str     # "full" | "partial" | "overpay"

class TransactionEnrichedResponse(TransactionBase):
    """
    Full transaction record with inline match context.
    Used by GET /transactions/ for rich, decision-ready responses.

    matched_confidence is computed at read time from transaction+obligation state:
      1.00 → exact account_no + exact amount on a PENDING obligation
      0.85 → account_no match + partial amount (PARTIAL status)
      0.70 → account_no match only (fallback to oldest open obligation)
      null → CATEGORIZED / UNMATCHED / MANUAL

    match_reasons values: account_no_match, exact_amount, partial_payment, fallback_oldest
    """
    id:                    uuid.UUID
    collection_id:         uuid.UUID
    phone:                 Optional[str]     = None
    status:                TransactionStatus
    is_manual:             bool
    collection_point_id:   Optional[uuid.UUID] = None
    matched_obligation_id: Optional[uuid.UUID] = None
    psp_config_id:         Optional[uuid.UUID] = None
    ingested_at:           datetime

    matched_confidence:    Optional[float]                      = None
    match_reasons:         Optional[List[str]]                  = None
    matched_payer:         Optional[MatchedPayerContext]         = None
    matched_obligation:    Optional[MatchedObligationContext]    = None

    class Config:
        from_attributes = True

class TransactionListResponse(BaseModel):
    total: int
    items: List[TransactionResponse]

class TransactionEnrichedListResponse(BaseModel):
    total: int
    items: List[TransactionEnrichedResponse]

# ─── CollectionPoint Schemas ──────────────────────────────────────────────────

class CollectionPointBase(BaseModel):
    name:                str            = Field(..., example="Matatu KAB-123C")
    account_no:          str            = Field(..., example="KAB-123C")
    description:         Optional[str]  = None
    cp_type:             CollectionPointType = CollectionPointType.CUSTOM
    goal_amount:         Optional[float] = None
    currency:            str             = "KES"
    goal_period:         CollectionGoalPeriod = CollectionGoalPeriod.CUSTOM
    start_date:          Optional[datetime] = None
    end_date:            Optional[datetime] = None
    compliance_threshold: Optional[float] = None
    is_active:           bool           = True
    sms_acknowledgement: bool           = False
    meta:                Optional[Dict]  = {}

class CollectionPointCreate(CollectionPointBase):
    pass

class CollectionPointUpdate(BaseModel):
    name:                 Optional[str]   = None
    account_no:           Optional[str]   = None
    description:          Optional[str]   = None
    cp_type:              Optional[CollectionPointType] = None
    goal_amount:          Optional[float] = None
    currency:             Optional[str]   = None
    goal_period:          Optional[CollectionGoalPeriod] = None
    start_date:           Optional[datetime] = None
    end_date:             Optional[datetime] = None
    compliance_threshold: Optional[float] = None
    is_active:            Optional[bool]  = None
    sms_acknowledgement:  Optional[bool]  = None
    meta:                 Optional[Dict]  = None

class CollectionPointRead(CollectionPointBase):
    id:                  uuid.UUID
    collection_id:       uuid.UUID
    sms_acknowledgement: bool
    created_at:          datetime
    updated_at:          datetime

    class Config:
        from_attributes = True

class CollectionPointListResponse(BaseModel):
    total: int
    items: List[CollectionPointRead]

# ─── CollectionPointPSP Schemas ───────────────────────────────────────────────

class CollectionPointPSPCreate(BaseModel):
    """Link a PSP channel to a collection point."""
    psp_config_id: uuid.UUID
    label:         Optional[str] = None  # e.g. "Primary M-Pesa", "Bank wire"

class CollectionPointPSPRead(BaseModel):
    id:                  uuid.UUID
    collection_point_id: uuid.UUID
    psp_config_id:       uuid.UUID
    label:               Optional[str]
    is_active:           bool
    created_at:          datetime

    class Config:
        from_attributes = True
