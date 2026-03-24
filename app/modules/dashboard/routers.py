import logging
from typing import List
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependancies import get_current_verified_user, get_db
from app.modules.auth.models import User
from app.modules.dashboard.schema import (
    DashboardMetrics, PaymentByAccountSummary, PaymentHistoryResponse, 
    NotificationPreferences
)
from app.modules.dashboard.services import DashboardService

logger = logging.getLogger(__name__)

dashboard_router = APIRouter(tags=["Dashboard"])

def get_dashboard_service(
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
) -> DashboardService:
    return DashboardService(db=db, collection_id=current_user.id)


@dashboard_router.get(
    "/metrics",
    response_model=DashboardMetrics,
    summary="Get top-level dashboard metrics",
)
async def get_metrics(
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Returns aggregated metrics for the dashboard:
    Total collected, total matched, total unmatched, and outstanding balances.
    Cached via Redis for speed.
    """
    return await service.get_metrics()


@dashboard_router.get(
    "/payments/accounts",
    response_model=List[PaymentByAccountSummary],
    summary="Get payments grouped by account",
)
async def get_payments_by_account(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Returns a list of accounts with their total amount paid and last payment date.
    Cached via Redis.
    """
    return await service.get_payments_by_account(skip=skip, limit=limit)


@dashboard_router.get(
    "/payments",
    response_model=PaymentHistoryResponse,
    summary="Get recent payment history",
)
async def get_payment_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    service: DashboardService = Depends(get_dashboard_service)
):
    """Returns a paginated list of all recent transactions."""
    total, items = await service.get_payment_history(skip=skip, limit=limit)
    return PaymentHistoryResponse(total=total, items=items)


@dashboard_router.post(
    "/settings/notifications",
    response_model=NotificationPreferences,
    summary="Update business payment notification preferences",
)
def update_notification_prefs(
    prefs: NotificationPreferences,
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Opt-in to receiving email or SMS notifications when a new payment is made to the platform.
    """
    try:
        updated = service.update_notification_prefs(prefs.model_dump())
        return NotificationPreferences(**updated)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
