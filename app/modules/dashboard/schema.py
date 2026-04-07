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


# ─── Collection Point Insights ────────────────────────────────────────────────

class PaceSummary(BaseModel):
    """
    Pace and goal-progress stats.
    Only populated when both goal_amount and end_date are set on the CP.
    """
    total_collected:       float
    goal_amount:           float
    progress_pct:          float             # 0-100
    days_elapsed:          int
    days_remaining:        Optional[int]     # None if no end_date
    daily_pace_actual:     float             # avg per day so far
    daily_pace_required:   Optional[float]   # needed per day to hit goal
    pace_delta_pct:        Optional[float]   # (actual - required) / required × 100
    projected_total:       Optional[float]   # if pace holds, where will you land?

class ChannelBreakdownItem(BaseModel):
    psp_type:  str
    total:     float
    count:     int
    pct:       float                        # share of total collected

class LargeTransactionFlag(BaseModel):
    """Transactions above the collection point's compliance_threshold."""
    id:          UUID
    amount:      float
    payer_name:  Optional[str]
    phone:       Optional[str]
    psp_ref:     Optional[str]
    ingested_at: datetime

class CollectionPointInsight(BaseModel):
    """
    Full intelligence snapshot for a single collection point.

    pace        — populated only when goal_amount + end_date are set
    channels    — populated only when PSP links exist; otherwise empty list
    compliance  — populated when compliance_threshold is set and triggered
    insight_text — one human-readable sentence summarising the key finding
    """
    cp_id:         UUID
    cp_name:       str
    cp_type:       str
    pace:          Optional[PaceSummary]            = None
    channels:      List[ChannelBreakdownItem]        = []
    compliance:    List[LargeTransactionFlag]        = []
    insight_text:  str
