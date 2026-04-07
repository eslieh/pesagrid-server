from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, text
import uuid
import logging
import json

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

    def _compute_next_due(self, config_data, from_date: datetime) -> Optional[datetime]:
        """Compute next_due_date from a RecurringConfigCreate."""
        rt = config_data.recurrence_type

        if rt == RecurrenceType.MONTHLY:
            import calendar
            day = config_data.day_of_month or from_date.day
            
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
            dow = config_data.day_of_week if config_data.day_of_week is not None else from_date.weekday()
            days_ahead = (dow - from_date.weekday()) % 7 or 7
            return from_date + timedelta(days=days_ahead)

        elif rt == RecurrenceType.CUSTOM:
            if not config_data.interval_days:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="interval_days is required for CUSTOM recurrence"
                )
            return from_date + timedelta(days=config_data.interval_days)

        elif rt == RecurrenceType.TERM:
            # Term billing: next_due_date managed by the scheduling engine
            return None

        return None

    def _apply_credit_to_obligation(self, payer: Payer, obligation: Obligation):
        """Consume any existing payer credit to settle or partially pay a new obligation."""
        from decimal import Decimal
        credit = Decimal(str(payer.credit_balance or 0))
        if credit <= 0:
            return

        needed = Decimal(str(obligation.balance))
        usage = min(credit, needed)

        obligation.amount_paid = Decimal(str(obligation.amount_paid)) + usage
        obligation.balance = Decimal(str(obligation.amount_due)) - Decimal(str(obligation.amount_paid))
        payer.credit_balance = credit - usage

        if obligation.balance <= 0:
            obligation.status = ObligationStatus.SETTLED
            obligation.balance = Decimal("0")
        elif usage > 0:
            obligation.status = ObligationStatus.PARTIAL

        logger.info(f"💳 Applied {usage} credit from Payer {payer.id} to Obligation {obligation.id}")

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
        if data.group_id:
            self._get_group_or_404(data.group_id)  # validate group belongs to collection

        account_no = data.account_no.strip().upper() if data.account_no else None
        
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
        await self._publish_obligation_created(obligation, payer)

        return obligation

    async def create_unified_payer_obligation(self, data: UnifiedPayerObligationCreate) -> Tuple[Payer, Obligation]:
        """
        Merged flow: Person + Invoice + Recurring in one transaction.
        """
        # 1. Payer creation logic (re-using logic from create_payer)
        account_no = data.account_no.strip().upper() if data.account_no else None
        
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
        self._apply_credit_to_obligation(payer, obligation)

        self.db.commit()
        self.db.refresh(payer)
        self.db.refresh(obligation)

        await self._publish_obligation_created(obligation, payer)
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

    def _enrich_obligation(self, ob: Obligation) -> dict:
        """Helper to create the rich descriptive strings for the ledger."""
        desc = ""
        if ob.status == ObligationStatus.OVERDUE:
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
            "status": ob.status.value,
            "status_reason": ob.status_reason,
            "is_recurring": ob.is_recurring,
            "meta": ob.meta,
            "created_by": str(ob.created_by),
            "created_at": ob.created_at.isoformat(),
            "updated_at": ob.updated_at.isoformat(),
            "status_description": desc
        }

    async def get_global_ledger(self, 
        status_filter: Optional[ObligationStatus] = None,
        is_recurring: Optional[bool] = None,
        overdue_only: bool = False,
        this_month: bool = False
    ) -> dict:
        """
        Global dashboard view: Grouped by Payer with filters.
        """
        q = self.db.query(Obligation).filter(Obligation.collection_id == self.collection_id)
        
        # 1. Global counts (unfiltered)
        # 1. Global counts (Database-level aggregation)
        status_counts = (
            self.db.query(Obligation.status, func.count(Obligation.id))
            .filter(Obligation.collection_id == self.collection_id)
            .group_by(Obligation.status)
            .all()
        )
        status_map = {s.value: count for s, count in status_counts}
        
        recurring_count = (
            self.db.query(func.count(Obligation.id))
            .filter(Obligation.collection_id == self.collection_id, Obligation.is_recurring == True)
            .scalar()
        )

        counts = {
            "all": sum(status_map.values()),
            "overdue": status_map.get(ObligationStatus.OVERDUE, 0),
            "pending": status_map.get(ObligationStatus.PENDING, 0) + status_map.get(ObligationStatus.PARTIAL, 0),
            "settled": status_map.get(ObligationStatus.SETTLED, 0),
            "recurring": recurring_count,
        }

        # 2. Applying filters
        if status_filter:
            q = q.filter(Obligation.status == status_filter)
        if is_recurring is not None:
            q = q.filter(Obligation.is_recurring == is_recurring)
        if overdue_only:
            q = q.filter(Obligation.status == ObligationStatus.OVERDUE)
        if this_month:
            now = now_nairobi()
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            q = q.filter(Obligation.created_at >= start)

        # Add Eager Loading to fix N+1 (Payer and its Group)
        q = q.options(
            joinedload(Obligation.payer).joinedload(Payer.group)
        )
        
        obs = q.order_by(Obligation.created_at.desc()).all()

        # 3. Grouping by Payer
        payer_map = {}
        for ob in obs:
            p_id = ob.payer_id
            if p_id not in payer_map:
                payer_map[p_id] = {
                    "payer": ob.payer,
                    "obligations": [],
                    "total_amount": 0,
                    "overdue_count": 0,
                    "pending_count": 0,
                    "status": "pending"
                }
            
            # Enrich the obligation
            rich_ob = ob
            rich_ob.status_description = self._get_status_desc(ob) # Dynamic attribute
            
            payer_map[p_id]["obligations"].append(rich_ob)
            payer_map[p_id]["total_amount"] += float(ob.balance)
            if ob.status == ObligationStatus.OVERDUE:
                payer_map[p_id]["overdue_count"] += 1
                payer_map[p_id]["status"] = "overdue"
            elif ob.status in (ObligationStatus.PENDING, ObligationStatus.PARTIAL):
                payer_map[p_id]["pending_count"] += 1

        items = []
        for p_id, data in payer_map.items():
            summary = ""
            if data["overdue_count"] > 0:
                summary = f"No group · {data['overdue_count']} overdue invoice"
            elif data["pending_count"] > 0:
                summary = f"No group · {data['pending_count']} pending invoice"
            else:
                summary = "All settled"
            
            # If they have a group, use it
            if data["payer"].group:
                summary = summary.replace("No group", data["payer"].group.name)

            items.append({
                "payer": data["payer"],
                "summary_text": summary,
                "total_amount": data["total_amount"],
                "status": data["status"],
                "obligations": data["obligations"]
            })

        return {
            "total_payers": len(items),
            "counts": counts,
            "items": items
        }

    def _get_status_desc(self, ob: Obligation) -> str:
        """Logic for rich description based on status."""
        if ob.status == ObligationStatus.OVERDUE:
            due_fmt = ob.due_date.strftime("%b %d") if ob.due_date else "unknown"
            return f"Was due {due_fmt} — no payment received yet"
        elif ob.status == ObligationStatus.ROLLED:
            close_fmt = ob.updated_at.strftime("%b %d")
            return f"Auto-closed {close_fmt} — replaced by current cycle above"
        elif ob.status == ObligationStatus.PARTIAL:
            return f"KES {ob.amount_paid:,.2f} received — KES {ob.balance:,.2f} still outstanding"
        elif ob.status == ObligationStatus.SETTLED:
            return f"Paid in full {ob.updated_at.strftime('%b %d')}"
        
        return f"Due {ob.due_date.strftime('%b %d')}" if ob.due_date else "Pending"

    async def _publish_obligation_created(self, obligation: Obligation, payer: Payer):
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
                    "currency":       obligation.currency,
                    "due_date":       obligation.due_date.isoformat() if obligation.due_date else "",
                    "description":    obligation.description or "",
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
