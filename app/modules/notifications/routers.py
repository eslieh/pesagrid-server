import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependancies import get_current_verified_user, get_db
from app.modules.auth.models import User
from app.modules.notifications.models import NotificationLog, NotifChannel, NotifStatus
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List

notifications_router = APIRouter(tags=["Notifications"])


class NotificationLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:           uuid.UUID
    collection_id: uuid.UUID
    payer_id:     Optional[uuid.UUID]
    channel:      NotifChannel
    recipient:    str
    event_type:   str
    subject:      Optional[str]
    body:         str
    status:       NotifStatus
    provider_ref: Optional[str]
    error_msg:    Optional[str]
    sent_at:      Optional[datetime]
    created_at:   datetime


class NotificationLogListResponse(BaseModel):
    total: int
    items: List[NotificationLogResponse]


@notifications_router.get(
    "/logs",
    response_model=NotificationLogListResponse,
    summary="Notification send history",
)
def list_notification_logs(
    channel:    Optional[NotifChannel]  = Query(None),
    log_status: Optional[NotifStatus]   = Query(None, alias="status"),
    event_type: Optional[str]           = Query(None),
    payer_id:   Optional[uuid.UUID]     = Query(None),
    skip:       int                     = Query(0, ge=0),
    limit:      int                     = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    """View every notification sent (or failed) — filterable by channel, status, event, payer."""
    q = db.query(NotificationLog).filter(NotificationLog.collection_id == current_user.id)
    if channel:
        q = q.filter(NotificationLog.channel == channel)
    if log_status:
        q = q.filter(NotificationLog.status == log_status)
    if event_type:
        q = q.filter(NotificationLog.event_type == event_type)
    if payer_id:
        q = q.filter(NotificationLog.payer_id == payer_id)
    total = q.count()
    items = q.order_by(NotificationLog.created_at.desc()).offset(skip).limit(limit).all()
    return NotificationLogListResponse(total=total, items=items)


class NotificationSettings(BaseModel):
    payment_notifications_enabled: bool = False
    payment_notification_channels: List[str] = ["email"]


@notifications_router.get(
    "/settings",
    response_model=NotificationSettings,
    summary="Get business notification settings",
)
def get_notification_settings(
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    from app.modules.accounts.models import BusinessProfile
    profile = db.query(BusinessProfile).filter(BusinessProfile.collection_id == current_user.id).first()
    if not profile or not profile.meta:
        return NotificationSettings()
    
    return NotificationSettings(
        payment_notifications_enabled=profile.meta.get("payment_notifications_enabled", False),
        payment_notification_channels=profile.meta.get("payment_notification_channels", ["email"]),
    )


@notifications_router.patch(
    "/settings",
    response_model=NotificationSettings,
    summary="Update business notification settings",
)
def update_notification_settings(
    data: NotificationSettings,
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    from app.modules.accounts.models import BusinessProfile
    profile = db.query(BusinessProfile).filter(BusinessProfile.collection_id == current_user.id).first()
    if not profile:
        # Create a skeleton profile if it doesn't exist? 
        # Usually it should exist by the time they reach settings.
        raise HTTPException(status_code=404, detail="Business profile not found")
    
    if profile.meta is None:
        profile.meta = {}
    
    # Merge settings into meta
    profile.meta["payment_notifications_enabled"] = data.payment_notifications_enabled
    profile.meta["payment_notification_channels"] = data.payment_notification_channels
    
    # SQLAlchemy note: force refresh of JSONB if needed, though simple dict assignment usually works
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(profile, "meta")
    
    db.commit()
    db.refresh(profile)
    
    return NotificationSettings(
        payment_notifications_enabled=profile.meta.get("payment_notifications_enabled", False),
        payment_notification_channels=profile.meta.get("payment_notification_channels", ["email"]),
    )
