import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, field_validator
from app.modules.ingestion.models import TransactionStatus
from app.modules.accounts.models import PSPType


# ─── Inbound webhook payloads (for docs / optional validation) ───────────────

class MpesaC2BCallback(BaseModel):
    """Safaricom C2B callback shape (used for OpenAPI docs)."""
    TransactionType:   Optional[str] = None
    TransID:           str
    TransTime:         str
    TransAmount:       str
    BusinessShortCode: str
    BillRefNumber:     str
    InvoiceNumber:     Optional[str] = None
    OrgAccountBalance: Optional[str] = None
    ThirdPartyTransID: Optional[str] = None
    MSISDN:            str
    FirstName:         Optional[str] = None
    MiddleName:        Optional[str] = None
    LastName:          Optional[str] = None


class MpesaSTKCallback(BaseModel):
    """Safaricom STK push callback shape (used for OpenAPI docs)."""
    Body: Dict[str, Any]


# ─── Transaction response schemas ─────────────────────────────────────────────

class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:                    uuid.UUID
    collection_id:         uuid.UUID
    psp_config_id:         Optional[uuid.UUID]
    psp_type:              str
    psp_ref:               Optional[str]
    amount:                float
    currency:              str
    phone:                 Optional[str]
    account_no:            Optional[str]
    payer_name:            Optional[str]
    status:                TransactionStatus
    matched_obligation_id: Optional[uuid.UUID]
    matched_at:            Optional[datetime]
    is_manual:             bool
    ingested_at:           datetime


class TransactionListResponse(BaseModel):
    total: int
    items: List[TransactionResponse]


# ─── Manual payment entry ──────────────────────────────────────────────────────

class ManualPaymentCreate(BaseModel):
    """A payment entered manually by the business — e.g. cash or bank transfer."""
    account_no: str
    amount:     float
    currency:   str           = "KES"
    phone:      Optional[str] = None
    payer_name: Optional[str] = None
    psp_type:   PSPType       = PSPType.CUSTOM
    psp_ref:    Optional[str] = None   # bank ref, receipt number, etc.
    note:       Optional[str] = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError("amount must be positive")
        return v
