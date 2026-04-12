import uuid
from typing import Optional, Any, Dict
from datetime import datetime
from fastapi import APIRouter, Depends, Query, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.dependancies import get_current_verified_user, get_db, verify_mfa
from app.core.config import settings
from app.modules.auth.models import User
from app.modules.accounts.models import BusinessProfile, PSPConfig
from app.rabbitmq import BasePublisher, EventType

async def publish_config_event(event_type: EventType, payload: dict):
    publisher = BasePublisher("accounts-service")
    await publisher.publish_event(event_type, payload)
from app.modules.accounts.models import BusinessProfile
from app.modules.accounts.schema import (
    PSPConfigCreate, PSPConfigUpdate,
    PSPConfigResponse, PSPConfigCreateResponse, PSPConfigListResponse,
)
from app.modules.ingestion.services import AccountsService

accounts_router = APIRouter(tags=["Accounts / PSP Settings"])


# ─── BusinessProfile schemas (inline — simple enough) ─────────────────────────

class BusinessProfileCreate(BaseModel):
    business_name: str
    display_name:  Optional[str]  = None
    phone:         Optional[str]  = None
    email:         Optional[str]  = None
    address:       Optional[str]  = None
    logo_url:      Optional[str]  = None
    email_from:    Optional[str]  = None
    meta:          Optional[Dict[str, Any]] = None

class BusinessProfileUpdate(BaseModel):
    business_name: Optional[str] = None
    display_name:  Optional[str] = None
    phone:         Optional[str] = None
    email:         Optional[str] = None
    address:       Optional[str] = None
    logo_url:      Optional[str] = None
    email_from:    Optional[str] = None
    meta:          Optional[Dict[str, Any]] = None

class BusinessProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id:            uuid.UUID
    collection_id: uuid.UUID
    business_name: str
    display_name:  Optional[str]
    phone:         Optional[str]
    email:         Optional[str]
    address:       Optional[str]
    logo_url:      Optional[str]
    email_from:    Optional[str]
    meta:          Optional[Dict[str, Any]]
    created_at:    datetime


def get_service(
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
) -> AccountsService:
    return AccountsService(
        db=db,
        collection_id=current_user.id,
        current_user_id=current_user.id,
    )


@accounts_router.post(
    "/psp",
    response_model=PSPConfigCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a payment channel",
)
def create_psp(
    data: PSPConfigCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(verify_mfa),
    service: AccountsService = Depends(get_service),
):
    """Register a PSP and dispatch a notification with instructions."""
    cfg = service.create_psp(data, base_url=settings.BASE_URL)
    
    payload = {
        "collection_id": str(cfg.collection_id),
        "psp_id": str(cfg.id),
        "collection_id": str(cfg.collection_id),
        "psp_type": cfg.psp_type.value,
        "paybill": cfg.paybill,
        "till_number": cfg.till_number,
        "business_key": cfg.business_key,
        "account_no": cfg.account_no,
        "webhook_url": cfg.webhook_url,
    }
    background_tasks.add_task(publish_config_event, EventType.CONFIG_PSP_CREATED, payload)
    
    return cfg


@accounts_router.get(
    "/psp",
    response_model=PSPConfigListResponse,
    summary="List payment channels",
)
def list_psps(
    skip:  int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    service: AccountsService = Depends(get_service),
):
    total, items = service.list_psps(skip=skip, limit=limit)
    return PSPConfigListResponse(total=total, items=items)


@accounts_router.get(
    "/psp/{psp_id}",
    response_model=PSPConfigResponse,
    summary="Get payment channel",
)
def get_psp(
    psp_id: uuid.UUID,
    service: AccountsService = Depends(get_service),
):
    return service.get_psp(psp_id)


@accounts_router.patch(
    "/psp/{psp_id}",
    response_model=PSPConfigResponse,
    summary="Update payment channel",
)
def update_psp(
    psp_id: uuid.UUID,
    data: PSPConfigUpdate,
    current_user: User = Depends(verify_mfa),
    service: AccountsService = Depends(get_service),
):
    return service.update_psp(psp_id, data)


@accounts_router.delete(
    "/psp/{psp_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete payment channel",
)
def delete_psp(
    psp_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(verify_mfa),
    service: AccountsService = Depends(get_service),
):
    cfg = service.get_psp(psp_id)
    payload = {
        "collection_id": str(cfg.collection_id),
        "psp_type": cfg.psp_type.value,
        "paybill": cfg.paybill,
    }
    
    service.delete_psp(psp_id)
    background_tasks.add_task(publish_config_event, EventType.CONFIG_PSP_DELETED, payload)


# ══════════════════════════════════════════════════════════════════════════════
#  BUSINESS PROFILE — setup + identity for the workspace
# ══════════════════════════════════════════════════════════════════════════════

@accounts_router.post(
    "/profile",
    response_model=BusinessProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create business profile",
)
def create_profile(
    data: BusinessProfileCreate,
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    """
    Register your business identity on PesaGrid.
    `display_name` appears as the sender name in SMS/email notifications.
    `email_from` overrides the platform default sender email for your messages.
    """
    existing = db.query(BusinessProfile).filter(BusinessProfile.collection_id == current_user.id).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Profile already exists. Use PATCH to update.")
    profile = BusinessProfile(
        collection_id=current_user.id,
        created_by=current_user.id,
        **data.model_dump(exclude_unset=True),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@accounts_router.get(
    "/profile",
    response_model=BusinessProfileResponse,
    summary="Get business profile",
)
def get_profile(
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    profile = db.query(BusinessProfile).filter(BusinessProfile.collection_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No business profile yet. POST /accounts/profile to create one.")
    return profile


@accounts_router.patch(
    "/profile",
    response_model=BusinessProfileResponse,
    summary="Update business profile",
)
def update_profile(
    data: BusinessProfileUpdate,
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    profile = db.query(BusinessProfile).filter(BusinessProfile.collection_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No business profile yet.")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile

