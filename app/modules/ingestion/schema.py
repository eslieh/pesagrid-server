import uuid
from datetime import datetime
from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field
from app.modules.ingestion.models import TransactionStatus

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
    ingested_at:           datetime

    class Config:
        from_attributes = True

class TransactionListResponse(BaseModel):
    total: int
    items: List[TransactionResponse]

# ─── CollectionPoint Schemas ──────────────────────────────────────────────────

class CollectionPointBase(BaseModel):
    name:                str = Field(..., example="Matatu KAB-123C")
    account_no:          str = Field(..., example="KAB-123C")
    description:         Optional[str] = None
    is_active:           bool = True
    sms_acknowledgement: bool = False
    meta:                Optional[Dict] = {}

class CollectionPointCreate(CollectionPointBase):
    pass

class CollectionPointUpdate(BaseModel):
    name:                Optional[str] = None
    account_no:          Optional[str] = None
    description:         Optional[str] = None
    is_active:           Optional[bool] = None
    sms_acknowledgement: Optional[bool] = None
    meta:                Optional[Dict] = None

class CollectionPointRead(CollectionPointBase):
    id:                  uuid.UUID
    collection_id:       uuid.UUID
    sms_acknowledgement: bool
    created_at:          datetime

    class Config:
        from_attributes = True

