"""
Billing API router — subscription plans, wallet top-ups, invoices.
"""
import hashlib
import hmac
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependancies import get_current_verified_user, get_db
from app.modules.auth.models import User
from app.modules.billing.models import PlanSlug
from app.modules.billing.schema import (
    BillingSummaryResponse,
    InvoiceListResponse,
    InvoiceResponse,
    PlanListResponse,
    PlanResponse,
    SubscribeRequest,
    SubscriptionResponse,
    TopupInitResponse,
    TopupRequest,
    TopupVerifyRequest,
    TopupVerifyResponse,
    WalletResponse,
    WalletTransactionListResponse,
)
from app.modules.billing.services import BillingService
from app.rabbitmq import BasePublisher, EventType

logger = logging.getLogger(__name__)
billing_router = APIRouter(tags=["Billing"])


def get_service(
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
) -> BillingService:
    return BillingService(db=db, collection_id=current_user.id)


# ══════════════════════════════════════════════════════════════════════════════
#  Plans (public)
# ══════════════════════════════════════════════════════════════════════════════

@billing_router.get(
    "/plans",
    response_model=PlanListResponse,
    summary="List all subscription plans",
)
def list_plans(
    db: Session = Depends(get_db),
):
    """Public endpoint — no auth required. Returns Starter, Growth, Enterprise."""
    svc = BillingService(db=db, collection_id=uuid.uuid4())  # collection_id unused here
    plans = svc.list_plans()
    return PlanListResponse(items=[PlanResponse.model_validate(p) for p in plans])


# ══════════════════════════════════════════════════════════════════════════════
#  Subscription
# ══════════════════════════════════════════════════════════════════════════════

@billing_router.post(
    "/subscribe",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Choose or change your subscription plan",
)
def subscribe(
    data: SubscribeRequest,
    background_tasks: BackgroundTasks,
    service: BillingService = Depends(get_service),
):
    """
    Select a plan (Starter / Growth / Enterprise).
    Creates a trial if first subscription, or upgrades/downgrades an existing one.
    """
    sub = service.subscribe(data.plan_slug)
    # Publish trial event if the tenant is still in trial
    from app.modules.billing.models import SubscriptionStatus
    if sub.status == SubscriptionStatus.TRIAL:
        background_tasks.add_task(
            _publish_event,
            EventType.BILLING_SUBSCRIPTION_TRIAL,
            {
                "collection_id": str(sub.collection_id),
                "plan_slug": data.plan_slug.value,
                "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
            },
        )
    return SubscriptionResponse.model_validate(sub)


@billing_router.get(
    "/subscription",
    response_model=SubscriptionResponse,
    summary="Get my current subscription",
)
def get_my_subscription(
    service: BillingService = Depends(get_service),
):
    sub = service.get_my_subscription()
    return SubscriptionResponse.model_validate(sub)


@billing_router.get(
    "/summary",
    response_model=BillingSummaryResponse,
    summary="Subscription, wallet and estimated charges for current month",
)
def get_billing_summary(
    service: BillingService = Depends(get_service),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    from decimal import Decimal
    sub = service.get_my_subscription()
    wallet = service.get_my_wallet()
    plan = sub.plan

    recon_est = Decimal(str(plan.recon_fee_kes)) * sub.recon_count
    notif_est = Decimal(str(plan.notification_fee_kes)) * sub.notification_count
    total_est = recon_est + notif_est

    return BillingSummaryResponse(
        subscription=SubscriptionResponse.model_validate(sub),
        wallet=WalletResponse.model_validate(wallet),
        recon_est=recon_est,
        notification_est=notif_est,
        current_month_est=total_est,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Wallet
# ══════════════════════════════════════════════════════════════════════════════

@billing_router.get(
    "/wallet",
    response_model=WalletResponse,
    summary="Get wallet balance",
)
def get_wallet(service: BillingService = Depends(get_service)):
    wallet = service.get_my_wallet()
    return WalletResponse.model_validate(wallet)


@billing_router.post(
    "/wallet/topup",
    response_model=TopupInitResponse,
    summary="Initiate a wallet top-up via Paystack",
)
def initiate_topup(
    data: TopupRequest,
    service: BillingService = Depends(get_service),
):
    """
    Creates a Paystack payment session.
    Returns `payment_url` — redirect the user there to complete payment.
    """
    return service.initiate_topup(
        amount_kes=data.amount_kes,
        email=data.email,
        callback_url=data.callback_url,
    )


@billing_router.post(
    "/wallet/topup/verify",
    response_model=TopupVerifyResponse,
    summary="Verify and credit a Paystack top-up",
)
def verify_topup(
    data: TopupVerifyRequest,
    service: BillingService = Depends(get_service),
):
    """
    Call after Paystack redirects back. Pass the `reference` from the URL.
    Idempotent — safe to call multiple times.
    """
    return service.verify_topup(data.reference)


@billing_router.get(
    "/wallet/transactions",
    response_model=WalletTransactionListResponse,
    summary="List wallet transactions (top-ups and deductions)",
)
def list_transactions(
    skip:  int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    service: BillingService = Depends(get_service),
):
    total, balance, items = service.list_wallet_transactions(skip=skip, limit=limit)
    return WalletTransactionListResponse(total=total, balance_kes=balance, items=items)


# ══════════════════════════════════════════════════════════════════════════════
#  Invoices
# ══════════════════════════════════════════════════════════════════════════════

@billing_router.get(
    "/invoices",
    response_model=InvoiceListResponse,
    summary="List platform invoices",
)
def list_invoices(
    skip:  int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=60),
    service: BillingService = Depends(get_service),
):
    total, items = service.list_invoices(skip=skip, limit=limit)
    return InvoiceListResponse(total=total, items=items)


@billing_router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceResponse,
    summary="Get invoice detail",
)
def get_invoice(
    invoice_id: uuid.UUID,
    service: BillingService = Depends(get_service),
):
    return InvoiceResponse.model_validate(service.get_invoice(invoice_id))


# ══════════════════════════════════════════════════════════════════════════════
#  Paystack webhook (no auth — verified by HMAC signature)
# ══════════════════════════════════════════════════════════════════════════════

@billing_router.post(
    "/paystack/webhook",
    status_code=status.HTTP_200_OK,
    summary="Paystack payment webhook",
    include_in_schema=False,   # hide from public docs
)
async def paystack_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_paystack_signature: Optional[str] = Header(None, alias="x-paystack-signature"),
):
    """
    Paystack sends events here for:
      - charge.success → credit wallet
    
    Verified via HMAC-SHA512 of the raw body using PAYSTACK_SECRET_KEY.
    """
    body_bytes = await request.body()

    # Verify signature
    expected = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode(),
        body_bytes,
        hashlib.sha512,
    ).hexdigest()
    if not x_paystack_signature or not hmac.compare_digest(expected, x_paystack_signature):
        raise HTTPException(status_code=400, detail="Invalid Paystack signature")

    try:
        payload = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = payload.get("event", "")
    data  = payload.get("data", {})

    if event == "charge.success":
        meta = data.get("metadata", {})
        if meta.get("type") == "wallet_topup":
            collection_id_str = meta.get("collection_id")
            ref = data.get("reference", "")
            if collection_id_str and ref:
                try:
                    svc = BillingService(db=db, collection_id=uuid.UUID(collection_id_str))
                    svc.verify_topup(ref)
                except Exception as exc:
                    logger.error(f"Webhook topup failed for ref={ref}: {exc}")

    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────────
#  Internal helper
# ──────────────────────────────────────────────────────────────────────────────

async def _publish_event(event_type: EventType, payload: dict) -> None:
    publisher = BasePublisher("billing-service")
    await publisher.publish_event(event_type, payload)
