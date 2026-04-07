import logging
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime

from app.core.dependancies import get_current_verified_user, get_db
from app.modules.auth.models import User
from app.modules.dashboard.schema import (
    DashboardMetrics, PaymentByAccountSummary, PaymentHistoryResponse,
    NotificationPreferences, CollectionPointSummary, TrendResponse, PeakTimeResponse,
    CollectionPointInsight, DashboardSearchResponse,
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
    collection_point_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Returns aggregated metrics for the dashboard:
    Total collected, total matched, total unmatched, and outstanding balances.
    Supports filtering by collection_point_id and date ranges.
    Cached via Redis for speed.
    """
    return await service.get_metrics(
        collection_point_id=collection_point_id,
        start_date=start_date,
        end_date=end_date
    )


@dashboard_router.get(
    "/collections/points",
    response_model=List[CollectionPointSummary],
    summary="Get aggregated metrics per collection point (e.g. per Bus)",
)
async def get_collection_point_metrics(
    start_date: Optional[datetime] = Query(None),
    end_date:   Optional[datetime] = Query(None),
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Returns total volume and transaction counts for all active collection points.
    Ideal for fleet tracking or branch performance monitoring.
    """
    return await service.get_collection_point_metrics(start_date=start_date, end_date=end_date)


@dashboard_router.get(
    "/collections/trends",
    response_model=TrendResponse,
    summary="Get historical collection trends",
)
async def get_collection_trends(
    interval: str = Query("day", regex="^(day|week|month|year)$"),
    collection_point_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Returns historical trend data (total collected and count) broken down by interval.
    Useful for daily/weekly/monthly/yearly reporting.
    """
    return await service.get_collection_trends(
        interval=interval,
        collection_point_id=collection_point_id,
        start_date=start_date,
        end_date=end_date
    )


@dashboard_router.get(
    "/collections/peak-times",
    response_model=PeakTimeResponse,
    summary="Get peak collection times by hour",
)
async def get_peak_collection_times(
    collection_point_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Returns collection volume and count broken down by hour of the day.
    Helps identify peak transaction periods.
    """
    # print(collection_point_id)
    peaks = await service.get_peak_collection_times(
        collection_point_id=collection_point_id,
        start_date=start_date,
        end_date=end_date
    )
    return PeakTimeResponse(peaks=peaks)


@dashboard_router.get(
    "/payments/accounts",
    response_model=List[PaymentByAccountSummary],
    summary="Get payments grouped by account",
)
async def get_payments_by_account(
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Returns a list of accounts with their total amount paid and last payment date.
    Supports optional date filtering (e.g., for daily tracking).
    Cached via Redis.
    """
    return await service.get_payments_by_account(
        start_date=start_date, 
        end_date=end_date, 
        skip=skip, 
        limit=limit
    )


@dashboard_router.get(
    "/payments",
    response_model=PaymentHistoryResponse,
    summary="Get recent payment history",
)
async def get_payment_history(
    collection_point_id: Optional[uuid.UUID] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    service: DashboardService = Depends(get_dashboard_service)
):
    """Returns a paginated list of all recent transactions."""
    total, items = await service.get_payment_history(
        skip=skip, 
        limit=limit, 
        collection_point_id=collection_point_id
    )
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


@dashboard_router.get(
    "/collections/{cp_id}/insights",
    response_model=CollectionPointInsight,
    summary="Get intelligence snapshot for a collection point",
)
async def get_collection_point_insights(
    cp_id: uuid.UUID,
    service: DashboardService = Depends(get_dashboard_service),
):
    """
    Returns a full intelligence snapshot for a single collection point:

    - **pace** — goal progress, daily pace actual vs. required, projected final total
      (only populated when `goal_amount` and `end_date` are set on the CP)
    - **channels** — breakdown of collections by payment channel (psp_type)
    - **compliance** — transactions above the CP's `compliance_threshold` flagged for review
    - **insight_text** — one human-readable sentence summarising the key finding

    Responses are cached for 60 seconds.
    """
    return await service.get_collection_point_insights(cp_id)


@dashboard_router.get(
    "/search",
    response_model=DashboardSearchResponse,
    summary="Global search across payers, invoices, and transactions",
)
async def global_search(
    q: str = Query(..., min_length=2),
    service: DashboardService = Depends(get_dashboard_service)
):
    """
    Powerful multi-entity search for the person-centric navigation bar.
    Searches by name, phone, account number, or payment reference.
    """
    items = await service.global_search(q)
    return DashboardSearchResponse(items=items)
