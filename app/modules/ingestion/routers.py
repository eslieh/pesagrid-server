import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Query, Request, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
import logging

from app.core.dependancies import get_current_verified_user, get_db, verify_mfa
from app.modules.auth.models import User
from app.modules.ingestion.models import TransactionStatus, CollectionPointType
from app.modules.ingestion.schema import (
    TransactionResponse, TransactionListResponse, ManualPaymentCreate,
    MpesaC2BCallback, MpesaSTKCallback,
    CollectionPointCreate, CollectionPointUpdate, CollectionPointRead,
    CollectionPointPSPCreate, CollectionPointPSPRead,
    TransactionEnrichedListResponse,
    CollectionPointListResponse,
)
from app.modules.ingestion.services import IngestionService, CollectionPointService
from app.rabbitmq import BasePublisher, EventType, Priority


logger = logging.getLogger(__name__)

# ─── Webhook router (no auth — PSPs fire these) ───────────────────────────────
webhook_router = APIRouter(tags=["Webhooks"])

# ─── Transactions router (authenticated — business views/adds payments) ────────
transactions_router = APIRouter(tags=["Transactions"])

# ─── Collection Points router (authenticated — fleet/bulk tracking) ─────────────
collection_points_router = APIRouter(tags=["Collection Points"])


def get_collection_point_service(
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
) -> CollectionPointService:
    return CollectionPointService(db=db, collection_id=current_user.id)


async def publish_config_event(event_type: EventType, payload: dict):
    publisher = BasePublisher("ingestion-service")
    await publisher.publish_event(event_type, payload)

def get_ingestion_service(
    collection_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> IngestionService:
    """Resolve IngestionService by tenant ID from the URL path."""
    return IngestionService(db=db, collection_id=collection_id)


def get_authed_service(
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
) -> IngestionService:
    return IngestionService(db=db, collection_id=current_user.id)


# ══════════════════════════════════════════════════════════════════════════════
#  WEBHOOK ENDPOINTS — no authentication
#  PSPs call these with raw payment callbacks
# ══════════════════════════════════════════════════════════════════════════════

@webhook_router.post(
    "/{collection_id}/c2b/callback",
    summary="C2B / STK callback",
    status_code=status.HTTP_200_OK,
)
async def mpesa_callback(
    collection_id: uuid.UUID,
    request: Request,
):
    """
    Safaricom fires this URL for C2B (paybill) and STK push results.

    **Always returns HTTP 200 immediately** — no DB writes, no processing.
    The raw payload is published to RabbitMQ and the worker picks it up
    asynchronously to normalize, ingest, and reconcile.
    """
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        logger.warning(f"M-PESA webhook received non-JSON body for collection {collection_id}")
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    try:
        publisher = BasePublisher(service_name="ingest-gateway")
        await publisher.publish_event(
            event_type=EventType.WEBHOOK_MPESA,
            payload={
                "collection_id": str(collection_id),
                "raw": payload,
            },
            priority=Priority.HIGH,
        )
    except Exception as e:
        logger.error(f"Failed to queue M-PESA callback for {collection_id}: {e}")

    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@webhook_router.post(
    "/{collection_id}/c2b/validate",
    summary="C2B validation callback",
    status_code=status.HTTP_200_OK,
)
async def mpesa_validate(
    collection_id: uuid.UUID,
    request: Request,
):
    """
    Safaricom fires this URL to validate a payment before completing it.
    Returns HTTP 200 with Accepted status to allow all payments.
    """
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


# ══════════════════════════════════════════════════════════════════════════════
#  TRANSACTION ENDPOINTS — authenticated (business views / manual entry)
# ══════════════════════════════════════════════════════════════════════════════

@transactions_router.post(
    "/",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Manual payment entry",
)
async def create_manual_payment(
    data: ManualPaymentCreate,
    current_user: User = Depends(get_current_verified_user),
    service: IngestionService = Depends(get_authed_service),
):
    """
    Record a payment that arrived outside the normal webhook flow
    (cash, bank transfer, cheque, etc.).

    The transaction is published to the reconciliation queue the same
    way as a webhook payment.
    """
    return await service.ingest_manual(data, current_user_id=current_user.id)


@transactions_router.get(
    "/",
    response_model=TransactionEnrichedListResponse,
    summary="List transactions",
    description=(
        "Returns a paginated, filterable, sortable list of transactions with inline "
        "match context (matched payer, obligation, confidence score, and match reasons). "
        "Use `enriched=false` for a lightweight plain response."
    ),
)
def list_transactions(
    account_no:          Optional[str]               = Query(None, description="Filter by account number"),
    psp_type:            Optional[str]               = Query(None, description="Filter by PSP (mpesa, kcb…)"),
    txn_status:          Optional[TransactionStatus] = Query(None, alias="status"),
    collection_point_id: Optional[uuid.UUID]         = Query(None, description="Filter by collection point"),
    start_date:          Optional[datetime]          = Query(None, description="Filter from date (ingested_at ≥)"),
    end_date:            Optional[datetime]          = Query(None, description="Filter to date (ingested_at ≤)"),
    amount_min:          Optional[float]             = Query(None, description="Minimum transaction amount"),
    amount_max:          Optional[float]             = Query(None, description="Maximum transaction amount"),
    phone:               Optional[str]               = Query(None, description="Filter by phone number (254XXXXXXXXX)"),
    psp_ref:             Optional[str]               = Query(None, description="Filter by transaction reference ID"),
    psp_config_id:       Optional[uuid.UUID]         = Query(None, description="Filter by specific payment channel"),
    search:              Optional[str]               = Query(None, description="Smart search across ref, phone, account, name"),
    sort:                str                         = Query("date_desc", regex="^(date_desc|date_asc|amount_desc|amount_asc)$"),
    skip:                int                         = Query(0, ge=0),
    limit:               int                         = Query(50, ge=1, le=200),
    service: IngestionService = Depends(get_authed_service),
):
    total, items = service.list_transactions_enriched(
        account_no=account_no,
        psp_type=psp_type,
        txn_status=txn_status,
        collection_point_id=collection_point_id,
        start_date=start_date,
        end_date=end_date,
        amount_min=amount_min,
        amount_max=amount_max,
        phone=phone,
        psp_ref=psp_ref,
        psp_config_id=psp_config_id,
        search=search,
        sort=sort,
        skip=skip,
        limit=limit,
    )
    return TransactionEnrichedListResponse(total=total, items=items)




@transactions_router.get(
    "/{transaction_id}",
    response_model=TransactionResponse,
    summary="Get transaction",
)
def get_transaction(
    transaction_id: uuid.UUID,
    service: IngestionService = Depends(get_authed_service),
):
    return service.get_transaction(transaction_id)


# ══════════════════════════════════════════════════════════════════════════════
#  COLLECTION POINT ENDPOINTS — authenticated (fleet / bulk tracking)
# ══════════════════════════════════════════════════════════════════════════════

@collection_points_router.post(
    "/",
    response_model=CollectionPointRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create collection point",
)
def create_collection_point(
    data: CollectionPointCreate,
    background_tasks: BackgroundTasks,
    service: CollectionPointService = Depends(get_collection_point_service),
):
    """
    Define a new target for bulk collections (a bus, a campaign, etc.).
    All payments to this account_no will be grouped here.
    """
    cp = service.create_collection_point(data)
    
    payload = {
        "collection_id": str(cp.collection_id),
        "cp_id": str(cp.id),
        "name": cp.name,
        "account_no": cp.account_no,
    }
    background_tasks.add_task(publish_config_event, EventType.CONFIG_COLLECTION_POINT_CREATED, payload)
    
    return cp


@collection_points_router.get(
    "/",
    response_model=CollectionPointListResponse,
    summary="List collection points",
)
def list_collection_points(
    search: Optional[str] = Query(None, description="Search by name, account_no, or description"),
    cp_type: Optional[CollectionPointType] = Query(None, description="Filter by instrument type"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    service: CollectionPointService = Depends(get_collection_point_service),
):
    """
    List all collection points for the business.
    Supports advanced filtering by type and search strings.
    """
    total, items = service.list_collection_points(
        search=search,
        cp_type=cp_type,
        is_active=is_active,
        skip=skip,
        limit=limit,
    )
    return CollectionPointListResponse(total=total, items=items)


@collection_points_router.get(
    "/{cp_id}",
    response_model=CollectionPointRead,
    summary="Get collection point details",
)
def get_collection_point(
    cp_id: uuid.UUID,
    service: CollectionPointService = Depends(get_collection_point_service),
):
    return service.get_collection_point(cp_id)


@collection_points_router.get(
    "/{cp_id}/totals",
    summary="Get collection point volume",
)
def get_collection_point_totals(
    cp_id: uuid.UUID,
    service: CollectionPointService = Depends(get_collection_point_service),
):
    """Returns the total sum of all money collected by this specific target."""
    return service.get_collection_point_totals(cp_id)


@collection_points_router.patch(
    "/{cp_id}",
    response_model=CollectionPointRead,
    summary="Update collection point",
)
def update_collection_point(
    cp_id: uuid.UUID,
    data: CollectionPointUpdate,
    service: CollectionPointService = Depends(get_collection_point_service),
):
    return service.update_collection_point(cp_id, data)

@collection_points_router.delete(
    "/{cp_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete collection point",
)
def delete_collection_point(
    cp_id: uuid.UUID,
    service: CollectionPointService = Depends(get_collection_point_service),
):
    service.delete_collection_point(cp_id)


# ── PSP channel links ─────────────────────────────────────────────────────────

@collection_points_router.post(
    "/{cp_id}/channels",
    response_model=CollectionPointPSPRead,
    status_code=status.HTTP_201_CREATED,
    summary="Link a PSP channel to a collection point",
)
def add_psp_channel(
    cp_id: uuid.UUID,
    data: CollectionPointPSPCreate,
    service: CollectionPointService = Depends(get_collection_point_service),
):
    """
    Declare that this collection point receives payments via the given PSP config.
    Used purely for analytics channel breakdown — routing still uses account_no.
    """
    return service.add_psp(cp_id, data)


@collection_points_router.get(
    "/{cp_id}/channels",
    response_model=List[CollectionPointPSPRead],
    summary="List PSP channels linked to a collection point",
)
def list_psp_channels(
    cp_id: uuid.UUID,
    service: CollectionPointService = Depends(get_collection_point_service),
):
    """Returns all payment channels declared for this collection point."""
    return service.list_psps(cp_id)


@collection_points_router.delete(
    "/{cp_id}/channels/{psp_config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink a PSP channel from a collection point",
)
def remove_psp_channel(
    cp_id: uuid.UUID,
    psp_config_id: uuid.UUID,
    service: CollectionPointService = Depends(get_collection_point_service),
):
    service.remove_psp(cp_id, psp_config_id)
