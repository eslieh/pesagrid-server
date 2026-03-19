from enum import Enum
from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, List
from datetime import date, datetime
import uuid

from .models import UserResponse

# Registration & Login
class RegisterRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    password: str = Field(..., min_length=8, max_length=100)
    auth_type: str
    
    # @validator('email')
    # def email_lowercase(cls, v):
    #     return v.lower()

class UserSetupRequest(BaseModel):
    email: EmailStr
    username: Optional[str] = None
    phone: Optional[str] = None
    auth_type: str = "email"

class LoginRequest(BaseModel):
    identifier: str = Field(..., description="Email, phone or username")
    password: str
    auth_type: str

class VerifyAccountRequest(BaseModel):
    token: str
    
class ResendVerificationRequest(BaseModel):
    email: EmailStr

# Password Management
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8, max_length=100)

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

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

class LogoutResponse(BaseModel):
    message: str = "Successfully logged out"


class RefreshTokenRequest(BaseModel):
    refresh_token: Optional[str] = None


class RefreshResponse(BaseModel):
    message: str = "Tokens refreshed successfully"
    