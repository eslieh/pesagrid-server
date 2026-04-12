import logging
import uuid
import json
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.cache import cache
from app.modules.ingestion.models import Transaction, TransactionStatus, CollectionPoint, CollectionPointPSP
from app.modules.obligations.models import Obligation, ObligationStatus
from app.modules.accounts.models import BusinessProfile
from app.core.timezone import now_nairobi

logger = logging.getLogger(__name__)

class DashboardService:
    def __init__(self, db: Session, collection_id: uuid.UUID):
        self.db = db
        self.collection_id = collection_id

    async def get_metrics(
        self, 
        collection_point_id: Optional[uuid.UUID] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> dict:
        cache_key = f"dashboard:metrics:{self.collection_id}:{start_date}:{end_date}"
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
        if start_date:
            trx_query = trx_query.filter(Transaction.ingested_at >= start_date)
        if end_date:
            trx_query = trx_query.filter(Transaction.ingested_at <= end_date)
            
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
        if start_date:
            out_bal_query = out_bal_query.filter(Obligation.created_at >= start_date)
        if end_date:
            out_bal_query = out_bal_query.filter(Obligation.created_at <= end_date)
                
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
        allowed_intervals = ["hour", "day", "week", "month", "year"]
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

    async def get_collection_point_insights(self, cp_id: uuid.UUID) -> dict:
        """
        Full intelligence snapshot for a single collection point.

        Computes:
          - pace / goal progress (when goal_amount + end_date are set)
          - channel breakdown by psp_type (from transactions, cross-referenced against PSP links)
          - compliance flags: transactions above compliance_threshold
          - a single human-readable insight sentence

        All data derived from existing columns — no new DB writes.
        Cached for 60s (shorter than dashboard 300s — used for live review).
        """
        from fastapi import HTTPException

        cache_key = f"dashboard:cp_insights:{self.collection_id}:{cp_id}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.error(f"Cache read error (insights): {e}")

        # ── Fetch the collection point ────────────────────────────────────────
        cp = (
            self.db.query(CollectionPoint)
            .filter(CollectionPoint.id == cp_id, CollectionPoint.collection_id == self.collection_id)
            .first()
        )
        if not cp:
            raise HTTPException(status_code=404, detail="Collection point not found")

        now = now_nairobi()
        
        # ── Determine period boundaries based on goal_period ──────────────────
        from app.modules.ingestion.models import CollectionGoalPeriod
        from datetime import timedelta
        import calendar

        period_start = cp.start_date or cp.created_at
        period_end = cp.end_date

        if cp.goal_period == CollectionGoalPeriod.DAILY:
            period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            period_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif cp.goal_period == CollectionGoalPeriod.WEEKLY:
            # Monday is 0, Sunday is 6
            start_of_week = now - timedelta(days=now.weekday())
            period_start = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_week = start_of_week + timedelta(days=6)
            period_end = end_of_week.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif cp.goal_period == CollectionGoalPeriod.MONTHLY:
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            last_day = calendar.monthrange(now.year, now.month)[1]
            period_end = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
        elif cp.goal_period == CollectionGoalPeriod.YEARLY:
            period_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            period_end = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)

        # ── Total collected at this point (within period) ─────────────────────
        q_collected = self.db.query(func.sum(Transaction.amount)).filter(
            Transaction.collection_point_id == cp.id,
            Transaction.status.in_([
                TransactionStatus.CATEGORIZED, TransactionStatus.MATCHED,
                TransactionStatus.MANUAL
            ]),
            Transaction.ingested_at >= period_start
        )
        if period_end:
            q_collected = q_collected.filter(Transaction.ingested_at <= period_end)
            
        total_collected = float(q_collected.scalar() or 0)

        # ── Pace / goal tracking ──────────────────────────────────────────────
        pace = None
        if cp.goal_amount and float(cp.goal_amount) > 0:
            goal = float(cp.goal_amount)
            progress_pct = round((total_collected / goal) * 100, 2) if goal > 0 else 0

            # Time elapsed bounded to now, preventing negative elapsed
            effective_now = min(now, period_end) if period_end else now
            elapsed_delta = effective_now - period_start
            
            # If period is less than a day, pacing is fractional day
            days_elapsed = max(elapsed_delta.total_seconds() / 86400, 0.01)
            daily_pace_actual = total_collected / days_elapsed

            days_remaining = None
            daily_pace_required = None
            pace_delta_pct = None
            projected_total = None

            if period_end:
                remaining_delta = period_end - now
                days_remaining = max(remaining_delta.total_seconds() / 86400, 0)
                remaining_amount = goal - total_collected
                if days_remaining > 0:
                    daily_pace_required = remaining_amount / days_remaining
                    if daily_pace_required > 0:
                        pace_delta_pct = round(
                            ((daily_pace_actual - daily_pace_required) / daily_pace_required) * 100, 2
                        )
                total_days = days_elapsed + days_remaining
                projected_total = round(daily_pace_actual * total_days, 2)

            # Cap values for display safety
            days_elapsed_disp = max(round(days_elapsed), 1)
            days_remaining_disp = max(round(days_remaining), 0) if days_remaining is not None else None

            pace = {
                "total_collected":      total_collected,
                "goal_amount":          goal,
                "progress_pct":         progress_pct,
                "days_elapsed":         days_elapsed_disp,
                "days_remaining":       days_remaining_disp,
                "daily_pace_actual":    round(daily_pace_actual, 2),
                "daily_pace_required":  round(daily_pace_required, 2) if daily_pace_required is not None else None,
                "pace_delta_pct":       pace_delta_pct,
                "projected_total":      projected_total,
            }

        # ── Channel breakdown by psp_type ─────────────────────────────────────
        channel_rows = (
            self.db.query(
                Transaction.psp_type,
                func.sum(Transaction.amount).label("total"),
                func.count(Transaction.id).label("count"),
            )
            .filter(Transaction.collection_point_id == cp.id)
            .group_by(Transaction.psp_type)
            .all()
        )
        channels = []
        if channel_rows and total_collected > 0:
            for row in channel_rows:
                channels.append({
                    "psp_type": row.psp_type,
                    "total":    float(row.total or 0),
                    "count":    row.count,
                    "pct":      round((float(row.total or 0) / total_collected) * 100, 2),
                })
            channels.sort(key=lambda x: x["total"], reverse=True)

        # ── Compliance flags ──────────────────────────────────────────────────
        compliance = []
        if cp.compliance_threshold:
            threshold = float(cp.compliance_threshold)
            flagged = (
                self.db.query(Transaction)
                .filter(
                    Transaction.collection_point_id == cp.id,
                    Transaction.amount >= threshold,
                )
                .order_by(Transaction.ingested_at.desc())
                .limit(50)
                .all()
            )
            compliance = [
                {
                    "id":          str(txn.id),
                    "amount":      float(txn.amount),
                    "payer_name":  txn.payer_name,
                    "phone":       txn.phone,
                    "psp_ref":     txn.psp_ref,
                    "ingested_at": txn.ingested_at.isoformat(),
                }
                for txn in flagged
            ]

        # ── Insight sentence ──────────────────────────────────────────────────
        if pace:
            delta = pace["pace_delta_pct"]
            proj  = pace["projected_total"]
            goal  = pace["goal_amount"]
            pct   = pace["progress_pct"]
            days_r = pace["days_remaining"]
            if delta is not None:
                direction = "ahead of" if delta >= 0 else "behind"
                abs_delta = abs(round(delta, 1))
                if proj is not None:
                    proj_pct = round((proj / goal) * 100, 1)
                    insight_text = (
                        f"You're {abs_delta}% {direction} your daily pace. "
                        f"At this trajectory you'll finish at ~{proj_pct}% of your goal"
                        f"{f' in {days_r} days' if days_r else ''}."
                    )
                else:
                    insight_text = (
                        f"You're {abs_delta}% {direction} your daily pace. "
                        f"You've collected {pct}% of your goal so far."
                    )
            else:
                insight_text = (
                    f"You've collected KES {total_collected:,.0f} — {pct}% of your goal."
                )
        elif compliance:
            insight_text = (
                f"{len(compliance)} transaction(s) above your compliance threshold "
                f"of KES {float(cp.compliance_threshold):,.0f} require manual review."
            )
        elif channels:
            top = channels[0]
            insight_text = (
                f"KES {total_collected:,.0f} collected. "
                f"{top['pct']}% came via {top['psp_type']}."
            )
        else:
            insight_text = f"KES {total_collected:,.0f} collected at this point."

        result = {
            "cp_id":        str(cp.id),
            "cp_name":      cp.name,
            "cp_type":      cp.cp_type.value,
            "pace":         pace,
            "channels":     channels,
            "compliance":   compliance,
            "insight_text": insight_text,
        }

        if cache.client:
            try:
                await cache.client.setex(cache_key, 60, json.dumps(result))
            except Exception as e:
                logger.error(f"Cache write error (insights): {e}")

        return result

    async def global_search(self, query: str) -> List[dict]:
        """
        Search across Payers, Obligations (Invoices), and Transactions.
        Returns a unified list of SearchResult-compatible dicts.
        """
        if not query or len(query) < 2:
            return []

        search_results = []
        q_term = f"%{query}%"

        # 1. Search Payers (name, phone, account_no)
        from app.modules.obligations.models import Payer
        payers = (
            self.db.query(Payer)
            .filter(
                Payer.collection_id == self.collection_id,
                (Payer.name.ilike(q_term)) | (Payer.phone.ilike(q_term)) | (Payer.account_no.ilike(q_term))
            )
            .limit(10)
            .all()
        )
        for p in payers:
            # Find last payment for this payer
            last_trx = (
                self.db.query(Transaction)
                .filter(Transaction.collection_id == self.collection_id, Transaction.account_no == p.account_no)
                .order_by(Transaction.ingested_at.desc())
                .first()
            )
            
            search_results.append({
                "type": "payer",
                "title": p.name,
                "subtitle": p.phone or p.account_no,
                "identifier": p.phone or p.account_no or "No Ref",
                "avatar_text": p.name[0].upper() if p.name else "P",
                "link_id": p.id,
                "meta": {
                    "status": "Active" if p.is_active else "Inactive",
                    "last_payment_date": last_trx.ingested_at.isoformat() if last_trx else None
                }
            })

        # 2. Search Obligations (Invoices) by account_no or description
        from app.modules.obligations.models import Obligation
        obs = (
            self.db.query(Obligation)
            .filter(
                Obligation.collection_id == self.collection_id,
                (Obligation.account_no.ilike(q_term)) | (Obligation.description.ilike(q_term))
            )
            .limit(10)
            .all()
        )
        for ob in obs:
            search_results.append({
                "type": "invoice",
                "title": ob.description or f"Invoice {ob.account_no}",
                "subtitle": f"Account: {ob.account_no}",
                "identifier": ob.account_no,
                "avatar_text": "INV",
                "link_id": ob.id,
                "meta": {
                    "balance": float(ob.balance),
                    "status": ob.status.value
                }
            })

        # 3. Search Transactions (psp_ref, phone)
        trxs = (
            self.db.query(Transaction)
            .filter(
                Transaction.collection_id == self.collection_id,
                (Transaction.psp_ref.ilike(q_term)) | (Transaction.phone.ilike(q_term))
            )
            .limit(10)
            .all()
        )
        for t in trxs:
            search_results.append({
                "type": "transaction",
                "title": f"KES {t.amount:,.0f} from {t.payer_name or t.phone}",
                "subtitle": f"Ref: {t.psp_ref}",
                "identifier": t.psp_ref or t.phone,
                "avatar_text": "TRX",
                "link_id": t.id,
                "meta": {
                    "status": t.status.value,
                    "last_payment_date": t.ingested_at.isoformat()
                }
            })

        return search_results
