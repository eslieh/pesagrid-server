"""
Billing cron jobs — monthly invoice generation, trial expiry, grace period enforcement.

Scheduled via APScheduler (same instance as obligations/cron.py).
"""
import asyncio
import logging
from datetime import datetime

from app.core.dependancies import SessionLocal
from app.core.timezone import now_nairobi
from app.modules.billing.services import BillingService
from app.rabbitmq.publisher import BasePublisher
from app.rabbitmq.types import EventType, Priority

logger = logging.getLogger(__name__)
publisher = BasePublisher("billing-cron")


async def run_monthly_invoice_generation():
    """
    Called on the 1st of each month at 00:05.
    Generates PlatformInvoice rows for all ACTIVE subscriptions for the prior month.
    """
    logger.info("📊 Running monthly invoice generation...")
    now = now_nairobi()

    # Period = last calendar month
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    last_month_end = first_of_this_month - timedelta(seconds=1)
    last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    db = SessionLocal()
    try:
        count = await asyncio.to_thread(
            BillingService.generate_monthly_invoices,
            db,
            last_month_start,
            first_of_this_month,
        )
        logger.info(f"✅ Monthly invoices: {count} generated")
        # Publish event so email dispatcher can send them
        await publisher.publish_event(
            EventType.BILLING_INVOICE_GENERATED,
            {"period_start": last_month_start.isoformat(), "count": count},
            Priority.HIGH,
        )
    except Exception as exc:
        logger.error(f"Monthly invoice generation failed: {exc}")
    finally:
        db.close()


async def run_trial_expiry():
    """Daily at 01:00 — move expired TRIAL subscriptions to SUSPENDED."""
    db = SessionLocal()
    try:
        count = await asyncio.to_thread(BillingService.expire_trials, db)
        if count:
            logger.info(f"⏰ Expired {count} trials → SUSPENDED")
    except Exception as exc:
        logger.error(f"Trial expiry job failed: {exc}")
    finally:
        db.close()


async def run_grace_period_enforcement():
    """Daily at 01:30 — move SUSPENDED tenants with expired grace periods to BLOCKED."""
    db = SessionLocal()
    try:
        count = await asyncio.to_thread(BillingService.enforce_grace_periods, db)
        if count:
            logger.warning(f"🔴 {count} tenants blocked (grace expired)")
    except Exception as exc:
        logger.error(f"Grace period enforcement failed: {exc}")
    finally:
        db.close()


def setup_billing_cron_jobs(scheduler):
    """Register billing jobs with the shared APScheduler instance."""
    # 1st of each month at 00:05
    scheduler.add_job(
        run_monthly_invoice_generation,
        "cron", day=1, hour=0, minute=5,
        id="billing_monthly_invoices",
        replace_existing=True,
    )
    # Daily trial expiry check at 01:00
    scheduler.add_job(
        run_trial_expiry,
        "cron", hour=1, minute=0,
        id="billing_trial_expiry",
        replace_existing=True,
    )
    # Daily grace period enforcement at 01:30
    scheduler.add_job(
        run_grace_period_enforcement,
        "cron", hour=1, minute=30,
        id="billing_grace_enforcement",
        replace_existing=True,
    )
    logger.info("⏰ Billing cron jobs registered: monthly invoices, trial expiry, grace enforcement")
