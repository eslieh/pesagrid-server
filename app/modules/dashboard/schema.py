from pydantic import BaseModel, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID

class DashboardMetrics(BaseModel):
    total_collected: float
    total_matched: float
    total_unmatched: float
    outstanding_balances: float

class PaymentByAccountSummary(BaseModel):
    account_no: str
    payer_name: Optional[str] = None
    total_paid: float
    last_payment_date: Optional[datetime] = None

class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    psp_type: str
    psp_ref: Optional[str] = None
    amount: float
    currency: str
    account_no: Optional[str] = None
    payer_name: Optional[str] = None
    status: str
    ingested_at: datetime
    is_manual: bool

class PaymentHistoryResponse(BaseModel):
    total: int
    items: List[TransactionResponse]

class NotificationPreferences(BaseModel):
    payment_notifications_enabled: bool = False
    payment_notification_channels: List[str] = ["email"]

class CollectionPointSummary(BaseModel):
    id: str
    name: str
    account_no: str
    total_collected: float
    transaction_count: int

class TrendItem(BaseModel):
    period: str
    total: float
    count: int

class TrendResponse(BaseModel):
    interval: str  # day, week, month, year
    trends: List[TrendItem]

class PeakTimeItem(BaseModel):
    hour: int
    total: float
    count: int

class PeakTimeResponse(BaseModel):
    peaks: List[PeakTimeItem]
