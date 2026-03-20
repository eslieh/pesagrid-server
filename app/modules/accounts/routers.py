import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.core.dependancies import get_current_verified_user, get_db
from app.core.config import settings
from app.modules.auth.models import User
from app.modules.accounts.schema import (
    PSPConfigCreate, PSPConfigUpdate,
    PSPConfigResponse, PSPConfigCreateResponse, PSPConfigListResponse,
)
from app.modules.ingestion.services import AccountsService

accounts_router = APIRouter(tags=["Accounts / PSP Settings"])


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
    service: AccountsService = Depends(get_service),
):
    """
    Register a PSP (e.g. M-PESA, KCB) as a payment entry point.

    The response includes the generated **webhook_url** — register this URL
    with the PSP so they send callbacks to your Pesagrid workspace.

    **M-PESA example** — register with Safaricom Daraja as your C2B
    Confirmation URL and Validation URL.

    Credentials are shown **only in this response** and redacted thereafter.
    """
    cfg = service.create_psp(data, base_url=settings.BASE_URL)
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
    service: AccountsService = Depends(get_service),
):
    service.delete_psp(psp_id)
