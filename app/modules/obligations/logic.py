import logging
import calendar
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from app.modules.obligations.models import Obligation, Payer, ObligationStatus, RecurrenceType

logger = logging.getLogger(__name__)

def compute_next_due(
    rt: RecurrenceType,
    from_date: datetime,
    day_of_month: Optional[int] = None,
    day_of_week: Optional[int] = None,
    interval_days: Optional[int] = None,
) -> Optional[datetime]:
    """
    Pure logic function to compute the next due date for a recurring cycle.
    Used by both ObligationService and the Background Billing Cron.
    """
    if rt == RecurrenceType.MONTHLY:
        day = day_of_month or from_date.day
        
        _, max_days_curr = calendar.monthrange(from_date.year, from_date.month)
        next_dt = from_date.replace(day=min(day, max_days_curr))
        
        if next_dt <= from_date:
            month = from_date.month + 1
            year = from_date.year
            if month > 12:
                month = 1
                year += 1
            _, max_days_next = calendar.monthrange(year, month)
            next_dt = from_date.replace(year=year, month=month, day=min(day, max_days_next))
            
        return next_dt

    elif rt == RecurrenceType.WEEKLY:
        target_dow = day_of_week if day_of_week is not None else from_date.weekday()
        days_ahead = (target_dow - from_date.weekday()) % 7 or 7
        return from_date + timedelta(days=days_ahead)

    elif rt == RecurrenceType.CUSTOM:
        return from_date + timedelta(days=interval_days or 30)

    elif rt == RecurrenceType.TERM:
        # Term billing managed externally or by specific scheduler triggers
        return None

    return None


def apply_credit_to_obligation(payer: Payer, obligation: Obligation) -> Decimal:
    """
    Consume any existing payer credit to settle or partially pay a new obligation.
    Mutates payer and obligation in-place (assumes they are attached to a DB session).
    
    Returns: The Decimal amount of credit consumed.
    """
    credit = Decimal(str(payer.credit_balance or 0))
    if credit <= 0:
        return Decimal("0")

    needed = Decimal(str(obligation.balance))
    usage = min(credit, needed)

    obligation.amount_paid = Decimal(str(obligation.amount_paid or 0)) + usage
    obligation.balance = Decimal(str(obligation.amount_due)) - Decimal(str(obligation.amount_paid))
    payer.credit_balance = credit - usage

    if obligation.balance <= 0:
        obligation.status = ObligationStatus.SETTLED
        obligation.balance = Decimal("0")
    elif usage > 0:
        obligation.status = ObligationStatus.PARTIAL

    if usage > 0:
        logger.info(f"💳 Applied {usage} credit from Payer {payer.id} to Obligation {obligation.id}")
    
    return usage
