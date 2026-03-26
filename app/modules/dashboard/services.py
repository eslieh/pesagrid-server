import logging
import uuid
import json
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.cache import cache
from app.modules.ingestion.models import Transaction, TransactionStatus, CollectionPoint
from app.modules.obligations.models import Obligation, ObligationStatus
from app.modules.accounts.models import BusinessProfile

logger = logging.getLogger(__name__)

class DashboardService:
    def __init__(self, db: Session, collection_id: uuid.UUID):
        self.db = db
        self.collection_id = collection_id

    async def get_metrics(self, collection_point_id: Optional[uuid.UUID] = None) -> dict:
        cache_key = f"dashboard:metrics:{self.collection_id}"
        if collection_point_id:
            cache_key += f":cp:{collection_point_id}"
            
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.error(f"Cache read error: {e}")

        # Transaction breakdown (Matched vs Unmatched/Manual)
        trx_query = (
            self.db.query(Transaction.status, func.sum(Transaction.amount))
            .filter(
                Transaction.collection_id == self.collection_id,
                Transaction.status.in_([TransactionStatus.MATCHED, TransactionStatus.UNMATCHED, TransactionStatus.MANUAL, TransactionStatus.CATEGORIZED])
            )
        )
        
        if collection_point_id:
            trx_query = trx_query.filter(Transaction.collection_point_id == collection_point_id)
            
        trx_stats = trx_query.group_by(Transaction.status).all()
        
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
        out_bal_query = (
            self.db.query(func.sum(Obligation.balance))
            .filter(
                Obligation.collection_id == self.collection_id,
                Obligation.status.in_([ObligationStatus.PENDING, ObligationStatus.PARTIAL, ObligationStatus.OVERDUE])
            )
        )
        
        if collection_point_id:
            cp = self.db.query(CollectionPoint).filter(CollectionPoint.id == collection_point_id).first()
            if cp:
                out_bal_query = out_bal_query.filter(Obligation.account_no == cp.account_no)
            else:
                out_bal_query = out_bal_query.filter(False)
                
        out_bal = out_bal_query.scalar()
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

    async def get_payments_by_account(
        self, 
        start_date: Optional[datetime] = None, 
        end_date: Optional[datetime] = None, 
        skip: int = 0, 
        limit: int = 50
    ) -> List[dict]:
        # Include dates in cache key if provided
        cache_key = f"dashboard:payments_acc:{self.collection_id}:{start_date}:{end_date}:{skip}:{limit}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.error(f"Cache read error: {e}")

        query = (
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
        )
        
        if start_date:
            query = query.filter(Transaction.ingested_at >= start_date)
        if end_date:
            query = query.filter(Transaction.ingested_at <= end_date)
            
        rows = (
            query
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

    async def get_payment_history(self, skip: int = 0, limit: int = 50, collection_point_id: Optional[uuid.UUID] = None) -> Tuple[int, List[Transaction]]:
        q = self.db.query(Transaction).filter(Transaction.collection_id == self.collection_id)
        if collection_point_id:
            q = q.filter(Transaction.collection_point_id == collection_point_id)
            
        total = q.count()
        items = q.order_by(Transaction.ingested_at.desc()).offset(skip).limit(limit).all()
        return total, items

    async def get_collection_trends(
        self,
        interval: str = "day",  # day, week, month, year
        collection_point_id: Optional[uuid.UUID] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> dict:
        """
        Aggregates total collections and transaction counts over time.
        """
        cache_key = f"dashboard:trends:{self.collection_id}:{interval}:{collection_point_id}:{start_date}:{end_date}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.error(f"Cache read error: {e}")

        # interval must be valid for DATE_TRUNC
        allowed_intervals = ["day", "week", "month", "year"]
        if interval not in allowed_intervals:
            interval = "day"

        query = (
            self.db.query(
                func.date_trunc(interval, Transaction.ingested_at).label("period"),
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("count")
            )
            .filter(
                Transaction.collection_id == self.collection_id,
                Transaction.status.in_([TransactionStatus.MATCHED, TransactionStatus.UNMATCHED, TransactionStatus.MANUAL, TransactionStatus.CATEGORIZED])
            )
        )

        if collection_point_id:
            query = query.filter(Transaction.collection_point_id == collection_point_id)
        
        if start_date:
            query = query.filter(Transaction.ingested_at >= start_date)
        if end_date:
            query = query.filter(Transaction.ingested_at <= end_date)

        rows = (
            query
            .group_by(func.date_trunc(interval, Transaction.ingested_at))
            .order_by(func.date_trunc(interval, Transaction.ingested_at))
            .all()
        )

        result = {
            "interval": interval,
            "trends": [
                {
                    "period": r.period.isoformat(),
                    "total": float(r.total or 0),
                    "count": r.count
                }
                for r in rows
            ]
        }

        if cache.client:
            try:
                await cache.client.setex(cache_key, 300, json.dumps(result))
            except Exception as e:
                logger.error(f"Cache write error: {e}")

        return result

    async def get_peak_collection_times(
        self,
        collection_point_id: Optional[uuid.UUID] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[dict]:
        """
        Aggregates collections by hour of the day to identify peak times.
        """
        cache_key = f"dashboard:peaks:{self.collection_id}:{collection_point_id}:{start_date}:{end_date}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.error(f"Cache read error: {e}")

        query = (
            self.db.query(
                func.extract("hour", Transaction.ingested_at).label("hour"),
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("count")
            )
            .filter(
                Transaction.collection_id == self.collection_id,
                Transaction.status.in_([TransactionStatus.MATCHED, TransactionStatus.UNMATCHED, TransactionStatus.MANUAL])
            )
        )

        if collection_point_id:
            query = query.filter(Transaction.collection_point_id == collection_point_id)
        
        if start_date:
            query = query.filter(Transaction.ingested_at >= start_date)
        if end_date:
            query = query.filter(Transaction.ingested_at <= end_date)

        rows = (
            query
            .group_by(func.extract("hour", Transaction.ingested_at))
            .order_by(func.extract("hour", Transaction.ingested_at))
            .all()
        )

        result = [
            {
                "hour": int(r.hour),
                "total": float(r.total or 0),
                "count": r.count
            }
            for r in rows
        ]

        if cache.client:
            try:
                await cache.client.setex(cache_key, 300, json.dumps(result))
            except Exception as e:
                logger.error(f"Cache write error: {e}")

        return result

    async def get_collection_point_metrics(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[dict]:
        """
        Specialized analytics for flexible bucket tracking (Buses, Branches, etc).
        Returns totals per CollectionPoint within the given period.
        """
        query = (
            self.db.query(
                CollectionPoint.id,
                CollectionPoint.name,
                CollectionPoint.account_no,
                func.sum(Transaction.amount).label("total_collected"),
                func.count(Transaction.id).label("transaction_count")
            )
            .join(Transaction, Transaction.collection_point_id == CollectionPoint.id)
            .filter(CollectionPoint.collection_id == self.collection_id)
        )

        if start_date:
            query = query.filter(Transaction.ingested_at >= start_date)
        if end_date:
            query = query.filter(Transaction.ingested_at <= end_date)

        rows = query.group_by(CollectionPoint.id, CollectionPoint.name, CollectionPoint.account_no).order_by(func.sum(Transaction.amount).desc()).all()

        return [
            {
                "id": str(r.id),
                "name": r.name,
                "account_no": r.account_no,
                "total_collected": float(r.total_collected or 0),
                "transaction_count": r.transaction_count
            }
            for r in rows
        ]

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
