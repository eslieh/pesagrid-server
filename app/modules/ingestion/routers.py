import uuid
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, Request, HTTPException, status
from sqlalchemy.orm import Session
import logging

from app.core.dependancies import get_current_verified_user, get_db
from app.modules.auth.models import User
from app.modules.ingestion.models import TransactionStatus
from app.modules.ingestion.schema import (
    TransactionResponse, TransactionListResponse, ManualPaymentCreate,
    MpesaC2BCallback, MpesaSTKCallback,
)
from app.modules.ingestion.services import IngestionService
from app.rabbitmq import BasePublisher, EventType, Priority

logger = logging.getLogger(__name__)

# ─── Webhook router (no auth — PSPs fire these) ───────────────────────────────
webhook_router = APIRouter(tags=["Webhooks"])

# ─── Transactions router (authenticated — business views/adds payments) ────────
transactions_router = APIRouter(tags=["Transactions"])


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
    "/{collection_id}/mpesa/callback",
    summary="M-PESA C2B / STK callback",
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
    # ── 1. Parse body ───────────────────────────────────────────────────────
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        logger.warning(f"M-PESA webhook received non-JSON body for collection {collection_id}")
        return {"ResultCode": 0, "ResultDesc": "Accepted"}

    # ── 2. Publish raw payload to worker ────────────────────────────────────
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
        # Log but still ACK — never let Safaricom retry aggressively
        logger.error(f"Failed to queue M-PESA callback for {collection_id}: {e}")

    # ── 3. Acknowledge immediately ───────────────────────────────────────────
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
    response_model=TransactionListResponse,
    summary="List transactions",
)
def list_transactions(
    account_no:    Optional[str]              = Query(None, description="Filter by account number"),
    psp_type:      Optional[str]              = Query(None, description="Filter by PSP (mpesa, kcb…)"),
    txn_status:    Optional[TransactionStatus] = Query(None, alias="status"),
    skip:          int                        = Query(0, ge=0),
    limit:         int                        = Query(50, ge=1, le=200),
    service: IngestionService = Depends(get_authed_service),
):
    total, items = service.list_transactions(
        account_no=account_no,
        psp_type=psp_type,
        txn_status=txn_status,
        skip=skip,
        limit=limit,
    )
    return TransactionListResponse(total=total, items=items)


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
