import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.dependancies import SessionLocal
from app.modules.obligations.models import (
    Obligation, RecurringConfig, ObligationStatus, RecurrenceType
)
from app.modules.obligations.models import Payer
from app.modules.obligations.logic import compute_next_due, apply_credit_to_obligation
from app.rabbitmq.publisher import BasePublisher
from app.rabbitmq.types import EventType, Priority
from app.core.timezone import now_nairobi, make_aware

logger = logging.getLogger(__name__)

publisher = BasePublisher("recurring-billing-cron")
scheduler = AsyncIOScheduler()


def _run_billing_cycle_sync():
    """Synchronous core of the billing cycle."""
    logger.info("🔄 Running scheduled recurring billing cycle...")
    db: Session = SessionLocal()
    events_to_publish = []
    processed_count = 0
    
    try:
        now = now_nairobi()
        while True:
            due_configs = (
                db.query(RecurringConfig)
                .filter(
                    RecurringConfig.next_due_date <= now,
                    RecurringConfig.auto_generate.is_(True)
                )
                .limit(1000)
                .all()
            )
            
            if not due_configs:
                break
                
            for config in due_configs:
                try:
                    old_ob = config.obligation
                    if not old_ob:
                        continue
                    
                    # 1. Calculate financial rollover
                    base_amount = Decimal(str(old_ob.meta.get("base_amount", old_ob.amount_due)))
                    arrears = Decimal(str(old_ob.balance))
                    penalty_rate = Decimal(str(old_ob.meta.get("penalty_rate", "0")))
                    
                    penalty = Decimal("0")
                    if arrears > 0 and old_ob.status not in (ObligationStatus.SETTLED, ObligationStatus.VOIDED, ObligationStatus.ROLLED):
                        penalty = arrears * penalty_rate
                    
                    new_amount_due = base_amount + arrears + penalty
                    
                    # 2. Setup the new obligation
                    new_meta = dict(old_ob.meta or {})
                    new_meta["base_amount"] = float(base_amount)
                    new_meta["rolled_over_from"] = str(old_ob.id)
                    if penalty > 0:
                        new_meta["penalty_applied"] = float(penalty)
                        new_meta["arrears_carried_forward"] = float(arrears)
                    
                    new_ob = Obligation(
                        collection_id=old_ob.collection_id,
                        payer_id=old_ob.payer_id,
                        account_no=old_ob.account_no,
                        description=old_ob.description, 
                        amount_due=new_amount_due,
                        amount_paid=0,
                        balance=new_amount_due,
                        currency=old_ob.currency,
                        due_date=make_aware(config.next_due_date) if config.next_due_date else now,
                        is_recurring=True,
                        status=ObligationStatus.PENDING,
                        meta=new_meta,
                        created_by=old_ob.created_by,
                    )
                    db.add(new_ob)
                    db.flush() 
                    
                    # 3. Mark old obligation as ROLLED (Rolled Over)
                    if old_ob.status != ObligationStatus.SETTLED:
                        old_ob.status = ObligationStatus.ROLLED
                        old_ob.status_reason = "Automatically rolled to new cycle"
                        old_meta = dict(old_ob.meta or {})
                        old_meta["rolled_over_to"] = str(new_ob.id)
                        old_ob.meta = old_meta
                    
                    # 4. Move config to new obligation and update next_due_date
                    config.obligation_id = new_ob.id
                    base_due = make_aware(config.next_due_date) if config.next_due_date else now
                    config.next_due_date = compute_next_due(
                        config.recurrence_type, 
                        base_due, 
                        config.day_of_month, 
                        config.day_of_week, 
                        config.interval_days
                    )
                    
                    # 5. Apply any existing credit balance to the new obligation
                    payer = db.query(Payer).filter(Payer.id == new_ob.payer_id).first()
                    credit_used = Decimal("0")
                    if payer:
                        credit_used = apply_credit_to_obligation(payer, new_ob)
                    
                    db.commit()
                    processed_count += 1
                    
                    # 6. Prepare event for publication
                    config_next_due = make_aware(config.next_due_date) if config.next_due_date else None
                    if payer and (config_next_due is None or config_next_due > now):

                        events_to_publish.append({
                            "event_type": EventType.OBLIGATION_CREATED,
                            "payload": {
                                "obligation_id":  str(new_ob.id),
                                "collection_id":  str(new_ob.collection_id),
                                "payer_id":       str(payer.id),
                                "payer_name":     payer.name,
                                "phone":          payer.phone or "",
                                "email":          payer.email or "",
                                "account_no":     new_ob.account_no,
                                "amount_due":     float(new_ob.amount_due),
                                "amount_paid":    float(new_ob.amount_paid),
                                "balance":        float(new_ob.balance),
                                "status":         new_ob.status.value,
                                "currency":       new_ob.currency,
                                "due_date":       new_ob.due_date.isoformat() if new_ob.due_date else "",
                                "description":    new_ob.description or "",
                                "is_rollover":    True,
                                "previous_arrears": float(arrears),
                                "penalty":        float(penalty),
                                "credit_used":    float(credit_used),
                            }
                        })

                except Exception as e:
                    logger.error(f"Failed to process recurring config {config.id}: {e}")
                    db.rollback()

        if processed_count > 0:
            logger.info(f"✅ Generated {processed_count} new recurring obligations")
            
    except Exception as e:
        logger.error(f"CRITICAL: Failed to run billing cycle sync: {e}")
    finally:
        db.close()
    
    return events_to_publish

def _run_reminders_cycle_sync():
    """Synchronous core of the reminders cycle."""
    logger.info("📢 Running scheduled payment reminders cycle...")
    db: Session = SessionLocal()
    events_to_publish = []
    reminded_count = 0
    
    try:
        now = now_nairobi()
        today_str = now.date().isoformat()
        
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        target_upcoming = start_of_day + timedelta(days=3)
        target_today    = start_of_day
        target_overdue  = start_of_day - timedelta(days=2)
        
        from sqlalchemy import or_, and_
        query = (
            db.query(Obligation)
            .filter(
                Obligation.status.in_([ObligationStatus.PENDING, ObligationStatus.PARTIAL]),
                Obligation.due_date.isnot(None),
                or_(
                    and_(Obligation.due_date >= target_upcoming, Obligation.due_date < target_upcoming + timedelta(days=1)),
                    and_(Obligation.due_date >= target_today,    Obligation.due_date < target_today + timedelta(days=1)),
                    and_(Obligation.due_date >= target_overdue,  Obligation.due_date < target_overdue + timedelta(days=1))
                )
            )
        )
        
        offset = 0
        while True:
            due_obs = query.offset(offset).limit(1000).all()
            if not due_obs:
                break
                
            for ob in due_obs:
                meta = dict(ob.meta or {})
                if meta.get("reminder_sent_date") == today_str:
                    continue
                    
                # Determine reminder type based on due date
                ob_due_aware = make_aware(ob.due_date)
                ob_date = ob_due_aware.replace(hour=0, minute=0, second=0, microsecond=0)
                if ob_date == target_upcoming:
                    rem_type = "upcoming"
                elif ob_date == target_today:
                    rem_type = "due_today"
                elif ob_date == target_overdue:
                    rem_type = "overdue"
                else:
                    continue
                    
                try:
                    payer = db.query(Payer).filter(Payer.id == ob.payer_id).first()
                    if not payer:
                        continue
                        
                    events_to_publish.append({
                        "event_type": EventType.OBLIGATION_DUE,
                        "payload": {
                            "obligation_id":  str(ob.id),
                            "collection_id":  str(ob.collection_id),
                            "payer_id":       str(payer.id),
                            "payer_name":     payer.name,
                            "phone":          payer.phone or "",
                            "email":          payer.email or "",
                            "account_no":     ob.account_no,
                            "amount_due":     float(ob.amount_due),
                            "amount_paid":    float(ob.amount_paid),
                            "balance":        float(ob.balance),
                            "currency":       ob.currency,
                            "due_date":       ob.due_date.isoformat(),
                            "description":    ob.description or "",
                            "reminder_type":  rem_type,
                        }
                    })
                    
                    meta["reminder_sent_date"] = today_str
                    ob.meta = meta
                    db.commit()
                    reminded_count += 1
                except Exception as e:
                    logger.error(f"Failed to send reminder for obligation {ob.id}: {e}")
                    db.rollback()
            
            offset += 1000

        if reminded_count > 0:
            logger.info(f"✅ Prepared {reminded_count} due date reminders")
            
    except Exception as e:
        logger.error(f"CRITICAL: Failed to run reminders cycle sync: {e}")
    finally:
        db.close()
        
    return events_to_publish

async def run_billing_cycle():
    """Async wrapper for the billing cycle."""
    events = await asyncio.to_thread(_run_billing_cycle_sync)
    for event in events:
        await publisher.publish_event(
            event_type=event["event_type"],
            payload=event["payload"],
            priority=Priority.MEDIUM,
        )

async def run_reminders_cycle():
    """Async wrapper for the reminders cycle."""
    events = await asyncio.to_thread(_run_reminders_cycle_sync)
    for event in events:
        await publisher.publish_event(
            event_type=event["event_type"],
            payload=event["payload"],
            priority=Priority.MEDIUM,
        )

def _refresh_ledger_summary_sync():
    """
    Refresh the obligations.ledger_summary materialized view CONCURRENTLY.
    Called every 5 minutes by the cron scheduler so the tracker board never
    reflects data that is more than 5 minutes stale.
    CONCURRENT refresh means read queries are never blocked.
    """
    db: Session = SessionLocal()
    try:
        db.execute(text(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY obligations.ledger_summary"
        ))
        db.commit()
        logger.info("🔄 [cron] Materialized view obligations.ledger_summary refreshed")
    except Exception as e:
        logger.warning(f"[cron] Ledger summary refresh failed: {e}")
    finally:
        db.close()


async def refresh_ledger_summary_view():
    """Async wrapper so APScheduler can call the sync DB work off-thread."""
    await asyncio.to_thread(_refresh_ledger_summary_sync)


def setup_cron_jobs():
    """Configure APScheduler jobs."""
    # Run billing cycle every 5 hours (as requested by user in previous conversation)
    # The user said standardizing to 5 hours in conversation 7cd542dd
    scheduler.add_job(run_billing_cycle, 'interval', hours=5, id='billing_cycle')

    # Run reminders once a day at 8 AM
    scheduler.add_job(run_reminders_cycle, 'cron', hour=8, minute=0, id='reminders_cycle')

    # Refresh the ledger_summary materialized view every 5 minutes
    scheduler.add_job(refresh_ledger_summary_view, 'interval', minutes=5, id='ledger_summary_refresh')

    logger.info("⏰ Cron jobs scheduled: Billing (5h), Reminders (Daily 08:00), Ledger Refresh (5min)")
