from datetime import datetime, timedelta, date
from collections import defaultdict
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, text
import uuid
import logging
import json
from decimal import Decimal

from app.core.cache import cache
from app.modules.obligations.models import (
    Obligation, ObligationStatus,
    Payer, PayerGroup,
    RecurringConfig, RecurrenceType,
    NotificationTemplate, TemplateType, TemplateChannel,
)
from app.modules.obligations.schema import (
    ObligationCreate, ObligationUpdate,
    PayerCreate, PayerUpdate,
    PayerGroupCreate, PayerGroupUpdate,
    RecurringConfigUpdate,
    NotificationTemplateCreate, NotificationTemplateUpdate,
    UnifiedPayerObligationCreate
)
from app.rabbitmq import BasePublisher, EventType, Priority
from app.core.timezone import now_nairobi

logger = logging.getLogger(__name__)


class ObligationService:
    def __init__(self, db: Session, collection_id: uuid.UUID, current_user_id: uuid.UUID):
        self.db = db
        self.collection_id = collection_id
        self.current_user_id = current_user_id

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _get_obligation_or_404(self, obligation_id: uuid.UUID) -> Obligation:
        ob = (
            self.db.query(Obligation)
            .options(
                joinedload(Obligation.payer),
                joinedload(Obligation.recurring_config),
            )
            .filter(
                Obligation.id == obligation_id,
                Obligation.collection_id == self.collection_id,
            )
            .first()
        )
        if not ob:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Obligation not found")
        return ob

    def _get_payer_or_404(self, payer_id: uuid.UUID) -> Payer:
        p = (
            self.db.query(Payer)
            .filter(Payer.id == payer_id, Payer.collection_id == self.collection_id)
            .first()
        )
        if not p:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payer not found")
        return p

    def _get_group_or_404(self, group_id: uuid.UUID) -> PayerGroup:
        g = (
            self.db.query(PayerGroup)
            .filter(PayerGroup.id == group_id, PayerGroup.collection_id == self.collection_id)
            .first()
        )
        if not g:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payer group not found")
        return g

        return None


    def _apply_credit_to_obligation(self, payer: Payer, obligation: Obligation) -> Decimal:
        return apply_credit_to_obligation(payer, obligation)

    def get_recurring_preview(self, rt: RecurrenceType, amount: float, start_date: datetime, interval_days: int = None, day_of_month: int = None, day_of_week: int = None) -> str:
        """
        Generates a human-readable sentence explaining the recurring schedule.
        Example: "will bill KES 464 every week starting April 3, previous cycle closes automatically when the new one starts."
        """
        import calendar
        
        freq = ""
        if rt == RecurrenceType.MONTHLY:
            day = day_of_month or start_date.day
            suffix = 'th' if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
            freq = f"every month on the {day}{suffix}"
        elif rt == RecurrenceType.WEEKLY:
            day_name = calendar.day_name[day_of_week] if day_of_week is not None else calendar.day_name[start_date.weekday()]
            freq = f"every week on {day_name}"
        elif rt == RecurrenceType.CUSTOM:
            freq = f"every {interval_days} days"
        elif rt == RecurrenceType.TERM:
            freq = "every school term"

        start_fmt = start_date.strftime("%B %d")
        return f"will bill KES {amount:,.0f} {freq} starting {start_fmt}, previous cycle closes automatically when the new one starts."

    # ─── PayerGroup CRUD ─────────────────────────────────────────────────────

    async def create_group(self, data: PayerGroupCreate) -> PayerGroup:
        group = PayerGroup(
            collection_id=self.collection_id,
            name=data.name,
            description=data.description,
            group_type=data.group_type,
            meta=data.meta,
            created_by=self.current_user_id,
        )
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group

    async def list_groups(self, skip: int = 0, limit: int = 100) -> Tuple[int, List[PayerGroup]]:
        q = self.db.query(PayerGroup).filter(PayerGroup.collection_id == self.collection_id)
        total = q.count()
        items = q.order_by(PayerGroup.created_at.desc()).offset(skip).limit(limit).all()
        return total, items

    async def get_group(self, group_id: uuid.UUID) -> PayerGroup:
        return self._get_group_or_404(group_id)

    async def update_group(self, group_id: uuid.UUID, data: PayerGroupUpdate) -> PayerGroup:
        group = self._get_group_or_404(group_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(group, field, value)
        self.db.commit()
        self.db.refresh(group)
        return group

    async def delete_group(self, group_id: uuid.UUID) -> None:
        group = self._get_group_or_404(group_id)
        self.db.delete(group)
        self.db.commit()

    # ─── Payer CRUD ──────────────────────────────────────────────────────────

    async def create_payer(self, data: PayerCreate) -> Payer:
        from app.core.utils import generate_unique_reference
        if data.group_id:
            self._get_group_or_404(data.group_id)  # validate group belongs to collection

        account_no = data.account_no.strip().upper() if data.account_no else generate_unique_reference("PG")
        
        # 1. Ensure it's not already used as a CollectionPoint (Bulk flow)
        if account_no:
            from app.modules.ingestion.models import CollectionPoint
            existing_cp = (
                self.db.query(CollectionPoint)
                .filter(CollectionPoint.collection_id == self.collection_id, CollectionPoint.account_no == account_no)
                .first()
            )
            if existing_cp:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Account number {account_no} is already assigned to a fleet/collection point."
                )

        payer = Payer(
            collection_id=self.collection_id,
            group_id=data.group_id,
            name=data.name,
            phone=data.phone,
            email=data.email,
            account_no=account_no,
            identifier=data.identifier,
            notes=data.notes,
            meta=data.meta,
            created_by=self.current_user_id,
        )
        self.db.add(payer)
        self.db.commit()
        self.db.refresh(payer)
        return payer


    async def list_payers(
        self,
        group_id: Optional[uuid.UUID] = None,
        is_active: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[int, List[Payer]]:
        q = self.db.query(Payer).filter(Payer.collection_id == self.collection_id)
        if group_id:
            q = q.filter(Payer.group_id == group_id)
        if is_active is not None:
            q = q.filter(Payer.is_active == is_active)
        total = q.count()
        items = q.order_by(Payer.created_at.desc()).offset(skip).limit(limit).all()
        return total, items

    async def get_payer(self, payer_id: uuid.UUID) -> Payer:
        return self._get_payer_or_404(payer_id)

    async def update_payer(self, payer_id: uuid.UUID, data: PayerUpdate) -> Payer:
        payer = self._get_payer_or_404(payer_id)
        if data.group_id:
            self._get_group_or_404(data.group_id)

        update_data = data.model_dump(exclude_unset=True)
        if "account_no" in update_data and update_data["account_no"]:
            account_no = update_data["account_no"].strip().upper()
            update_data["account_no"] = account_no
            
            # 1. Ensure it's not already used as a CollectionPoint (Bulk flow)
            from app.modules.ingestion.models import CollectionPoint
            existing_cp = (
                self.db.query(CollectionPoint)
                .filter(CollectionPoint.collection_id == self.collection_id, CollectionPoint.account_no == account_no)
                .first()
            )
            if existing_cp:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Account number {account_no} is already assigned to a fleet/collection point."
                )

        for field, value in update_data.items():
            setattr(payer, field, value)
        self.db.commit()
        self.db.refresh(payer)
        return payer


    async def delete_payer(self, payer_id: uuid.UUID) -> None:
        payer = self._get_payer_or_404(payer_id)
        # Check for active unpaid obligations before hard delete
        open_count = (
            self.db.query(Obligation)
            .filter(
                Obligation.payer_id == payer_id,
                Obligation.status.in_([ObligationStatus.PENDING, ObligationStatus.PARTIAL, ObligationStatus.OVERDUE])
            )
            .count()
        )
        if open_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payer has {open_count} open obligation(s). Settle or cancel them first."
            )
        self.db.delete(payer)
        self.db.commit()

    # ─── Obligation CRUD ─────────────────────────────────────────────────────

    async def create_obligation(self, data: ObligationCreate) -> Obligation:
        if data.is_recurring and not data.recurring:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="recurring config is required when is_recurring=true"
            )

        # Validate payer belongs to this collection
        payer = self._get_payer_or_404(data.payer_id)
        
        # Auto-inherit account_no
        account_no = data.account_no or payer.account_no
        if not account_no:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="account_no is required (either on Payer or Obligation payload)"
            )
            
        # Auto-inherit due_date for recurring setups
        due_date = data.due_date
        if not due_date and data.is_recurring and data.recurring:
            due_date = data.recurring.start_date

        obligation = Obligation(
            collection_id=self.collection_id,
            payer_id=data.payer_id,
            account_no=account_no,
            description=data.description,
            amount_due=data.amount_due,
            amount_paid=0,
            balance=data.amount_due,
            currency=data.currency,
            due_date=due_date,
            is_recurring=data.is_recurring,
            status=ObligationStatus.PENDING,
            meta=data.meta or {},
            created_by=self.current_user_id,
        )
        self.db.add(obligation)
        self.db.flush()

        if data.is_recurring and data.recurring:
            rc = data.recurring
            next_due = self._compute_next_due(rc, rc.start_date)
            config = RecurringConfig(
                obligation_id=obligation.id,
                recurrence_type=rc.recurrence_type,
                interval_days=rc.interval_days,
                day_of_month=rc.day_of_month,
                day_of_week=rc.day_of_week,
                start_date=rc.start_date,
                end_date=rc.end_date,
                next_due_date=next_due,
                grace_period_days=rc.grace_period_days,
                auto_generate=rc.auto_generate,
            )
            self.db.add(config)

        # 4. Auto-apply existing credit
        self._apply_credit_to_obligation(payer, obligation)

        self.db.commit()
        self.db.refresh(obligation)

        # Publish event
        await self._publish_obligation_created(obligation, payer, float(credit_used))

        return obligation

    async def create_unified_payer_obligation(self, data: UnifiedPayerObligationCreate) -> Tuple[Payer, Obligation]:
        """
        Merged flow: Person + Invoice + Recurring in one transaction.
        """
        from app.core.utils import generate_unique_reference
        # 1. Payer creation logic (re-using logic from create_payer)
        account_no = data.account_no.strip().upper() if data.account_no else generate_unique_reference("PG")
        
        # Check for existing Payer by account_no or phone if provided
        payer = None
        if account_no:
            payer = self.db.query(Payer).filter(
                Payer.collection_id == self.collection_id,
                Payer.account_no == account_no
            ).first()
        
        if not payer and data.phone:
            payer = self.db.query(Payer).filter(
                Payer.collection_id == self.collection_id,
                Payer.phone == data.phone
            ).first()

        if not payer:
            # Create new payer
            payer = Payer(
                collection_id=self.collection_id,
                group_id=data.group_id,
                name=data.name,
                phone=data.phone,
                email=data.email,
                account_no=account_no,
                identifier=data.identifier,
                created_by=self.current_user_id,
            )
            self.db.add(payer)
            self.db.flush()
        else:
            # Update existing payer name/email if they were empty
            if not payer.email and data.email: payer.email = data.email
            if data.name and payer.name != data.name: payer.name = data.name

        # 2. Obligation creation
        obligation = Obligation(
            collection_id=self.collection_id,
            payer_id=payer.id,
            account_no=payer.account_no or account_no,
            description=data.description,
            amount_due=data.amount,
            amount_paid=0,
            balance=data.amount,
            currency=data.currency,
            due_date=data.due_date or (data.recurring.start_date if data.is_recurring and data.recurring else None),
            is_recurring=data.is_recurring,
            status=ObligationStatus.PENDING,
            created_by=self.current_user_id,
        )
        self.db.add(obligation)
        self.db.flush()

        # 3. Recurring config
        if data.is_recurring and data.recurring:
            rc = data.recurring
            next_due = self._compute_next_due(rc, rc.start_date)
            config = RecurringConfig(
                obligation_id=obligation.id,
                recurrence_type=rc.recurrence_type,
                interval_days=rc.interval_days,
                day_of_month=rc.day_of_month,
                day_of_week=rc.day_of_week,
                start_date=rc.start_date,
                end_date=rc.end_date,
                next_due_date=next_due,
                grace_period_days=rc.grace_period_days,
                auto_generate=rc.auto_generate,
            )
            self.db.add(config)

        # 4. Auto-apply existing credit
        credit_used = self._apply_credit_to_obligation(payer, obligation)

        self.db.commit()
        self.db.refresh(payer)
        self.db.refresh(obligation)

        await self._publish_obligation_created(obligation, payer, float(credit_used))
        return payer, obligation

    async def get_payer_ledger(self, payer_id: uuid.UUID) -> dict:
        """
        Unified statement for a person: all invoices and their status.
        Enhanced with rich status descriptions.
        """
        payer = self._get_payer_or_404(payer_id)
        obs = (
            self.db.query(Obligation)
            .filter(Obligation.payer_id == payer_id)
            .order_by(Obligation.created_at.desc())
            .all()
        )
        
        rich_obs = []
        for ob in obs:
            rich_obs.append(self._enrich_obligation(ob))

        total_due  = sum(float(ob.amount_due) for ob in obs)
        total_paid = sum(float(ob.amount_paid) for ob in obs)
        balance    = sum(float(ob.balance) for ob in obs)
        
        return {
            "payer": payer,
            "obligations": rich_obs,
            "total_due": total_due,
            "total_paid": total_paid,
            "balance": balance,
            "credit_balance": float(payer.credit_balance),
        }

    def _get_computed_status(self, ob: Obligation) -> ObligationStatus:
        status_val = ob.status
        if status_val in (ObligationStatus.PENDING, ObligationStatus.PARTIAL):
            from app.core.timezone import now_nairobi, make_aware
            if ob.due_date and make_aware(ob.due_date) < now_nairobi():
                return ObligationStatus.OVERDUE
        return status_val

    def _enrich_obligation(self, ob: Obligation) -> dict:
        """Helper to create the rich descriptive strings for the ledger."""
        desc = ""
        computed_status = self._get_computed_status(ob)
        if computed_status == ObligationStatus.OVERDUE:
            due_fmt = ob.due_date.strftime("%b %d") if ob.due_date else "unknown date"
            desc = f"Was due {due_fmt} — no payment received yet"
        elif ob.status == ObligationStatus.ROLLED:
            close_fmt = ob.updated_at.strftime("%b %d")
            desc = f"Auto-closed {close_fmt} — replaced by current cycle above"
        elif ob.status == ObligationStatus.PARTIAL:
            paid_fmt = f"KES {ob.amount_paid:,.2f}"
            bal_fmt = f"KES {ob.balance:,.2f}"
            desc = f"{paid_fmt} received — {bal_fmt} still outstanding"
        elif ob.status == ObligationStatus.SETTLED:
            settle_fmt = ob.updated_at.strftime("%b %d")
            desc = f"Paid in full {settle_fmt}"
        else:
            if ob.due_date:
                desc = f"Due {ob.due_date.strftime('%b %d, %Y')}"
            else:
                desc = "Awaiting payment"

        # Construct dictionary that matches ObligationLedgerItem schema
        return {
            "id": str(ob.id),
            "collection_id": str(ob.collection_id),
            "payer_id": str(ob.payer_id),
            "account_no": ob.account_no,
            "description": ob.description,
            "amount_due": float(ob.amount_due),
            "amount_paid": float(ob.amount_paid),
            "balance": float(ob.balance),
            "currency": ob.currency,
            "due_date": ob.due_date.isoformat() if ob.due_date else None,
            "status": computed_status.value,
            "status_reason": ob.status_reason,
            "is_recurring": ob.is_recurring,
            "meta": ob.meta,
            "created_by": str(ob.created_by),
            "created_at": ob.created_at.isoformat(),
            "updated_at": ob.updated_at.isoformat(),
            "status_description": desc
        }

    # ─── Ledger Tracker (materialized-view backed) ────────────────────────────

    async def refresh_ledger_summary(self) -> None:
        """
        Refresh the materialized view CONCURRENTLY so reads are never blocked.
        Stores the timestamp in Redis so we don't refresh more than once per 5 min.
        """
        refresh_key = f"ledger_summary:refreshed_at:{self.collection_id}"
        try:
            if cache.client:
                last_refresh = await cache.client.get(refresh_key)
                if last_refresh:
                    return  # Still fresh — skip
        except Exception:
            pass  # If Redis is down, proceed with the refresh anyway

        try:
            self.db.execute(text(
                "REFRESH MATERIALIZED VIEW CONCURRENTLY obligations.ledger_summary"
            ))
            self.db.commit()
            logger.info("🔄 Materialized view obligations.ledger_summary refreshed")
        except Exception as e:
            logger.warning(f"Materialized view refresh failed: {e}")
            return

        try:
            if cache.client:
                # Mark as fresh for 5 minutes (300 seconds)
                await cache.client.setex(refresh_key, 300, "1")
        except Exception:
            pass

    async def get_global_ledger(
        self,
        page: int = 1,
        page_size: int = 25,
        group_id: Optional[uuid.UUID] = None,
        status_filter: Optional[str] = None,  # "overdue" | "pending" | "settled" | "clear"
        search: Optional[str] = None,
    ) -> dict:
        """
        High-performance obligation tracker board backed by the
        obligations.ledger_summary materialized view.

        Replaces the original Python-loop global ledger with pure SQL aggregates.
        Cached per-tenant per-page for 30 seconds in Redis.
        """
        cache_key = (
            f"ledger:tracker:{self.collection_id}:"
            f"{page}:{page_size}:{group_id}:{status_filter}:{search}"
        )
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"Cache read error (ledger tracker): {e}")

        # Lazily refresh if the view is stale (Redis TTL guards against over-refresh)
        await self.refresh_ledger_summary()

        offset = (page - 1) * page_size
        cid = str(self.collection_id)

        # Build WHERE clause fragments
        where_clauses = ["ls.collection_id = :cid"]
        params: dict = {"cid": cid, "limit": page_size, "offset": offset}

        if group_id:
            where_clauses.append("ls.group_id = :group_id")
            params["group_id"] = str(group_id)

        if status_filter == "overdue":
            where_clauses.append("ls.overdue_count > 0")
        elif status_filter == "pending":
            where_clauses.append("ls.pending_count > 0 AND ls.overdue_count = 0")
        elif status_filter == "settled":
            where_clauses.append("ls.settled_count > 0 AND ls.overdue_count = 0 AND ls.pending_count = 0")
        elif status_filter == "clear":
            where_clauses.append("ls.overdue_count = 0 AND ls.pending_count = 0")

        if search:
            where_clauses.append("ls.payer_name ILIKE :search")
            params["search"] = f"%{search}%"

        where_sql = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT
                ls.payer_id,
                ls.payer_name,
                ls.payer_phone,
                ls.payer_account_no,
                ls.payer_is_active,
                ls.group_id,
                pg.name             AS group_name,
                ls.total_obligations,
                ls.total_due,
                ls.total_paid,
                ls.total_balance,
                ls.overdue_count,
                ls.pending_count,
                ls.settled_count,
                ls.next_due_date,
                ls.last_activity,
                ls.credit_balance,

                COUNT(*) OVER ()    AS row_count,
                SUM(ls.total_balance) OVER () AS grand_balance,
                SUM(ls.total_due)    OVER () AS grand_due,
                SUM(ls.total_paid)   OVER () AS grand_paid,
                SUM(ls.credit_balance) OVER () AS grand_credit,
                COUNT(*) FILTER (WHERE ls.overdue_count  > 0) OVER () AS overdue_payers,
                COUNT(*) FILTER (WHERE ls.pending_count  > 0 AND ls.overdue_count  = 0) OVER () AS pending_payers,
                COUNT(*) FILTER (WHERE ls.settled_count  > 0 AND ls.overdue_count  = 0 AND ls.pending_count = 0) OVER () AS settled_payers,
                COUNT(*) FILTER (WHERE ls.overdue_count  = 0 AND ls.pending_count  = 0) OVER () AS clear_payers
            FROM obligations.ledger_summary ls
            LEFT JOIN obligations.payer_groups pg ON pg.id = ls.group_id
            WHERE {where_sql}
            ORDER BY ls.overdue_count DESC, ls.total_balance DESC
            LIMIT :limit OFFSET :offset
        """)

        rows = self.db.execute(sql, params).fetchall()

        if not rows:
            empty = {
                "total_payers": 0,
                "total_balance": 0.0,
                "total_due": 0.0,
                "total_paid": 0.0,
                "grand_credit": 0.0,
                "counts": {"total": 0, "overdue": 0, "pending": 0, "settled": 0, "clear": 0},
                "page": page,
                "page_size": page_size,
                "items": [],
            }
            return empty

        first = rows[0]
        total_payers = first.row_count
        grand_balance = float(first.grand_balance or 0)
        grand_due = float(first.grand_due or 0)
        grand_paid = float(first.grand_paid or 0)
        grand_credit = float(first.grand_credit or 0)
        counts = {
            "total":   total_payers,
            "overdue": first.overdue_payers,
            "pending": first.pending_payers,
            "settled": first.settled_payers,
            "clear":   first.clear_payers,
        }

        items = []
        for r in rows:
            if r.overdue_count > 0:
                payer_status = "overdue"
            elif r.pending_count > 0:
                payer_status = "pending"
            elif r.settled_count > 0:
                payer_status = "settled"
            else:
                payer_status = "clear"

            items.append({
                "payer_id":          str(r.payer_id),
                "payer_name":        r.payer_name,
                "payer_phone":       r.payer_phone,
                "payer_account_no":  r.payer_account_no,
                "payer_is_active":   r.payer_is_active,
                "group_id":          str(r.group_id) if r.group_id else None,
                "group_name":        r.group_name,
                "total_obligations": r.total_obligations,
                "total_due":         float(r.total_due),
                "total_paid":        float(r.total_paid),
                "total_balance":     float(r.total_balance),
                "overdue_count":     r.overdue_count,
                "pending_count":     r.pending_count,
                "settled_count":     r.settled_count,
                "next_due_date":     r.next_due_date.isoformat() if r.next_due_date else None,
                "last_activity":     r.last_activity.isoformat() if r.last_activity else None,
                "credit_balance":    float(r.credit_balance or 0),
                "payer_status":      payer_status,
            })


        result = {
            "total_payers":  total_payers,
            "total_balance": grand_balance,
            "total_due":     grand_due,
            "total_paid":    grand_paid,
            "grand_credit":  grand_credit,
            "counts":        counts,
            "page":          page,
            "page_size":     page_size,
            "items":         items,
        }

        if cache.client:
            try:
                await cache.client.setex(cache_key, 30, json.dumps(result))
            except Exception as e:
                logger.warning(f"Cache write error (ledger tracker): {e}")

        return result

    async def get_group_summary(self, group_id: uuid.UUID) -> dict:
        """
        Single-query roll-up for one PayerGroup using the materialized view.
        Returns group metadata + per-payer rows, all from SQL.
        Cached for 60 seconds.
        """
        cache_key = f"ledger:group:{self.collection_id}:{group_id}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"Cache read error (group summary): {e}")

        # Validate group belongs to this collection
        group = self._get_group_or_404(group_id)

        sql = text("""
            SELECT
                ls.payer_id,
                ls.payer_name,
                ls.payer_phone,
                ls.payer_account_no,
                ls.payer_is_active,
                ls.group_id,
                ls.total_obligations,
                ls.total_due,
                ls.total_paid,
                ls.total_balance,
                ls.overdue_count,
                ls.pending_count,
                ls.settled_count,
                ls.next_due_date,
                ls.last_activity,
                ls.credit_balance
            FROM obligations.ledger_summary ls
            WHERE ls.collection_id = :cid
              AND ls.group_id = :gid
            ORDER BY ls.overdue_count DESC, ls.total_balance DESC
        """)

        rows = self.db.execute(sql, {
            "cid": str(self.collection_id),
            "gid": str(group_id),
        }).fetchall()

        payers = []
        for r in rows:
            if r.overdue_count > 0:
                payer_status = "overdue"
            elif r.pending_count > 0:
                payer_status = "pending"
            elif r.settled_count > 0:
                payer_status = "settled"
            else:
                payer_status = "clear"

            payers.append({
                "payer_id":          str(r.payer_id),
                "payer_name":        r.payer_name,
                "payer_phone":       r.payer_phone,
                "payer_account_no":  r.payer_account_no,
                "payer_is_active":   r.payer_is_active,
                "group_id":          str(r.group_id) if r.group_id else None,
                "group_name":        group.name,
                "total_obligations": r.total_obligations,
                "total_due":         float(r.total_due),
                "total_paid":        float(r.total_paid),
                "total_balance":     float(r.total_balance),
                "overdue_count":     r.overdue_count,
                "pending_count":     r.pending_count,
                "settled_count":     r.settled_count,
                "next_due_date":     r.next_due_date.isoformat() if r.next_due_date else None,
                "last_activity":     r.last_activity.isoformat() if r.last_activity else None,
                "credit_balance":    float(r.credit_balance or 0),
                "payer_status":      payer_status,
            })

        agg_sql = text("""
            SELECT
                COUNT(DISTINCT ls.payer_id)   AS total_payers,
                COALESCE(SUM(ls.total_due),  0) AS total_due,
                COALESCE(SUM(ls.total_paid), 0) AS total_paid,
                COALESCE(SUM(ls.total_balance), 0) AS total_balance,
                SUM(ls.overdue_count)  AS overdue_count,
                SUM(ls.pending_count)  AS pending_count,
                SUM(ls.settled_count)  AS settled_count
            FROM obligations.ledger_summary ls
            WHERE ls.collection_id = :cid AND ls.group_id = :gid
        """)
        agg = self.db.execute(agg_sql, {
            "cid": str(self.collection_id),
            "gid": str(group_id),
        }).fetchone()

        result = {
            "group_id":      str(group.id),
            "group_name":    group.name,
            "group_type":    group.group_type.value,
            "description":   group.description,
            "total_payers":  agg.total_payers if agg else 0,
            "total_due":     float(agg.total_due) if agg else 0.0,
            "total_paid":    float(agg.total_paid) if agg else 0.0,
            "total_balance": float(agg.total_balance) if agg else 0.0,
            "overdue_count": agg.overdue_count if agg else 0,
            "pending_count": agg.pending_count if agg else 0,
            "settled_count": agg.settled_count if agg else 0,
            "payers":        payers,
        }

        if cache.client:
            try:
                await cache.client.setex(cache_key, 60, json.dumps(result))
            except Exception as e:
                logger.warning(f"Cache write error (group summary): {e}")

        return result

    async def get_upcoming_payments(
        self,
        window_days: int = 30,
        group_id: Optional[uuid.UUID] = None,
    ) -> dict:
        """
        Date-bucketed view of the next N days of open obligations.
        Powered by the existing indexed `due_date` column — no mat-view needed.
        """
        cache_key = f"ledger:upcoming:{self.collection_id}:{window_days}:{group_id}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"Cache read error (upcoming): {e}")

        now = now_nairobi()
        window_end = now + timedelta(days=window_days)

        q = (
            self.db.query(Obligation)
            .options(joinedload(Obligation.payer))
            .filter(
                Obligation.collection_id == self.collection_id,
                Obligation.status.in_([
                    ObligationStatus.PENDING,
                    ObligationStatus.PARTIAL,
                    ObligationStatus.OVERDUE,
                ]),
                Obligation.due_date.isnot(None),
                Obligation.due_date <= window_end,
            )
            .order_by(Obligation.due_date.asc())
        )

        if group_id:
            q = q.join(Payer, Payer.id == Obligation.payer_id).filter(Payer.group_id == group_id)

        obs = q.all()

        # Bucket by due_date.date()
        buckets: dict = defaultdict(list)
        for ob in obs:
            day = ob.due_date.date()
            buckets[day].append(ob)

        entries = []
        for day in sorted(buckets.keys()):
            day_obs = buckets[day]
            paid_count   = sum(1 for o in day_obs if o.status == ObligationStatus.SETTLED)
            unpaid_count = len(day_obs) - paid_count
            total_due    = sum(float(o.balance) for o in day_obs)

            obs_out = []
            for o in day_obs:
                obs_out.append({
                    "id":           str(o.id),
                    "collection_id": str(o.collection_id),
                    "payer_id":     str(o.payer_id),
                    "account_no":   o.account_no,
                    "description":  o.description,
                    "amount_due":   float(o.amount_due),
                    "amount_paid":  float(o.amount_paid),
                    "balance":      float(o.balance),
                    "currency":     o.currency,
                    "due_date":     o.due_date.isoformat() if o.due_date else None,
                    "status":       self._get_computed_status(o).value,
                    "status_reason": o.status_reason,
                    "is_recurring": o.is_recurring,
                    "meta":         o.meta,
                    "created_by":   str(o.created_by),
                    "created_at":   o.created_at.isoformat(),
                    "updated_at":   o.updated_at.isoformat(),
                    "payer":        {
                        "id":            str(o.payer.id),
                        "name":          o.payer.name,
                        "phone":         o.payer.phone,
                        "account_no":    o.payer.account_no,
                        "collection_id": str(o.payer.collection_id),
                        "group_id":      str(o.payer.group_id) if o.payer.group_id else None,
                        "email":         o.payer.email,
                        "identifier":    o.payer.identifier,
                        "notes":         o.payer.notes,
                        "is_active":     o.payer.is_active,
                        "credit_balance": float(o.payer.credit_balance),
                        "meta":          o.payer.meta,
                        "created_by":    str(o.payer.created_by),
                        "created_at":    o.payer.created_at.isoformat(),
                        "updated_at":    o.payer.updated_at.isoformat(),
                    } if o.payer else None,
                    "recurring_config": None,
                })

            entries.append({
                "due_date":    day.isoformat(),
                "total_due":   total_due,
                "total_count": len(day_obs),
                "paid_count":  paid_count,
                "unpaid_count": unpaid_count,
                "obligations": obs_out,
            })

        result = {
            "window_days":    window_days,
            "total_upcoming": len(obs),
            "entries":        entries,
        }

        if cache.client:
            try:
                await cache.client.setex(cache_key, 60, json.dumps(result))
            except Exception as e:
                logger.warning(f"Cache write error (upcoming): {e}")

        return result

    def _get_status_desc(self, ob: Obligation) -> str:
        """Rich description based on obligation status (used in payer ledger)."""
        computed_status = self._get_computed_status(ob)
        if computed_status == ObligationStatus.OVERDUE:
            due_fmt = ob.due_date.strftime("%b %d") if ob.due_date else "unknown"
            return f"Was due {due_fmt} — no payment received yet"
        elif computed_status == ObligationStatus.ROLLED:
            close_fmt = ob.updated_at.strftime("%b %d")
            return f"Auto-closed {close_fmt} — replaced by current cycle above"
        elif computed_status == ObligationStatus.PARTIAL:
            return f"KES {ob.amount_paid:,.2f} received — KES {ob.balance:,.2f} still outstanding"
        elif computed_status == ObligationStatus.SETTLED:
            return f"Paid in full {ob.updated_at.strftime('%b %d')}"

        return f"Due {ob.due_date.strftime('%b %d')}" if ob.due_date else "Pending"

    async def _publish_obligation_created(self, obligation: Obligation, payer: Payer, credit_used: float = 0.0):
        try:
            from app.rabbitmq.publisher import BasePublisher
            from app.rabbitmq.types import EventType, Priority
            publisher = BasePublisher(service_name="obligations-service")
            await publisher.publish_event(
                event_type=EventType.OBLIGATION_CREATED,
                payload={
                    "obligation_id":  str(obligation.id),
                    "collection_id":  str(obligation.collection_id),
                    "payer_id":       str(payer.id),
                    "payer_name":     payer.name,
                    "phone":          payer.phone or "",
                    "email":          payer.email or "",
                    "account_no":     obligation.account_no,
                    "amount_due":     float(obligation.amount_due),
                    "amount_paid":    float(obligation.amount_paid),
                    "balance":        float(obligation.balance),
                    "currency":       obligation.currency,
                    "status":         obligation.status.value,
                    "due_date":       obligation.due_date.isoformat() if obligation.due_date else "",
                    "description":    obligation.description or "",
                    "credit_used":    credit_used,
                },
                priority=Priority.MEDIUM,
            )
        except Exception as e:
            logger.warning(f"Failed to publish obligation.created event: {e}")

    async def list_obligations(
        self,
        payer_id: Optional[uuid.UUID] = None,
        account_no: Optional[str] = None,
        ob_status: Optional[ObligationStatus] = None,
        is_recurring: Optional[bool] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[int, List[Obligation]]:
        # Build cache key scoped to this tenant
        cache_key = f"obligations:list:{self.collection_id}:{payer_id}:{account_no}:{ob_status}:{is_recurring}:{skip}:{limit}"
        if cache.client:
            try:
                cached = await cache.client.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return data["total"], []
            except Exception as e:
                logger.warning(f"Cache read error (obligations list): {e}")

        q = (
            self.db.query(Obligation)
            .options(
                joinedload(Obligation.payer),
                joinedload(Obligation.recurring_config),
            )
            .filter(Obligation.collection_id == self.collection_id)
        )
        if payer_id:
            q = q.filter(Obligation.payer_id == payer_id)
        if account_no:
            q = q.filter(Obligation.account_no == account_no)
        if ob_status:
            q = q.filter(Obligation.status == ob_status)
        if is_recurring is not None:
            q = q.filter(Obligation.is_recurring == is_recurring)

        # Single query: use a window function to avoid separate COUNT round-trip
        total_col = func.count(Obligation.id).over().label("_total")
        q_with_total = (
            self.db.query(Obligation, total_col)
            .options(
                joinedload(Obligation.payer),
                joinedload(Obligation.recurring_config),
            )
            .filter(Obligation.collection_id == self.collection_id)
        )
        if payer_id:
            q_with_total = q_with_total.filter(Obligation.payer_id == payer_id)
        if account_no:
            q_with_total = q_with_total.filter(Obligation.account_no == account_no)
        if ob_status:
            q_with_total = q_with_total.filter(Obligation.status == ob_status)
        if is_recurring is not None:
            q_with_total = q_with_total.filter(Obligation.is_recurring == is_recurring)

        rows = (
            q_with_total
            .order_by(Obligation.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        items = [row[0] for row in rows]
        total = rows[0][1] if rows else 0

        # Cache total only (models can't be serialised easily)
        if cache.client:
            try:
                await cache.client.setex(cache_key, 60, json.dumps({"total": total}))
            except Exception as e:
                logger.warning(f"Cache write error (obligations list): {e}")

        return total, items

    async def get_obligation(self, obligation_id: uuid.UUID) -> Obligation:
        cache_key = f"obligation:{self.collection_id}:{obligation_id}"
        # Single row with joinedload — no cache for the ORM object itself,
        # but _get_obligation_or_404 already uses joinedload so it's a single SQL.
        return self._get_obligation_or_404(obligation_id)

    async def update_obligation(self, obligation_id: uuid.UUID, data: ObligationUpdate) -> Obligation:
        ob = self._get_obligation_or_404(obligation_id)
        if ob.status in (ObligationStatus.SETTLED, ObligationStatus.VOIDED, ObligationStatus.ROLLED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot update an obligation with status '{ob.status}'"
            )
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(ob, field, value)
        if "amount_due" in update_data:
            ob.balance = float(ob.amount_due) - float(ob.amount_paid)
        self.db.commit()
        self.db.refresh(ob)
        return ob

    async def update_recurring_config(self, obligation_id: uuid.UUID, data: RecurringConfigUpdate) -> Obligation:
        ob = self._get_obligation_or_404(obligation_id)
        if not ob.recurring_config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No recurring config on this obligation")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(ob.recurring_config, field, value)
        self.db.commit()
        self.db.refresh(ob)
        return ob

    async def cancel_obligation(self, obligation_id: uuid.UUID, reason: str = "Manual void") -> Obligation:
        ob = self._get_obligation_or_404(obligation_id)
        if ob.status in (ObligationStatus.VOIDED, ObligationStatus.ROLLED):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Obligation already closed")
        
        ob.status = ObligationStatus.VOIDED
        ob.status_reason = reason
        
        # Stop future invoices if this was a recurring obligation setup
        if ob.recurring_config:
            ob.recurring_config.auto_generate = False
            
        self.db.commit()
        self.db.refresh(ob)

        # Payer already loaded via joinedload — no extra query
        payer_name = ob.payer.name if ob.payer else "Unknown"
        payer_email = ob.payer.email if ob.payer else None
        payer_phone = ob.payer.phone if ob.payer else None

        # Publish event
        _publisher = BasePublisher(service_name="obligations-service")
        await _publisher.publish_event(
            event_type=EventType.OBLIGATION_CANCELLED,
            payload={
                "obligation_id": str(ob.id),
                "collection_id": str(ob.collection_id),
                "payer_id": str(ob.payer_id),
                "payer_name": payer_name,
                "account_no": ob.account_no,
                "description": ob.description,
                "currency": ob.currency,
                "balance": float(ob.balance),
                "email": payer_email,
                "phone": payer_phone,
                "reason": reason,
            }
        )

        return ob

    async def delete_obligation(self, obligation_id: uuid.UUID) -> None:
        ob = self._get_obligation_or_404(obligation_id)
        if ob.status == ObligationStatus.SETTLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete a fully paid obligation. Void it instead."
            )
        self.db.delete(ob)
        self.db.commit()

    # ─── NotificationTemplate CRUD ───────────────────────────────────────────

    async def create_template(self, data: NotificationTemplateCreate) -> NotificationTemplate:
        # If this is set as default — unset any existing default for same type+channel
        if data.is_default:
            self.db.query(NotificationTemplate).filter(
                NotificationTemplate.collection_id == self.collection_id,
                NotificationTemplate.template_type == data.template_type,
                NotificationTemplate.channel == data.channel,
                NotificationTemplate.is_default.is_(True),
            ).update({"is_default": False})

        template = NotificationTemplate(
            collection_id=self.collection_id,
            name=data.name,
            template_type=data.template_type,
            channel=data.channel,
            subject=data.subject,
            body=data.body,
            is_default=data.is_default,
            created_by=self.current_user_id,
        )
        self.db.add(template)
        self.db.commit()
        self.db.refresh(template)
        return template

    async def create_templates_bulk(self, data: List[NotificationTemplateCreate]) -> List[NotificationTemplate]:
        """Create multiple templates in a single request. Skips existing exact matches? No, just creates."""
        results = []
        for t_data in data:
            # Re-use logic for unsetting defaults
            if t_data.is_default:
                self.db.query(NotificationTemplate).filter(
                    NotificationTemplate.collection_id == self.collection_id,
                    NotificationTemplate.template_type == t_data.template_type,
                    NotificationTemplate.channel == t_data.channel,
                    NotificationTemplate.is_default.is_(True),
                ).update({"is_default": False})

            template = NotificationTemplate(
                collection_id=self.collection_id,
                name=t_data.name,
                template_type=t_data.template_type,
                channel=t_data.channel,
                subject=t_data.subject,
                body=t_data.body,
                is_default=t_data.is_default,
                created_by=self.current_user_id,
            )
            self.db.add(template)
            results.append(template)
        
        self.db.commit()
        for t in results:
            self.db.refresh(t)
        return results

    async def list_templates(
        self,
        template_type: Optional[TemplateType] = None,
        channel: Optional[TemplateChannel] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[int, List[NotificationTemplate]]:
        q = self.db.query(NotificationTemplate).filter(
            NotificationTemplate.collection_id == self.collection_id
        )
        if template_type:
            q = q.filter(NotificationTemplate.template_type == template_type)
        if channel:
            q = q.filter(NotificationTemplate.channel == channel)
        total = q.count()
        items = q.order_by(NotificationTemplate.created_at.desc()).offset(skip).limit(limit).all()
        return total, items

    async def get_template(self, template_id: uuid.UUID) -> NotificationTemplate:
        t = (
            self.db.query(NotificationTemplate)
            .filter(NotificationTemplate.id == template_id, NotificationTemplate.collection_id == self.collection_id)
            .first()
        )
        if not t:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        return t

    async def update_template(self, template_id: uuid.UUID, data: NotificationTemplateUpdate) -> NotificationTemplate:
        t = await self.get_template(template_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(t, field, value)
        self.db.commit()
        self.db.refresh(t)
        return t

    async def delete_template(self, template_id: uuid.UUID) -> None:
        t = await self.get_template(template_id)
        self.db.delete(t)
        self.db.commit()
