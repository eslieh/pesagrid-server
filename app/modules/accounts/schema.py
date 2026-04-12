import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict
from app.modules.accounts.models import PSPType


class PSPConfigCreate(BaseModel):
    psp_type:     PSPType
    display_name: str
    paybill:      Optional[str]          = None
    till_number:  Optional[str]          = None
    business_key: Optional[str]          = None
    account_no:   Optional[str]          = None
    credentials:  Optional[Dict[str, Any]] = None  # consumer_key, consumer_secret, passkey
    meta:         Optional[Dict[str, Any]] = None


class PSPConfigUpdate(BaseModel):
    display_name: Optional[str]            = None
    paybill:      Optional[str]            = None
    till_number:  Optional[str]            = None
    business_key: Optional[str]            = None
    account_no:   Optional[str]            = None
    credentials:  Optional[Dict[str, Any]] = None
    meta:         Optional[Dict[str, Any]] = None
    is_active:    Optional[bool]           = None


class PSPConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            uuid.UUID
    collection_id: uuid.UUID
    psp_type:      PSPType
    display_name:  str
    paybill:       Optional[str]
    till_number:   Optional[str]
    business_key:  Optional[str]
    account_no:    Optional[str]
    webhook_url:   str
    meta:          Optional[Dict[str, Any]]
    is_active:     bool
    created_by:    uuid.UUID
    created_at:    datetime
    updated_at:    datetime
    # NOTE: credentials intentionally omitted — never returned after creation


class PSPConfigCreateResponse(PSPConfigResponse):
    """Returned only on creation — includes credentials so business can verify."""
    credentials: Optional[Dict[str, Any]] = None


class PSPConfigListResponse(BaseModel):
    total: int
    items: List[PSPConfigResponse]
