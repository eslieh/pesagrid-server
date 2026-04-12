import uuid
from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, field_validator
from app.modules.obligations.models import (
    ObligationStatus, RecurrenceType,
    GroupType, TemplateChannel, TemplateType,
)


# ─── PayerGroup Schemas ────────────────────────────────────────────────────────

class PayerGroupCreate(BaseModel):
    name:        str
    description: Optional[str] = None
    group_type:  GroupType     = GroupType.CUSTOM
    meta:        Optional[dict] = None  # e.g. {"floor": 2, "block": "A", "capacity": 30}


class PayerGroupUpdate(BaseModel):
    name:        Optional[str]       = None
    description: Optional[str]       = None
    group_type:  Optional[GroupType] = None
    meta:        Optional[dict]       = None


class PayerGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            uuid.UUID
    collection_id: uuid.UUID
    name:          str
    description:   Optional[str]
    group_type:    GroupType
    meta:          Optional[dict]
    created_by:    uuid.UUID
    created_at:    datetime
    updated_at:    datetime


# ─── Payer Schemas ─────────────────────────────────────────────────────────────

class PayerCreate(BaseModel):
    name:       str
    phone:      Optional[str] = None
    email:      Optional[str] = None
    account_no: Optional[str] = None   # M-PESA sub-account (e.g. "UNIT-3B")
    identifier: Optional[str] = None   # student no, member no, etc.
    group_id:   Optional[uuid.UUID] = None
    notes:      Optional[str] = None
    meta:       Optional[dict] = None  # e.g. {"grade": "7A", "guardian": "John Doe"}


class PayerUpdate(BaseModel):
    name:       Optional[str]       = None
    phone:      Optional[str]       = None
    email:      Optional[str]       = None
    account_no: Optional[str]       = None
    identifier: Optional[str]       = None
    group_id:   Optional[uuid.UUID] = None
    notes:      Optional[str]       = None
    is_active:  Optional[bool]      = None
    meta:       Optional[dict]       = None


class PayerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            uuid.UUID
    collection_id: uuid.UUID
    group_id:      Optional[uuid.UUID]
    name:          str
    phone:         Optional[str]
    email:         Optional[str]
    account_no:    Optional[str]
    identifier:    Optional[str]
    notes:         Optional[str]
    is_active:      bool
    credit_balance: float
    meta:           Optional[dict]
    created_by:     uuid.UUID
    created_at:     datetime
    updated_at:     datetime


class PayerListResponse(BaseModel):
    total: int
    items: List[PayerResponse]


# ─── RecurringConfig Schemas ───────────────────────────────────────────────────

class RecurringConfigCreate(BaseModel):
    recurrence_type:   RecurrenceType
    start_date:        datetime
    end_date:          Optional[datetime] = None
    interval_days:     Optional[int]      = None
    day_of_month:      Optional[int]      = None   # 1-28, MONTHLY
    day_of_week:       Optional[int]      = None   # 0=Mon…6=Sun, WEEKLY
    grace_period_days: int  = 0
    auto_generate:     bool = True

    @field_validator("day_of_month")
    @classmethod
    def validate_day_of_month(cls, v):
        if v is not None and not (1 <= v <= 28):
            raise ValueError("day_of_month must be between 1 and 28")
        return v

    @field_validator("day_of_week")
    @classmethod
    def validate_day_of_week(cls, v):
        if v is not None and not (0 <= v <= 6):
            raise ValueError("day_of_week must be 0 (Mon) – 6 (Sun)")
        return v


class RecurringConfigUpdate(BaseModel):
    end_date:          Optional[datetime] = None
    grace_period_days: Optional[int]      = None
    auto_generate:     Optional[bool]     = None
    next_due_date:     Optional[datetime] = None


class RecurringConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               uuid.UUID
    obligation_id:    uuid.UUID
    recurrence_type:  RecurrenceType
    interval_days:    Optional[int]
    day_of_month:     Optional[int]
    day_of_week:      Optional[int]
    start_date:       datetime
    end_date:         Optional[datetime]
    next_due_date:    Optional[datetime]
    grace_period_days: int
    auto_generate:    bool
    created_at:       datetime


# ─── Obligation Schemas ────────────────────────────────────────────────────────

class ObligationCreate(BaseModel):
    payer_id:     uuid.UUID
    account_no:   Optional[str] = None
    description:  Optional[str]  = None
    amount_due:   float
    currency:     str            = "KES"
    due_date:     Optional[datetime] = None
    is_recurring: bool           = False
    recurring:    Optional[RecurringConfigCreate] = None
    # Tenant-defined fields: e.g. {"invoice_no": "INV-001", "term": "Term 1 2025", "penalty_rate": 0.05}
    meta:         Optional[dict] = None

    @field_validator("amount_due")
    @classmethod
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError("amount_due must be positive")
        return v


class UnifiedPayerObligationCreate(BaseModel):
    """
    Person-centric creation: Register a person and their first invoice in one flow.
    """
    # Payer details
    name:       str
    phone:      Optional[str] = None
    email:      Optional[str] = None
    account_no: Optional[str] = None
    identifier: Optional[str] = None
    group_id:   Optional[uuid.UUID] = None
    
    # Obligation details
    amount:       float
    description:  Optional[str] = None
    due_date:     Optional[datetime] = None
    currency:     str = "KES"
    
    # Recurring details
    is_recurring: bool = False
    recurring:    Optional[RecurringConfigCreate] = None
    
    # Flags
    send_notification: bool = True


class ObligationUpdate(BaseModel):
    description:  Optional[str]             = None
    amount_due:   Optional[float]           = None
    due_date:     Optional[datetime]        = None
    status:       Optional[ObligationStatus] = None
    meta:         Optional[dict]             = None


class ObligationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:              uuid.UUID
    collection_id:   uuid.UUID
    payer_id:        uuid.UUID
    account_no:      str
    description:     Optional[str]
    amount_due:      float
    amount_paid:     float
    balance:         float
    currency:        str
    due_date:        Optional[datetime]
    status:          ObligationStatus
    status_reason:   Optional[str]     = None
    is_recurring:    bool
    meta:            Optional[dict]
    created_by:      uuid.UUID
    created_at:      datetime
    updated_at:      datetime
    payer:           Optional[PayerResponse]           = None
    recurring_config: Optional[RecurringConfigResponse] = None


class ObligationListResponse(BaseModel):
    total: int
    items: List[ObligationResponse]


class RecurringPreviewResponse(BaseModel):
    preview_sentence: str


class PayerLedgerResponse(BaseModel):
    payer:          PayerResponse
    obligations:    List[ObligationResponse]
    total_due:      float
    total_paid:     float
    balance:        float
    credit_balance: float


class ObligationLedgerItem(ObligationResponse):
    """Rich obligation data for the ledger with descriptive strings."""
    status_description: str


class PayerLedgerGroup(BaseModel):
    """Groups a payer with their filtered obligations for the dashboard."""
    payer:        PayerResponse
    summary_text: str
    total_amount: float
    status:       str  # top-level status for the group (e.g. "overdue")
    obligations:  List[ObligationLedgerItem]


class GlobalLedgerResponse(BaseModel):
    """The full dashboard response with grouped payers and filter counts."""
    total_payers: int
    counts:       dict  # e.g. {"overdue": 1, "pending": 2, "paid_this_month": 5}
    items:        List[PayerLedgerGroup]


# ─── Tracker / Summary Schemas (materialized-view powered) ────────────────────

class PayerSummaryRow(BaseModel):
    """
    One row in the fast tracker board — sourced from obligations.ledger_summary
    materialized view. No Python-level aggregation needed.
    """
    payer_id:          uuid.UUID
    payer_name:        str
    payer_phone:       Optional[str] = None
    payer_account_no:  Optional[str] = None
    payer_is_active:   bool = True
    group_id:          Optional[uuid.UUID] = None
    group_name:        Optional[str] = None
    total_obligations: int
    total_due:         float
    total_paid:        float
    total_balance:     float
    overdue_count:     int
    pending_count:     int
    settled_count:     int
    next_due_date:     Optional[datetime] = None
    last_activity:     Optional[datetime] = None
    # Computed by the service — "overdue" | "pending" | "settled" | "clear"
    payer_status:      str


class TrackerCounts(BaseModel):
    total:   int
    overdue: int
    pending: int
    settled: int
    clear:   int


class TrackerSummaryResponse(BaseModel):
    """Paginated obligation tracker board — replaces the old GlobalLedgerResponse."""
    total_payers:   int
    total_balance:  float
    total_due:      float
    total_paid:     float
    counts:         TrackerCounts
    page:           int
    page_size:      int
    items:          List[PayerSummaryRow]


class GroupSummaryResponse(BaseModel):
    """Roll-up for a single PayerGroup — all computed by SQL, no Python loops."""
    group_id:      uuid.UUID
    group_name:    str
    group_type:    GroupType
    description:   Optional[str] = None
    total_payers:  int
    total_due:     float
    total_paid:    float
    total_balance: float
    overdue_count: int
    pending_count: int
    settled_count: int
    payers:        List[PayerSummaryRow]


class ObligationCalendarEntry(BaseModel):
    """One day bucket in the upcoming-payments calendar view."""
    due_date:    date
    total_due:   float
    total_count: int
    paid_count:  int
    unpaid_count: int
    obligations: List[ObligationResponse]


class UpcomingPaymentsResponse(BaseModel):
    """Date-bucketed view of the next N days of obligations."""
    window_days:    int
    total_upcoming: int
    entries:        List[ObligationCalendarEntry]


# ─── NotificationTemplate Schemas ─────────────────────────────────────────────

class NotificationTemplateCreate(BaseModel):
    name:          str
    template_type: TemplateType
    channel:       TemplateChannel
    subject:       Optional[str] = None   # email subject
    body:          str                    # supports {{payer_name}}, {{amount_due}}, etc.
    is_default:    bool = False


class NotificationTemplateUpdate(BaseModel):
    name:       Optional[str]            = None
    subject:    Optional[str]            = None
    body:       Optional[str]            = None
    is_active:  Optional[bool]           = None
    is_default: Optional[bool]           = None


class NotificationTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            uuid.UUID
    collection_id: uuid.UUID
    name:          str
    template_type: TemplateType
    channel:       TemplateChannel
    subject:       Optional[str]
    body:          str
    is_active:     bool
    is_default:    bool
    created_by:    uuid.UUID
    created_at:    datetime
    updated_at:    datetime


class NotificationTemplateListResponse(BaseModel):
    total: int
    items: List[NotificationTemplateResponse]


class NotificationTemplateLibraryItem(BaseModel):
    name:          str
    template_type: TemplateType
    channel:       TemplateChannel
    subject:       Optional[str]
    body:          str
    category:      str
    description:   Optional[str]


class NotificationTemplateLibraryResponse(BaseModel):
    total: int
    items: List[NotificationTemplateLibraryItem]


class NotificationTemplateBulkCreate(BaseModel):
    templates: List[NotificationTemplateCreate]
