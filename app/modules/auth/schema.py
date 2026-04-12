from enum import Enum
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List
from datetime import date, datetime
import uuid

from .models import UserResponse
from app.core.phone_utils import normalise_phone, InvalidPhoneNumberError

# Registration & Login
class RegisterRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    password: str = Field(..., min_length=8, max_length=100)
    auth_type: str

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v):
        if not v:
            return v
        try:
            return normalise_phone(str(v))
        except InvalidPhoneNumberError as e:
            raise ValueError(str(e))

class UserSetupRequest(BaseModel):
    email: EmailStr
    username: Optional[str] = None
    phone: Optional[str] = None
    auth_type: str = "email"

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, v):
        if not v:
            return v
        try:
            return normalise_phone(str(v))
        except InvalidPhoneNumberError as e:
            raise ValueError(str(e))

class LoginRequest(BaseModel):
    identifier: str = Field(..., description="Email, phone or username")
    password: str
    auth_type: str

class VerifyAccountRequest(BaseModel):
    token: str
    
class ResendVerificationRequest(BaseModel):
    identifier: str = Field(..., description="Email or phone")

# Password Management
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8, max_length=100)

class ForgotPasswordRequest(BaseModel):
    identifier: str = Field(..., description="Email or phone")

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=100)

# Token Management
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class AuthResponse(BaseModel):
    user: UserResponse
    tokens: Optional[Token] = None

class LogoutResponse(BaseModel):
    message: str = "Successfully logged out"


class RefreshTokenRequest(BaseModel):
    refresh_token: Optional[str] = None


class RefreshResponse(BaseModel):
    message: str = "Tokens refreshed successfully"
    