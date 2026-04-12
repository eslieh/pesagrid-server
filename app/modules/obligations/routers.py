import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependancies import get_current_verified_user, get_db
from app.modules.auth.models import User
from app.modules.obligations.models import ObligationStatus, TemplateType, TemplateChannel
from app.modules.obligations.schema import (
    # Groups
    PayerGroupCreate, PayerGroupUpdate, PayerGroupResponse,
    # Payers
    PayerCreate, PayerUpdate, PayerResponse, PayerListResponse,
    # Obligations
    ObligationCreate, ObligationUpdate, ObligationResponse, ObligationListResponse,
    UnifiedPayerObligationCreate, PayerLedgerResponse, RecurringPreviewResponse,
    RecurringConfigUpdate,
    # Templates
    NotificationTemplateCreate, NotificationTemplateUpdate,
    NotificationTemplateResponse, NotificationTemplateListResponse,
    RecurrenceType,
    GlobalLedgerResponse, PayerLedgerResponse,
    # Tracker
    TrackerSummaryResponse, GroupSummaryResponse, UpcomingPaymentsResponse,
)
from datetime import datetime
from app.modules.obligations.services import ObligationService

obligations_router = APIRouter(tags=["Obligations"])


def get_service(
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
) -> ObligationService:
    """
    ObligationService scoped to the authenticated user's collection.
    In MVP: collection_id == user.id (one workspace per user).
    Swap this dependency later when multi-workspace teams are added.
    """
    return ObligationService(
        db=db,
        collection_id=current_user.id,
        current_user_id=current_user.id,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  PAYER GROUPS  —  /groups
# ══════════════════════════════════════════════════════════════════════════════

@obligations_router.post(
    "/groups",
    response_model=PayerGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create payer group",
)
async def create_group(
    data: PayerGroupCreate,
    service: ObligationService = Depends(get_service),
):
    """
    Create a named cohort of payers.

    Examples:
    - Rental → `{name: "Block A", group_type: "apartment_block"}`
    - School  → `{name: "Grade 7", group_type: "school_class"}`
    - SACCO   → `{name: "Route 12", group_type: "bus_route"}`

    Use the `meta` field to attach any extra data your business needs
    without requiring a schema change.
    """
    return await service.create_group(data)


@obligations_router.get(
    "/groups",
    response_model=List[PayerGroupResponse],
    summary="List payer groups",
)
async def list_groups(
    skip:  int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    service: ObligationService = Depends(get_service),
):
    _, items = await service.list_groups(skip=skip, limit=limit)
    return items


@obligations_router.get(
    "/groups/{group_id}/summary",
    response_model=GroupSummaryResponse,
    summary="Group obligation roll-up drill-down",
)
async def get_group_summary(
    group_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    """
    Single-query roll-up for a PayerGroup: total due, paid, outstanding balance,
    and per-payer status rows. Cached for 60 seconds.

    Use this to power a **group detail card** showing which members have paid
    and which haven't, without loading individual obligations.
    """
    return await service.get_group_summary(group_id)


@obligations_router.get(
    "/groups/{group_id}",
    response_model=PayerGroupResponse,
    summary="Get payer group",
)
async def get_group(
    group_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    return await service.get_group(group_id)


@obligations_router.patch(
    "/groups/{group_id}",
    response_model=PayerGroupResponse,
    summary="Update payer group",
)
async def update_group(
    group_id: uuid.UUID,
    data: PayerGroupUpdate,
    service: ObligationService = Depends(get_service),
):
    return await service.update_group(group_id, data)


@obligations_router.delete(
    "/groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete payer group",
)
async def delete_group(
    group_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    await service.delete_group(group_id)


# ══════════════════════════════════════════════════════════════════════════════
#  PAYERS  —  /payers
# ══════════════════════════════════════════════════════════════════════════════

@obligations_router.post(
    "/payers",
    response_model=PayerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create payer",
)
async def create_payer(
    data: PayerCreate,
    service: ObligationService = Depends(get_service),
):
    """
    Register an individual payer (tenant, student, bus, retailer, member…).

    - `account_no` — the M-PESA sub-account they pay to (e.g. `UNIT-3B`)
    - `identifier` — any business-specific ID (student no, member no, meter no)
    - `group_id`   — optional group this payer belongs to
    - `meta`       — any extra fields your business needs, e.g.
      `{"grade": "7A", "guardian": "Jane Doe", "credit_limit": 50000}`
    """
    return await service.create_payer(data)


@obligations_router.post(
    "/unified-create",
    response_model=ObligationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create payer and first invoice (Person-centric flow)",
)
async def create_unified(
    data: UnifiedPayerObligationCreate,
    service: ObligationService = Depends(get_service),
):
    """
    Person-centric merged flow:
    - Creates or updates a Payer
    - Creates their first Obligation
    - Sets up recurring schedule if provided
    - Sends an automated SMS/email notification (optional, defaults to true)
    
    Returns the created obligation for "Save & view" context.
    """
    _, ob = await service.create_unified_payer_obligation(data)
    return ob


@obligations_router.get(
    "/payers",
    response_model=PayerListResponse,
    summary="List payers",
)
async def list_payers(
    group_id:  Optional[uuid.UUID] = Query(None, description="Filter by group"),
    is_active: Optional[bool]      = Query(None, description="Filter active/inactive payers"),
    skip:      int                 = Query(0, ge=0),
    limit:     int                 = Query(100, ge=1, le=500),
    service: ObligationService = Depends(get_service),
):
    total, items = await service.list_payers(group_id=group_id, is_active=is_active, skip=skip, limit=limit)
    return PayerListResponse(total=total, items=items)


@obligations_router.get(
    "/payers/{payer_id}/ledger",
    response_model=PayerLedgerResponse,
    summary="Get full person-centric statement (Ledger)",
)
async def get_payer_ledger(
    payer_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    """
    Returns a unified ledger for a customer:
    - Every invoice they owe or have paid
    - Aggregate balance (Total Due vs Total Paid)
    """
    return await service.get_payer_ledger(payer_id)


@obligations_router.get(
    "/payers/{payer_id}",
    response_model=PayerResponse,
    summary="Get payer",
)
async def get_payer(
    payer_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    return await service.get_payer(payer_id)


@obligations_router.patch(
    "/payers/{payer_id}",
    response_model=PayerResponse,
    summary="Update payer",
)
async def update_payer(
    payer_id: uuid.UUID,
    data: PayerUpdate,
    service: ObligationService = Depends(get_service),
):
    """Partial update — only fields you send are changed."""
    return await service.update_payer(payer_id, data)


@obligations_router.delete(
    "/payers/{payer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete payer",
)
async def delete_payer(
    payer_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    """Blocked if payer has open (pending/partial/overdue) obligations."""
    await service.delete_payer(payer_id)


# ══════════════════════════════════════════════════════════════════════════════
#  NOTIFICATION TEMPLATES  —  /templates
# ══════════════════════════════════════════════════════════════════════════════

@obligations_router.post(
    "/templates",
    response_model=NotificationTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create notification template",
)
async def create_template(
    data: NotificationTemplateCreate,
    service: ObligationService = Depends(get_service),
):
    """
    Create a reusable message template for automated notifications.

    Supported variables in `body` / `subject`:
    `{{payer_name}}`, `{{amount_due}}`, `{{amount_paid}}`, `{{balance}}`,
    `{{due_date}}`, `{{account_no}}`, `{{collection_name}}`, `{{description}}`

    Example WhatsApp rent reminder:
    ```
    Hi {{payer_name}}, your rent of KSh {{amount_due}} for {{account_no}}
    is due on {{due_date}}. Balance: KSh {{balance}}. Pay via Paybill 123456.
    ```
    """
    return await service.create_template(data)


@obligations_router.get(
    "/templates",
    response_model=NotificationTemplateListResponse,
    summary="List notification templates",
)
async def list_templates(
    template_type: Optional[TemplateType]    = Query(None, alias="type"),
    channel:       Optional[TemplateChannel] = Query(None),
    skip:          int                       = Query(0, ge=0),
    limit:         int                       = Query(50, ge=1, le=200),
    service: ObligationService = Depends(get_service),
):
    total, items = await service.list_templates(template_type=template_type, channel=channel, skip=skip, limit=limit)
    return NotificationTemplateListResponse(total=total, items=items)


@obligations_router.get(
    "/templates/{template_id}",
    response_model=NotificationTemplateResponse,
    summary="Get template",
)
async def get_template(
    template_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    return await service.get_template(template_id)


@obligations_router.patch(
    "/templates/{template_id}",
    response_model=NotificationTemplateResponse,
    summary="Update template",
)
async def update_template(
    template_id: uuid.UUID,
    data: NotificationTemplateUpdate,
    service: ObligationService = Depends(get_service),
):
    return await service.update_template(template_id, data)


@obligations_router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete template",
)
async def delete_template(
    template_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    await service.delete_template(template_id)


# ══════════════════════════════════════════════════════════════════════════════
#  OBLIGATIONS  —  /
# ══════════════════════════════════════════════════════════════════════════════

@obligations_router.post(
    "/",
    response_model=ObligationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create obligation",
)
async def create_obligation(
    data: ObligationCreate,
    service: ObligationService = Depends(get_service),
):
    """
    Create a payment obligation for a payer.

    **One-off**: set `due_date`, leave `is_recurring=false`.

    **Recurring** (rent, bus fare, school fees, chama contributions):
    set `is_recurring=true` and include a `recurring` block:
    ```json
    {
      "recurrence_type": "monthly",
      "day_of_month": 1,
      "start_date": "2025-04-01T00:00:00",
      "grace_period_days": 5
    }
    ```

    Use `meta` for any custom fields:
    ```json
    {"invoice_no": "INV-001", "term": "Term 1 2025", "penalty_rate": 0.05}
    ```
    """
    return await service.create_obligation(data)


@obligations_router.get(
    "/recurring-preview",
    response_model=RecurringPreviewResponse,
    summary="Generate plain-language preview of recurring schedule",
)
async def get_recurring_preview(
    type:     RecurrenceType  = Query(...),
    amount:   float           = Query(...),
    start:    datetime        = Query(...),
    interval: Optional[int]   = Query(None),
    dom:      Optional[int]   = Query(None),
    dow:      Optional[int]   = Query(None),
    service: ObligationService = Depends(get_service),
):
    """
    Returns a human-readable sentence for the UI form:
    'will bill KES 500 every week on Monday starting April 1...'
    """
    sentence = service.get_recurring_preview(
        rt=type, amount=amount, start_date=start,
        interval_days=interval, day_of_month=dom, day_of_week=dow
    )
    return RecurringPreviewResponse(preview_sentence=sentence)


@obligations_router.get(
    "/",
    response_model=ObligationListResponse,
    summary="List obligations",
)
async def list_obligations(
    payer_id:     Optional[uuid.UUID]       = Query(None, description="Filter by payer"),
    account_no:   Optional[str]             = Query(None, description="Filter by account number"),
    status_filter: Optional[ObligationStatus] = Query(None, alias="status"),
    is_recurring: Optional[bool]            = Query(None),
    skip:         int                       = Query(0, ge=0),
    limit:        int                       = Query(50, ge=1, le=200),
    service: ObligationService = Depends(get_service),
):
    total, items = await service.list_obligations(
        payer_id=payer_id,
        account_no=account_no,
        ob_status=status_filter,
        is_recurring=is_recurring,
        skip=skip,
        limit=limit,
    )
    return ObligationListResponse(total=total, items=items)


@obligations_router.get(
    "/ledger",
    response_model=TrackerSummaryResponse,
    summary="Obligation tracker board (paginated, materialized-view backed)",
)
async def get_global_ledger(
    page:          int                 = Query(1, ge=1, description="Page number"),
    page_size:     int                 = Query(25, ge=5, le=100, description="Rows per page"),
    group_id:      Optional[uuid.UUID] = Query(None, description="Drill into a specific group"),
    status_filter: Optional[str]       = Query(None, enum=["overdue", "pending", "settled", "clear"], description="Filter payers by their overall payment status"),
    search:        Optional[str]       = Query(None, description="Search by payer name (case-insensitive)"),
    service: ObligationService = Depends(get_service),
):
    """
    High-performance obligation tracker board.

    Backed by a PostgreSQL materialized view (obligations.ledger_summary)
    so totals are pre-computed server-side — no Python-level aggregation.
    Results are cached in Redis for 30 seconds per tenant.

    **Filter tabs the client can render:**
    - No filter → all payers
    - `status=overdue`  → payers with at least one overdue obligation
    - `status=pending`  → payers with pending obligations (no overdue)
    - `status=settled`  → payers whose open obligations are all settled
    - `status=clear`    → payers with zero outstanding balance

    **Group drill-down:** pass `group_id` to scope the board to one group.
    For a full group roll-up (total payers / due / paid) use `GET /groups/{id}/summary`.
    """
    return await service.get_global_ledger(
        page=page,
        page_size=page_size,
        group_id=group_id,
        status_filter=status_filter,
        search=search,
    )


@obligations_router.get(
    "/ledger/upcoming",
    response_model=UpcomingPaymentsResponse,
    summary="Upcoming payments calendar (next N days)",
)
async def get_upcoming_payments(
    days:     int                  = Query(30, ge=1, le=90, description="Lookahead window in days"),
    group_id: Optional[uuid.UUID]  = Query(None, description="Scope to a specific group"),
    service: ObligationService = Depends(get_service),
):
    """
    Date-bucketed view of all open obligations due in the next N days.

    Returns one entry per calendar date, each containing:
    - `total_due`    — sum of outstanding balances for that day
    - `total_count`  — number of obligations
    - `unpaid_count` — how many are still unpaid
    - `obligations`  — full obligation list for that day (with payer info)

    Use this to build a **payment calendar / timeline** in the UI.
    Results are cached for 60 seconds.
    """
    return await service.get_upcoming_payments(window_days=days, group_id=group_id)


@obligations_router.get(
    "/{obligation_id}",
    response_model=ObligationResponse,
    summary="Get obligation",
)
async def get_obligation(
    obligation_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    return await service.get_obligation(obligation_id)


@obligations_router.patch(
    "/{obligation_id}",
    response_model=ObligationResponse,
    summary="Update obligation",
)
async def update_obligation(
    obligation_id: uuid.UUID,
    data: ObligationUpdate,
    service: ObligationService = Depends(get_service),
):
    """
    Partially update an obligation. You can adjust `amount_due` if the
    requirement changes (reviewed rent, revised invoice, etc.) — the
    `balance` recalculates automatically.
    """
    return await service.update_obligation(obligation_id, data)


@obligations_router.patch(
    "/{obligation_id}/recurring",
    response_model=ObligationResponse,
    summary="Update recurring schedule",
)
async def update_recurring_config(
    obligation_id: uuid.UUID,
    data: RecurringConfigUpdate,
    service: ObligationService = Depends(get_service),
):
    """Adjust grace period, end date, or next_due_date for a recurring obligation."""
    return await service.update_recurring_config(obligation_id, data)


@obligations_router.post(
    "/{obligation_id}/cancel",
    response_model=ObligationResponse,
    summary="Cancel obligation",
)
async def cancel_obligation(
    obligation_id: uuid.UUID,
    reason: str = Query("Manual void", description="Why is this being voided?"),
    service: ObligationService = Depends(get_service),
):
    """Refined cancellation: records the specific reason for audit."""
    return await service.cancel_obligation(obligation_id, reason=reason)




@obligations_router.delete(
    "/{obligation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete obligation",
)
async def delete_obligation(
    obligation_id: uuid.UUID,
    service: ObligationService = Depends(get_service),
):
    """Hard delete. Blocked on fully paid obligations — cancel those instead."""
    await service.delete_obligation(obligation_id)
