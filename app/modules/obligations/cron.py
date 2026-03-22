import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session
from app.core.dependancies import SessionLocal
from app.modules.obligations.models import (
    Obligation, RecurringConfig, ObligationStatus, RecurrenceType
)
from app.modules.obligations.models import Payer
from app.rabbitmq.publisher import BasePublisher
from app.rabbitmq.types import EventType, Priority

logger = logging.getLogger(__name__)

publisher = BasePublisher("recurring-billing-cron")

def compute_next_due(config: RecurringConfig, from_date: datetime) -> Optional[datetime]:
    rt = config.recurrence_type

    if rt == RecurrenceType.MONTHLY:
        try:
            from dateutil.relativedelta import relativedelta
            day = config.day_of_month or from_date.day
            next_dt = from_date.replace(day=day)
            if next_dt <= from_date:
                next_dt = (from_date + relativedelta(months=1)).replace(day=day)
            return next_dt
        except ImportError:
            return from_date + timedelta(days=30)

    elif rt == RecurrenceType.WEEKLY:
        dow = config.day_of_week if config.day_of_week is not None else from_date.weekday()
        days_ahead = (dow - from_date.weekday()) % 7 or 7
        return from_date + timedelta(days=days_ahead)

    elif rt == RecurrenceType.CUSTOM:
        if config.interval_days:
            return from_date + timedelta(days=config.interval_days)
        return from_date + timedelta(days=30)

    return None

async def run_billing_cycle():
    """
    Checks for all RecurringConfigs where next_due_date <= now() and auto_generate=True.
    Calculates previous unpaid balances, applies penalties, generates new obligations, 
    and marks the old ones as CANCELLED (Rolled over).
    """
    logger.info("🔄 Running scheduled recurring billing cycle...")
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()
        processed_count = 0
        while True:
            # We fetch 1000 at a time. Because we update `next_due_date` in the loop,
            # processed items naturally drop out of this query on the next iteration.
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
                if arrears > 0 and old_ob.status not in (ObligationStatus.PAID, ObligationStatus.CANCELLED):
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
                    due_date=config.next_due_date or now,
                    is_recurring=True,
                    status=ObligationStatus.PENDING,
                    meta=new_meta,
                    created_by=old_ob.created_by,
                )
                db.add(new_ob)
                db.flush() 
                
                # 3. Mark old obligation as CANCELLED (Rolled Over)
                # Note: We do this so the payer only has ONE active obligation for this recurrence line.
                if old_ob.status != ObligationStatus.PAID:
                    old_ob.status = ObligationStatus.CANCELLED
                    old_meta = dict(old_ob.meta or {})
                    old_meta["rolled_over_to"] = str(new_ob.id)
                    old_ob.meta = old_meta
                
                # 4. Move config to new obligation and update next_due_date
                config.obligation_id = new_ob.id
                config.next_due_date = compute_next_due(config, config.next_due_date or now)
                
                db.commit()
                processed_count += 1
                
                # 5. Publish event for notification worker
                payer = db.query(Payer).filter(Payer.id == new_ob.payer_id).first()
                if payer:
                    await publisher.publish_event(
                        event_type=EventType.OBLIGATION_CREATED,
                        payload={
                            "obligation_id":  str(new_ob.id),
                            "collection_id":  str(new_ob.collection_id),
                            "payer_id":       str(payer.id),
                            "payer_name":     payer.name,
                            "phone":          payer.phone or "",
                            "email":          payer.email or "",
                            "account_no":     new_ob.account_no,
                            "amount_due":     float(new_ob.amount_due),
                            "currency":       new_ob.currency,
                            "due_date":       new_ob.due_date.isoformat() if new_ob.due_date else "",
                            "description":    new_ob.description or "",
                            "is_rollover":    True,
                            "previous_arrears": float(arrears),
                            "penalty":        float(penalty),
                        },
                        priority=Priority.MEDIUM,
                    )

            except Exception as e:
                logger.error(f"Failed to process recurring config {config.id}: {e}")
                db.rollback()

        if processed_count > 0:
            logger.info(f"✅ Generated {processed_count} new recurring obligations")

    except Exception as e:
        logger.error(f"CRITICAL: Failed to run billing cycle: {e}")
    finally:
        db.close()

async def run_reminders_cycle():
    """
    Checks for obligations due today and sends a reminder SMS.
    Ensures we only send one reminder per obligation per day by checking meta.
    """
    logger.info("📢 Running scheduled payment reminders cycle...")
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()
        today_str = now.date().isoformat()
        
        start_of_day = datetime(now.year, now.month, now.day)
        end_of_day = start_of_day + timedelta(days=1)
        
        query = (
            db.query(Obligation)
            .filter(
                Obligation.status.in_([ObligationStatus.PENDING, ObligationStatus.PARTIAL]),
                Obligation.due_date >= start_of_day,
                Obligation.due_date < end_of_day
            )
        )
        
        reminded_count = 0
        offset = 0
        while True:
            due_obs = query.offset(offset).limit(1000).all()
            if not due_obs:
                break
                
            for ob in due_obs:
                meta = dict(ob.meta or {})
                if meta.get("reminder_sent_date") == today_str:
                    continue
                    
                try:
                    payer = db.query(Payer).filter(Payer.id == ob.payer_id).first()
                    if not payer:
                        continue
                        
                    await publisher.publish_event(
                        event_type=EventType.OBLIGATION_DUE,
                        payload={
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
                        },
                        priority=Priority.MEDIUM,
                    )
                    
                    meta["reminder_sent_date"] = today_str
                    ob.meta = meta
                    db.commit()
                    reminded_count += 1
                except Exception as e:
                    logger.error(f"Failed to send reminder for obligation {ob.id}: {e}")
                    db.rollback()
            
            offset += 1000

                
        if reminded_count > 0:
            logger.info(f"✅ Sent {reminded_count} due date reminders")
    except Exception as e:
        logger.error(f"CRITICAL: Failed to run reminders cycle: {e}")
    finally:
        db.close()

async def cron_worker_loop():
    """Background loop that runs the billing cycle daily (or minutely for testing)"""
    # Wait for RabbitMQ to connect first
    await asyncio.sleep(5)
    while True:
        await run_billing_cycle()
        await run_reminders_cycle()
        await asyncio.sleep(60)
