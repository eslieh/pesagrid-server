from datetime import datetime
from enum import Enum
from sqlalchemy import Boolean, Column, DateTime, Text, Enum as SQLEnum, Index
from app.core.db_types import UUID, uuid4
from app.core.base import Base
from app.core.timezone import now_nairobi


class NotifChannel(str, Enum):
    SMS      = "sms"
    EMAIL    = "email"
    WHATSAPP = "whatsapp"  # future


class NotifStatus(str, Enum):
    QUEUED  = "queued"
    SENT    = "sent"
    FAILED  = "failed"
    SKIPPED = "skipped"   # no contact info / channel not configured


class NotificationLog(Base):
    """
    Immutable record of every notification dispatched (or attempted).

    Lets the business see what was sent, to whom, and whether it succeeded.
    One row per channel per dispatch — if we send both SMS and email for one
    event, that's two rows.
    """
    __tablename__ = "notification_logs"

    id            = Column(UUID, primary_key=True, default=uuid4)
    collection_id = Column(UUID, nullable=False, index=True)
    payer_id      = Column(UUID, nullable=True, index=True)   # nullable for owner-only notifs / auth events
    channel       = Column(SQLEnum(NotifChannel), nullable=False, index=True)
    recipient     = Column(Text, nullable=False)               # phone number or email address
    event_type    = Column(Text, nullable=False, index=True)   # e.g. "payment.matched"
    template_id   = Column(UUID, nullable=True)               # FK not enforced — template may be deleted
    subject       = Column(Text, nullable=True)               # email subject (null for SMS)
    body          = Column(Text, nullable=False)               # rendered message body
    status        = Column(SQLEnum(NotifStatus), default=NotifStatus.QUEUED, nullable=False, index=True)
    provider_ref  = Column(Text, nullable=True)               # Resend message ID / Hostpinnacle ref
    error_msg     = Column(Text, nullable=True)
    sent_at       = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=now_nairobi, nullable=False)

    __table_args__ = (
        Index("idx_notif_logs_collection_event",    "collection_id", "event_type"),
        Index("idx_notif_logs_collection_status",   "collection_id", "status"),
        Index("idx_notif_logs_collection_channel",  "collection_id", "channel"),
        {"schema": "notifications"},
    )
