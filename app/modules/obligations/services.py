from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
import uuid
import logging

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
)

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
            try:
                from dateutil.relativedelta import relativedelta
                day = config_data.day_of_month or from_date.day
                next_dt = from_date.replace(day=day)
                if next_dt <= from_date:
                    next_dt = (from_date + relativedelta(months=1)).replace(day=day)
                return next_dt
            except ImportError:
                # fallback without dateutil
                return from_date + timedelta(days=30)

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
        group.updated_at = datetime.utcnow()
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

        payer = Payer(
            collection_id=self.collection_id,
            group_id=data.group_id,
            name=data.name,
            phone=data.phone,
            email=data.email,
            account_no=data.account_no,
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
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(payer, field, value)
        payer.updated_at = datetime.utcnow()
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

        self.db.commit()
        self.db.refresh(obligation)

        # Publish event for the notification worker
        payer = self._get_payer_or_404(data.payer_id)
        await self._publish_obligation_created(obligation, payer)

        return obligation

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
        q = self.db.query(Obligation).filter(Obligation.collection_id == self.collection_id)
        if payer_id:
            q = q.filter(Obligation.payer_id == payer_id)
        if account_no:
            q = q.filter(Obligation.account_no == account_no)
        if ob_status:
            q = q.filter(Obligation.status == ob_status)
        if is_recurring is not None:
            q = q.filter(Obligation.is_recurring == is_recurring)
        total = q.count()
        items = q.order_by(Obligation.created_at.desc()).offset(skip).limit(limit).all()
        return total, items

    async def get_obligation(self, obligation_id: uuid.UUID) -> Obligation:
        return self._get_obligation_or_404(obligation_id)

    async def update_obligation(self, obligation_id: uuid.UUID, data: ObligationUpdate) -> Obligation:
        ob = self._get_obligation_or_404(obligation_id)
        if ob.status in (ObligationStatus.PAID, ObligationStatus.CANCELLED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot update an obligation with status '{ob.status}'"
            )
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(ob, field, value)
        if "amount_due" in update_data:
            ob.balance = float(ob.amount_due) - float(ob.amount_paid)
        ob.updated_at = datetime.utcnow()
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

    async def cancel_obligation(self, obligation_id: uuid.UUID) -> Obligation:
        ob = self._get_obligation_or_404(obligation_id)
        if ob.status == ObligationStatus.CANCELLED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Obligation already cancelled")
        
        ob.status = ObligationStatus.CANCELLED
        ob.updated_at = datetime.utcnow()
        
        # Stop future invoices if this was a recurring obligation setup
        if ob.recurring_config:
            ob.recurring_config.auto_generate = False
            
        self.db.commit()
        self.db.refresh(ob)
        return ob

    async def delete_obligation(self, obligation_id: uuid.UUID) -> None:
        ob = self._get_obligation_or_404(obligation_id)
        if ob.status == ObligationStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete a fully paid obligation. Cancel it instead."
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
        t.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(t)
        return t

    async def delete_template(self, template_id: uuid.UUID) -> None:
        t = await self.get_template(template_id)
        self.db.delete(t)
        self.db.commit()
