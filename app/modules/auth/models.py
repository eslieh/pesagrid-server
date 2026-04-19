from datetime import datetime
from typing import Optional, List
from enum import Enum
from sqlalchemy import Boolean, Column, DateTime, String, ForeignKey, Text, Enum as SQLEnum, Index
from app.core.db_types import UUID, uuid4
from sqlalchemy.orm import relationship
from pydantic import BaseModel, EmailStr, ConfigDict
from app.core.base import Base
import uuid
from app.core.timezone import now_nairobi

# Enums
class AuthType(str, Enum):
    EMAIL = "email"
    USERNAME = "username"
    GOOGLE = "google"
    FACEBOOK = "facebook"
    PHONE = "phone"


class UserRoleStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


# SQLAlchemy Models
class User(Base):
    __tablename__ = "users"

    id = Column(UUID, primary_key=True, default=uuid4)
    email = Column(Text, unique=True, nullable=True, index=True)
    password_hash = Column(Text, nullable=True)
    username = Column(Text, unique=True, nullable=True, index=True)
    phone = Column(Text, nullable=True, index=True)
    google_id = Column(Text, unique=True, nullable=True, index=True)  # Google OAuth subject ID
    verified = Column(Boolean, default=False, index=True)
    auth_type = Column(SQLEnum(AuthType), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=now_nairobi, index=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    auth_tokens = relationship("AuthToken", back_populates="user", cascade="all, delete-orphan")
    __table_args__ = (
        Index('idx_users_email_verified', 'email', 'verified'),
        Index('idx_users_auth_type_verified', 'auth_type', 'verified'),
        Index('idx_users_created_at_desc', 'created_at', postgresql_using='btree'),
        Index('idx_users_google_id', 'google_id', postgresql_using='hash'),
        {"schema": "auth"},
    )

class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id = Column(UUID, primary_key=True, default=uuid4)
    user_id = Column(UUID, ForeignKey("auth.users.id"), nullable=False, index=True)
    hash_tokens = Column(Text, nullable=False, index=True)
    sent_at = Column(DateTime(timezone=True), default=now_nairobi, index=True)

    # Relationships
    user = relationship("User", back_populates="auth_tokens")

    __table_args__ = (
        Index('idx_auth_tokens_user_sent_at', 'user_id', 'sent_at'),
        Index('idx_auth_tokens_hash_lookup', 'hash_tokens', postgresql_using='hash'),
        {"schema": "auth"},
    )

class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(UUID, primary_key=True, default=uuid4)
    event_name = Column(Text, nullable=False, index=True)
    done_by_user_id = Column(UUID, nullable=False, index=True)
    to_user_id = Column(UUID, nullable=True, index=True)
    event_description = Column(Text, nullable=True)
    done_at = Column(DateTime(timezone=True), default=now_nairobi, index=True)

    # Relationships are removed to decouple from User deletion

    __table_args__ = (
        Index('idx_event_log_done_by_user_done_at', 'done_by_user_id', 'done_at'),
        Index('idx_event_log_to_user_done_at', 'to_user_id', 'done_at'),
        Index('idx_event_log_event_name_done_at', 'event_name', 'done_at'),
        Index('idx_event_log_done_at_desc', 'done_at', postgresql_using='btree'),
        {"schema": "auth"},
    )


# Pydantic Schemas
class UserBase(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    auth_type: AuthType


class UserCreate(UserBase):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    auth_type: AuthType
    password: Optional[str] = None


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    verified: bool
    created_at: datetime
    verified_at: Optional[datetime] = None
    google_id: Optional[str] = None


class GoogleLinkResponse(BaseModel):
    message: str
    google_email: Optional[str] = None


class AuthTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    user_id: uuid.UUID
    sent_at: datetime


class RoleBase(BaseModel):
    role_name: str
    description: Optional[str] = None


class RoleCreate(RoleBase):
    pass


class RoleResponse(RoleBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID


class UserRoleBase(BaseModel):
    user_id: uuid.UUID
    role_id: uuid.UUID
    status: UserRoleStatus
    school_id: Optional[uuid.UUID] = None


class UserRoleCreate(UserRoleBase):
    pass


class UserRoleResponse(UserRoleBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID


class PermissionBase(BaseModel):
    permission: str
    description: Optional[str] = None


class PermissionCreate(PermissionBase):
    pass


class PermissionResponse(PermissionBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    created_at: datetime


class UserPermissionBase(BaseModel):
    user_id: uuid.UUID
    permission_id: uuid.UUID


class UserPermissionCreate(UserPermissionBase):
    pass


class UserPermissionResponse(UserPermissionBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PermissionRequestBase(BaseModel):
    user_id: uuid.UUID
    permission_id: uuid.UUID
    description: Optional[str] = None


class PermissionRequestCreate(PermissionRequestBase):
    pass


class PermissionRequestResponse(PermissionRequestBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    approved: bool
    approved_by: Optional[uuid.UUID] = None


class EventLogBase(BaseModel):
    event_name: str
    done_by_user_id: uuid.UUID
    to_user_id: Optional[uuid.UUID] = None
    event_description: Optional[str] = None


class EventLogCreate(EventLogBase):
    pass


class EventLogResponse(EventLogBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: uuid.UUID
    done_at: datetime