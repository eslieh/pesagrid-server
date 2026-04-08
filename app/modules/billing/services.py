"""
BillingService — core business logic for subscriptions, wallets, Paystack top-ups,
usage tracking, and monthly invoice generation.
"""
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timezone import now_nairobi
from app.modules.billing.models import (
    InvoiceStatus, PlanSlug, SubscriptionStatus,
    SubscriptionPlan, TenantSubscription, TenantWallet,
    WalletTransaction, WalletTxEvent, WalletTxType, PlatformInvoice,
)
from app.modules.billing.schema import (
    TopupInitResponse, TopupVerifyResponse,
)

logger = logging.getLogger(__name__)

# Grace period before SUSPENDED → BLOCKED
GRACE_PERIOD_DAYS = 7
# Trial length
TRIAL_DAYS = 30
# Minimum top-up
MIN_TOPUP_KES = Decimal("100.00")


class BillingService:
    def __init__(self, db: Session, collection_id: uuid.UUID):
        self.db = db
        self.collection_id = collection_id

    # ──────────────────────────────────────────────────────────────────────────
    #  Plans
    # ──────────────────────────────────────────────────────────────────────────

    def list_plans(self):
        """Return all active subscription plans ordered by price."""
        return (
            self.db.query(SubscriptionPlan)
            .filter(SubscriptionPlan.is_active == True)
            .order_by(SubscriptionPlan.monthly_fee_kes)
            .all()
        )

    def get_plan_by_slug(self, slug: PlanSlug) -> SubscriptionPlan:
        plan = (
            self.db.query(SubscriptionPlan)
            .filter(SubscriptionPlan.slug == slug, SubscriptionPlan.is_active == True)
            .first()
        )
        if not plan:
            raise HTTPException(status_code=404, detail=f"Plan '{slug}' not found")
        return plan

    # ──────────────────────────────────────────────────────────────────────────
    #  Trial / Subscription bootstrap
    # ──────────────────────────────────────────────────────────────────────────

    def get_or_create_trial(self) -> Tuple[TenantSubscription, TenantWallet]:
        """
        Called on first profile creation. Creates a 30-day TRIAL subscription
        on the Starter plan + an empty wallet.
        Returns the existing subscription if already set up.
        """
        existing = self._get_subscription()
        if existing:
            return existing, self._get_wallet(existing)

        starter = self.get_plan_by_slug(PlanSlug.STARTER)
        now = now_nairobi()

        sub = TenantSubscription(
            collection_id=self.collection_id,
            plan_id=starter.id,
            status=SubscriptionStatus.TRIAL,
            trial_ends_at=now + timedelta(days=TRIAL_DAYS),
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        self.db.add(sub)
        self.db.flush()

        wallet = TenantWallet(
            collection_id=self.collection_id,
            subscription_id=sub.id,
            balance_kes=Decimal("0.00"),
        )
        self.db.add(wallet)
        self.db.commit()
        self.db.refresh(sub)
        self.db.refresh(wallet)
        return sub, wallet

    def subscribe(self, plan_slug: PlanSlug) -> TenantSubscription:
        """
        Switch the tenant to a new plan. Upgrades/downgrades are immediate.
        If coming from TRIAL, move to ACTIVE and set billing period.
        """
        plan = self.get_plan_by_slug(plan_slug)
        sub = self._get_subscription()
        now = now_nairobi()

        if sub:
            sub.plan_id = plan.id
            if sub.status in (SubscriptionStatus.TRIAL, SubscriptionStatus.SUSPENDED):
                sub.status = SubscriptionStatus.ACTIVE
            sub.current_period_start = now
            sub.current_period_end = now.replace(day=1) + timedelta(days=32)
            sub.current_period_end = sub.current_period_end.replace(day=1)  # first of next month
        else:
            sub = TenantSubscription(
                collection_id=self.collection_id,
                plan_id=plan.id,
                status=SubscriptionStatus.TRIAL,
                trial_ends_at=now + timedelta(days=TRIAL_DAYS),
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
            self.db.add(sub)
            self.db.flush()

            wallet = TenantWallet(
                collection_id=self.collection_id,
                subscription_id=sub.id,
            )
            self.db.add(wallet)

        self.db.commit()
        self.db.refresh(sub)
        return sub

    def get_my_subscription(self) -> TenantSubscription:
        sub = self._get_subscription()
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No subscription found. Please select a plan."
            )
        return sub

    # ──────────────────────────────────────────────────────────────────────────
    #  Wallet & Top-ups
    # ──────────────────────────────────────────────────────────────────────────

    def get_my_wallet(self) -> TenantWallet:
        sub = self.get_my_subscription()
        wallet = self._get_wallet(sub)
        if not wallet:
            raise HTTPException(status_code=404, detail="Wallet not found for this subscription.")
        return wallet

    def initiate_topup(self, amount_kes: Decimal, email: str, callback_url: Optional[str] = None) -> TopupInitResponse:
        """
        Create a Paystack payment session for a wallet top-up.
        Returns the checkout URL and reference.
        """
        if amount_kes < MIN_TOPUP_KES:
            raise HTTPException(
                status_code=400,
                detail=f"Minimum top-up is KES {MIN_TOPUP_KES}"
            )

        reference = f"PG-TOPUP-{self.collection_id!s:.8}-{uuid.uuid4().hex[:8].upper()}"
        amount_kobo = int(amount_kes * 100)  # Paystack uses smallest currency unit

        payload = {
            "email": email,
            "amount": amount_kobo,
            "currency": "KES",
            "reference": reference,
            "callback_url": callback_url or f"{settings.CLIENT_URL}/billing/wallet",
            "metadata": {
                "collection_id": str(self.collection_id),
                "type": "wallet_topup",
            },
        }

        resp = self._paystack_post("/transaction/initialize", payload)
        data = resp.get("data", {})

        return TopupInitResponse(
            payment_url=data["authorization_url"],
            reference=reference,
            amount_kes=amount_kes,
        )

    def verify_topup(self, reference: str) -> TopupVerifyResponse:
        """
        Verify a Paystack payment reference and credit the wallet.
        Idempotent — re-verifying an already credited reference returns success.
        """
        # Idempotency: check if reference already credited
        existing = (
            self.db.query(WalletTransaction)
            .filter(
                WalletTransaction.collection_id == self.collection_id,
                WalletTransaction.reference == reference,
                WalletTransaction.tx_type == WalletTxType.TOPUP,
            )
            .first()
        )
        if existing:
            wallet = self.get_my_wallet()
            return TopupVerifyResponse(
                success=True,
                amount_credited=existing.amount_kes,
                balance_kes=wallet.balance_kes,
                message="Payment already credited.",
            )

        # Verify with Paystack
        resp = self._paystack_get(f"/transaction/verify/{reference}")
        data = resp.get("data", {})

        if data.get("status") != "success":
            raise HTTPException(status_code=400, detail="Payment not successful or still pending.")

        amount_kes = Decimal(str(data["amount"])) / 100

        # Credit wallet
        wallet = self.get_my_wallet()
        sub = self.get_my_subscription()
        new_balance = wallet.balance_kes + amount_kes

        wallet.balance_kes = new_balance
        wallet.lifetime_topup += amount_kes
        wallet.last_topup_at = now_nairobi()

        tx = WalletTransaction(
            wallet_id=wallet.id,
            collection_id=self.collection_id,
            tx_type=WalletTxType.TOPUP,
            event_type=WalletTxEvent.TOPUP,
            amount_kes=amount_kes,
            balance_after=new_balance,
            description=f"Wallet top-up via Paystack",
            reference=reference,
            meta={"paystack_ref": reference, "channel": data.get("channel")},
        )
        self.db.add(tx)

        # If tenant was SUSPENDED and balance is now above minimum, reactivate
        plan = sub.plan
        if sub.status == SubscriptionStatus.SUSPENDED and new_balance >= plan.wallet_minimum_kes:
            sub.status = SubscriptionStatus.ACTIVE
            sub.suspended_at = None
            sub.grace_ends_at = None
            logger.info(f"✅ Tenant {self.collection_id} wallet topped up — subscription reactivated")

        self.db.commit()
        self.db.refresh(wallet)

        # Trigger notification to business owner
        try:
            from app.rabbitmq import BasePublisher, EventType, Priority
            import asyncio
            publisher = BasePublisher("billing-service")
            asyncio.create_task(
                publisher.publish_event(
                    EventType.BILLING_TOPUP_SUCCESS,
                    {
                        "collection_id": str(self.collection_id),
                        "amount_credited": float(amount_kes),
                        "balance_kes": float(wallet.balance_kes),
                        "reference": reference,
                    },
                    Priority.MEDIUM
                )
            )
        except Exception as exc:
            logger.debug(f"topup notification event publish failed (non-critical): {exc}")

        return TopupVerifyResponse(
            success=True,
            amount_credited=amount_kes,
            balance_kes=wallet.balance_kes,
            message=f"KES {amount_kes:,.2f} credited to your wallet.",
        )

    # ──────────────────────────────────────────────────────────────────────────
    #  Paystack webhook verification
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def verify_paystack_signature(payload_bytes: bytes, signature: str) -> bool:
        """Verify the X-Paystack-Signature header on inbound webhook calls."""
        expected = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(),
            payload_bytes,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def handle_paystack_webhook(self, event: str, data: dict) -> None:
        """
        Process an inbound Paystack webhook event.
        Currently handles: charge.success (wallet top-ups).
        """
        if event == "charge.success":
            ref = data.get("reference", "")
            meta = data.get("metadata", {})
            if meta.get("type") == "wallet_topup":
                collection_id_str = meta.get("collection_id")
                if collection_id_str:
                    # Set collection_id to the one from the webhook and verify
                    self.collection_id = uuid.UUID(collection_id_str)
                    try:
                        self.verify_topup(ref)
                    except Exception as exc:
                        logger.error(f"Webhook verify_topup failed for ref={ref}: {exc}")

    # ──────────────────────────────────────────────────────────────────────────
    #  Usage deductions (called from RabbitMQ handlers)
    # ──────────────────────────────────────────────────────────────────────────

    def deduct_usage(
        self,
        event_type: WalletTxEvent,
        count: int = 1,
        meta: Optional[dict] = None,
    ) -> None:
        """
        Deduct per-unit usage fees from the wallet.
        If the wallet is below minimum after deduction, trigger suspension flow.

        This is called from the RabbitMQ billing handlers — should never raise
        so it never blocks the originating event.
        """
        try:
            sub = self._get_subscription()
            if not sub or sub.status in (SubscriptionStatus.CANCELLED, SubscriptionStatus.BLOCKED):
                return

            wallet = self._get_wallet(sub)
            if not wallet or not wallet.is_auto_deduct_enabled:
                return

            plan = sub.plan
            if event_type == WalletTxEvent.RECONCILIATION:
                unit_fee = Decimal(str(plan.recon_fee_kes))
                sub.recon_count += count
            elif event_type in (WalletTxEvent.SMS, WalletTxEvent.EMAIL):
                unit_fee = Decimal(str(plan.notification_fee_kes))
                sub.notification_count += count
            elif event_type == WalletTxEvent.INVOICE:
                unit_fee = Decimal(str(plan.invoice_fee_kes))
                sub.invoice_count += count
            else:
                return

            total_fee = (unit_fee * count).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

            # Do not allow balance to go negative
            if wallet.balance_kes < total_fee:
                logger.warning(
                    f"Wallet {wallet.id} insufficient balance (KES {wallet.balance_kes}) "
                    f"for fee KES {total_fee}. Skipping deduction."
                )
                self._check_and_suspend(sub, wallet, plan)
                return

            new_balance = wallet.balance_kes - total_fee
            wallet.balance_kes = new_balance

            # We no longer log individual per-event deductions in WalletTransaction to avoid clutter.
            # Balance accuracy is maintained, and raw notification logs serve as the audit trace.
            
            self.db.commit()

            # Check if balance dropped below minimum → suspension flow
            self._check_and_suspend(sub, wallet, plan)

        except Exception as exc:
            logger.error(f"deduct_usage failed for {self.collection_id}: {exc}")

    def _check_and_suspend(self, sub: TenantSubscription, wallet: TenantWallet, plan: SubscriptionPlan) -> None:
        """If wallet below minimum, move to SUSPENDED and set grace deadline."""
        if wallet.balance_kes >= plan.wallet_minimum_kes:
            return

        if sub.status == SubscriptionStatus.ACTIVE:
            now = now_nairobi()
            sub.status = SubscriptionStatus.SUSPENDED
            sub.suspended_at = now
            sub.grace_ends_at = now + timedelta(days=GRACE_PERIOD_DAYS)
            self.db.commit()
            logger.warning(
                f"⚠️ Tenant {self.collection_id} suspended — wallet KES {wallet.balance_kes} "
                f"below minimum KES {plan.wallet_minimum_kes}. Grace until {sub.grace_ends_at}."
            )

    # ──────────────────────────────────────────────────────────────────────────
    #  Grace period enforcement (called from cron)
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def enforce_grace_periods(cls, db: Session) -> int:
        """
        Cron task: promote SUSPENDED tenants to BLOCKED if grace period expired.
        Returns count of tenants blocked.
        """
        now = now_nairobi()
        expired = (
            db.query(TenantSubscription)
            .filter(
                TenantSubscription.status == SubscriptionStatus.SUSPENDED,
                TenantSubscription.grace_ends_at <= now,
            )
            .all()
        )
        blocked = 0
        for sub in expired:
            sub.status = SubscriptionStatus.BLOCKED
            blocked += 1
        if blocked:
            db.commit()
            logger.warning(f"🔴 {blocked} tenants moved to BLOCKED (grace period expired)")
        return blocked

    @classmethod
    def expire_trials(cls, db: Session) -> int:
        """
        Cron task: move TRIAL subscriptions past their trial_ends_at to SUSPENDED.
        """
        now = now_nairobi()
        expired = (
            db.query(TenantSubscription)
            .filter(
                TenantSubscription.status == SubscriptionStatus.TRIAL,
                TenantSubscription.trial_ends_at <= now,
            )
            .all()
        )
        count = 0
        for sub in expired:
            sub.status = SubscriptionStatus.SUSPENDED
            sub.suspended_at = now
            sub.grace_ends_at = now + timedelta(days=GRACE_PERIOD_DAYS)
            count += 1
        if count:
            db.commit()
            logger.info(f"⏰ {count} trials expired → SUSPENDED")
        return count

    # ──────────────────────────────────────────────────────────────────────────
    #  Monthly invoice generation (called from cron on 1st of month)
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def generate_monthly_invoices(cls, db: Session, period_start: datetime, period_end: datetime) -> int:
        """
        Generate PlatformInvoice rows for all ACTIVE subscriptions.
        Reads recon_count + notification_count from TenantSubscription,
        charges subscription fee + usage, resets counters.
        Returns number of invoices generated.
        """
        from app.core.timezone import now_nairobi

        subs = (
            db.query(TenantSubscription)
            .filter(TenantSubscription.status == SubscriptionStatus.ACTIVE)
            .all()
        )

        generated = 0
        for sub in subs:
            # Skip if invoice already exists for this period
            existing = (
                db.query(PlatformInvoice)
                .filter(
                    PlatformInvoice.collection_id == sub.collection_id,
                    PlatformInvoice.period_start == period_start,
                )
                .first()
            )
            if existing:
                continue

            plan = sub.plan
            sub_fee   = Decimal(str(plan.monthly_fee_kes))
            recon_fee = Decimal(str(plan.recon_fee_kes)) * sub.recon_count
            notif_fee = Decimal(str(plan.notification_fee_kes)) * sub.notification_count
            inv_usage_fee = Decimal(str(plan.invoice_fee_kes)) * sub.invoice_count

            # Count active collection points
            from app.modules.ingestion.models import CollectionPoint
            cp_count = db.query(CollectionPoint).filter(
                CollectionPoint.collection_id == sub.collection_id,
                CollectionPoint.is_active == True
            ).count()
            cp_rent_fee = Decimal(str(plan.collection_point_fee_kes)) * cp_count

            total = (sub_fee + recon_fee + notif_fee + inv_usage_fee + cp_rent_fee).quantize(Decimal("0.01"))

            # Generate sequential invoice number: INV-YYYY-MM-XXXXXX
            invoice_seq = db.query(func.count(PlatformInvoice.id)).scalar() + 1
            invoice_number = f"INV-{period_start.year}-{period_start.month:02d}-{invoice_seq:05d}"

            invoice = PlatformInvoice(
                collection_id=sub.collection_id,
                plan_id=plan.id,
                invoice_number=invoice_number,
                period_start=period_start,
                period_end=period_end,
                subscription_fee_kes=sub_fee,
                recon_count=sub.recon_count,
                recon_fee_total_kes=recon_fee,
                notification_count=sub.notification_count,
                notification_fee_total_kes=notif_fee,
                invoice_count=sub.invoice_count,
                invoice_fee_total_kes=inv_usage_fee,
                collection_point_count=cp_count,
                collection_point_fee_total_kes=cp_rent_fee,
                total_amount_kes=total,
                status=InvoiceStatus.SENT,
                sent_at=now_nairobi(),
            )
            db.add(invoice)

            # Attempt wallet deduction for subscription fee
            wallet = db.query(TenantWallet).filter(
                TenantWallet.subscription_id == sub.id
            ).first()
            if wallet and wallet.balance_kes >= sub_fee:
                new_balance = wallet.balance_kes - sub_fee
                wallet.balance_kes = new_balance
                tx = WalletTransaction(
                    wallet_id=wallet.id,
                    collection_id=sub.collection_id,
                    tx_type=WalletTxType.SUBSCRIPTION,
                    event_type=WalletTxEvent.SUBSCRIPTION_FEE,
                    amount_kes=sub_fee,
                    balance_after=new_balance,
                    description=f"Monthly platform fee — {plan.name} plan",
                    reference=invoice_number,
                )
                db.add(tx)

            # Reset monthly usage counters
            sub.recon_count = 0
            sub.notification_count = 0
            sub.invoice_count = 0
            sub.current_period_start = period_end
            # Next period end = first of month after period_end
            next_month = (period_end.replace(day=1) + timedelta(days=32)).replace(day=1)
            sub.current_period_end = next_month

            db.flush()
            generated += 1

        db.commit()
        logger.info(f"📄 Generated {generated} monthly invoices for period {period_start.date()} → {period_end.date()}")
        return generated

    def list_invoices(self, skip: int = 0, limit: int = 24):
        q = (
            self.db.query(PlatformInvoice)
            .filter(PlatformInvoice.collection_id == self.collection_id)
            .order_by(PlatformInvoice.period_start.desc())
        )
        total = q.count()
        items = q.offset(skip).limit(limit).all()
        return total, items

    def get_invoice(self, invoice_id: uuid.UUID) -> PlatformInvoice:
        inv = (
            self.db.query(PlatformInvoice)
            .filter(
                PlatformInvoice.id == invoice_id,
                PlatformInvoice.collection_id == self.collection_id,
            )
            .first()
        )
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return inv

    def list_wallet_transactions(self, skip: int = 0, limit: int = 50):
        wallet = self.get_my_wallet()
        q = (
            self.db.query(WalletTransaction)
            .filter(WalletTransaction.wallet_id == wallet.id)
            .order_by(WalletTransaction.created_at.desc())
        )
        total = q.count()
        items = q.offset(skip).limit(limit).all()
        return total, wallet.balance_kes, items

    # ──────────────────────────────────────────────────────────────────────────
    #  Access guard (used by other modules)
    # ──────────────────────────────────────────────────────────────────────────

    def assert_active(self) -> None:
        """
        Raise 403 if the tenant is BLOCKED (grace expired).
        SUSPENDED tenants get a warning but are not blocked.
        TRIAL tenants pass through freely.
        """
        sub = self._get_subscription()
        if not sub:
            return  # No subscription yet → developer/test environment, allow through

        if sub.status == SubscriptionStatus.BLOCKED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your subscription is blocked due to insufficient wallet balance. "
                    "Please top up your wallet to restore access."
                ),
            )

    # ──────────────────────────────────────────────────────────────────────────
    #  Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get_subscription(self) -> Optional[TenantSubscription]:
        return (
            self.db.query(TenantSubscription)
            .filter(TenantSubscription.collection_id == self.collection_id)
            .first()
        )

    def _get_wallet(self, sub: TenantSubscription) -> Optional[TenantWallet]:
        return (
            self.db.query(TenantWallet)
            .filter(TenantWallet.subscription_id == sub.id)
            .first()
        )

    # ──────────────────────────────────────────────────────────────────────────
    #  Paystack HTTP helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _paystack_post(self, path: str, payload: dict) -> dict:
        url = f"{settings.PAYSTACK_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("status"):
                raise HTTPException(status_code=502, detail=data.get("message", "Paystack error"))
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(f"Paystack POST {path} failed: {exc}")
            raise HTTPException(status_code=502, detail="Payment gateway error. Please try again.")

    def _paystack_get(self, path: str) -> dict:
        url = f"{settings.PAYSTACK_BASE_URL}{path}"
        headers = {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}
        try:
            resp = httpx.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("status"):
                raise HTTPException(status_code=502, detail=data.get("message", "Paystack error"))
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(f"Paystack GET {path} failed: {exc}")
            raise HTTPException(status_code=502, detail="Payment gateway error. Please try again.")
