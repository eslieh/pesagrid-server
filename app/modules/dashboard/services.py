import logging
import uuid
import json
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.cache import cache
from app.modules.ingestion.models import Transaction, TransactionStatus
from app.modules.obligations.models import Obligation, ObligationStatus
from app.modules.accounts.models import BusinessProfile

logger = logging.getLogger(__name__)

class DashboardService:
    def __init__(self, db: Session, collection_id: uuid.UUID):
        self.db = db
        self.collection_id = collection_id

    async def get_metrics(self) -> dict:
        cache_key = f"dashboard:metrics:{self.collection_id}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.error(f"Cache read error: {e}")

        # Transaction breakdown (Matched vs Unmatched/Manual)
        trx_stats = (
            self.db.query(Transaction.status, func.sum(Transaction.amount))
            .filter(
                Transaction.collection_id == self.collection_id,
                Transaction.status.in_([TransactionStatus.MATCHED, TransactionStatus.UNMATCHED, TransactionStatus.MANUAL])
            )
            .group_by(Transaction.status)
            .all()
        )
        
        total_matched = 0.0
        total_unmatched = 0.0
        for status, amt in trx_stats:
            val = float(amt or 0)
            if status == TransactionStatus.UNMATCHED:
                total_unmatched += val
            else:
                total_matched += val
                
        total_collected = total_matched + total_unmatched

        # Outstanding Balances
        out_bal = (
            self.db.query(func.sum(Obligation.balance))
            .filter(
                Obligation.collection_id == self.collection_id,
                Obligation.status.in_([ObligationStatus.PENDING, ObligationStatus.PARTIAL, ObligationStatus.OVERDUE])
            )
            .scalar()
        )
        outstanding_balances = float(out_bal or 0.0)

        result = {
            "total_collected": total_collected,
            "total_matched": total_matched,
            "total_unmatched": total_unmatched,
            "outstanding_balances": outstanding_balances
        }

        if cache.client:
            try:
                await cache.client.setex(cache_key, 300, json.dumps(result))
            except Exception as e:
                logger.error(f"Cache write error: {e}")

        return result

    async def get_payments_by_account(self, skip: int = 0, limit: int = 50) -> List[dict]:
        cache_key = f"dashboard:payments_acc:{self.collection_id}:{skip}:{limit}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.error(f"Cache read error: {e}")

        rows = (
            self.db.query(
                Transaction.account_no,
                func.max(Transaction.payer_name).label("payer_name"),
                func.sum(Transaction.amount).label("total_paid"),
                func.max(Transaction.ingested_at).label("last_payment_date")
            )
            .filter(
                Transaction.collection_id == self.collection_id,
                Transaction.account_no.isnot(None),
                Transaction.account_no != ""
            )
            .group_by(Transaction.account_no)
            .order_by(func.max(Transaction.ingested_at).desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        result = [
            {
                "account_no": r.account_no,
                "payer_name": r.payer_name,
                "total_paid": float(r.total_paid or 0),
                "last_payment_date": r.last_payment_date.isoformat() if r.last_payment_date else None
            }
            for r in rows
        ]

        if cache.client:
            try:
                await cache.client.setex(cache_key, 300, json.dumps(result))
            except Exception as e:
                logger.error(f"Cache write error: {e}")

        return result

    async def get_payment_history(self, skip: int = 0, limit: int = 50) -> Tuple[int, List[Transaction]]:
        # History is dynamic, maybe shorter cache or no cache. We'll cache for 60s.
        cache_key = f"dashboard:payment_history:{self.collection_id}:{skip}:{limit}"
        if cache.client:
            try:
                cached_data = await cache.client.get(cache_key)
                if cached_data:
                    parsed = json.loads(cached_data)
                    return parsed["total"], parsed["items"] # Note: this won't be sqlalchemy objects, we need to return dicts if cached.
            except Exception as e:
                pass
        
        q = self.db.query(Transaction).filter(Transaction.collection_id == self.collection_id)
        total = q.count()
        items = q.order_by(Transaction.ingested_at.desc()).offset(skip).limit(limit).all()
        
        # We can't easily cache SQLAlchemy models without serialization overhead, 
        # so for this method we will just return the objects and NOT cache it, 
        # since it's an admin list page that needs recent data anyway.
        return total, items

    def update_notification_prefs(self, prefs_data: dict) -> dict:
        profile = self.db.query(BusinessProfile).filter(BusinessProfile.collection_id == self.collection_id).first()
        if not profile:
            raise ValueError("Business profile not found")
        
        meta = profile.meta or {}
        meta["payment_notifications_enabled"] = prefs_data.get("payment_notifications_enabled", False)
        meta["payment_notification_channels"] = prefs_data.get("payment_notification_channels", ["email"])
        
        profile.meta = meta
        self.db.commit()
        return {
            "payment_notifications_enabled": meta["payment_notifications_enabled"],
            "payment_notification_channels": meta["payment_notification_channels"]
        }
