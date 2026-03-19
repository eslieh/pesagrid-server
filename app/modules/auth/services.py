import uuid
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import sqlalchemy
from datetime import datetime, timedelta
from typing import Optional, Tuple, List
import secrets
import hashlib
import time
import os 
from dotenv import load_dotenv
load_dotenv()
import logging

logger = logging.getLogger(__name__)

from .models import User, AuthToken, EventLog, AuthType
from .schema import RegisterRequest, LoginRequest, Token, UserSetupRequest
from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash as generate_hash # reuse if needed
)
from app.rabbitmq import BasePublisher, EventType, Priority

class AuthService:
    def __init__(self, db: Session):
        self.db = db

    async def setup_user(self, data: UserSetupRequest) -> User:
        """
        Internal method to find or create a user by email.
        Used for invitations where the user might not exist yet.
        """
        user = self.db.query(User).filter(User.email == data.email).first()
        
        if not user:
            # Create user with a random temporary password if not provided
            # They will likely reset it or use a magic link/invite token
            temp_pass = secrets.token_urlsafe(12)
            user = User(
                email=data.email,
                username=data.username,
                phone=data.phone,
                password_hash=get_password_hash(temp_pass),
                auth_type=AuthType(data.auth_type),
                verified=False
            )
            self.db.add(user)
            self.db.flush()
        
        return user

    async def get_user_by_username(self, username: str) -> Optional[User]:
        """
        Find user by username.
        """
        return self.db.query(User).filter(User.username.ilike(username)).first()
    
    async def get_user_by_identifier(self, identifier: str) -> Optional[User]:
        """
        Find user by email, phone or username.
        """
        return self.db.query(User).filter(
            (User.email == identifier) | 
            (User.phone.ilike(identifier)) | 
            (User.username.ilike(identifier))
        ).first()
    
    async def register_user(self, data: RegisterRequest) -> Tuple[User, str]:
        """Register a new user and generate verification token"""
        # Check existing user
        filters = [User.email == data.email]
        if data.phone:
            filters.append(User.phone == data.phone)
        if data.username:
            filters.append(User.username == data.username)
        
        existing = self.db.query(User).filter(
            sqlalchemy.or_(*filters)
        ).first()
        
        if existing:
            # If user exists with this email but is NOT verified, resend verification
            if existing.email == data.email:
                if not existing.verified:
                    # Delete old tokens for this user
                    self.db.query(AuthToken).filter(AuthToken.user_id == existing.id).delete()
                    self.db.flush()
                    
                    # Generate new verification token
                    token = self._generate_verification_token(existing.id)
                    self.db.commit()
                    self.db.refresh(existing)
                    
                    # Send verification email
                    email_data = {
                        "to": existing.email,
                        "subject": "Verify Your Account - SMS",
                        "body": f"Welcome back! Here's your verification token {token}",
                        "template": "notification.html",
                        "template_data": {
                            "title": "Verify Your Account",
                            "name": existing.email,
                            "body": (
                                f"We noticed you tried to register again. Your account isn't verified yet. "
                                f"Please verify your account using the button below to get started.<br><br>"
                            ),
                            "accent_color": "#000000",
                            "button": {
                                "text": "Verify Account",
                                "url": f"{os.getenv('CLIENT_URL')}/auth/verify?token={token}"
                            },
                            "secondary_text": "This link will expire in 24 hours. If you did not create this account, please ignore this email."
                        }
                    }
                    publisher = BasePublisher(service_name="auth-service")
                    await publisher.publish_event(
                        event_type=EventType.SEND_EMAIL,
                        payload=email_data
                    )
                    
                   # Return the existing user with new token instead of raising exception
                    return existing, token
                else:
                    raise HTTPException(400, "Email already registered")
            
            if data.phone and existing.phone == data.phone:
                raise HTTPException(400, "Phone already registered")
            if data.username and existing.username == data.username:
                raise HTTPException(400, "Username already registered")
            
        # Create user
        user = User(
            email=data.email,
            username=data.username,
            phone=data.phone,
            password_hash=get_password_hash(data.password),
            auth_type=AuthType(data.auth_type),
            verified=False
        )
        
        self.db.add(user)
        self.db.flush()
        
        # Generate verification token
        token = self._generate_verification_token(user.id)
        
        self.db.commit()
        self.db.refresh(user)

        
        # Publish welcome/verification notification
        email_data = {
            "to": user.email,
            "subject": "Welcome to SMS",
            "body": f"Welcome! Here's your verification token {token}",
            "template": "notification.html",
            "template_data": {
                "title": "Welcome to Copycat LMS!",
                "name": user.email,
                "body": (
                    f"We're excited to have you on board. Please verify your account using the button below to get started.<br><br>"
                ),
                "accent_color": "#000000",
                "button": {
                    "text": "Verify Account",
                    "url": f"{os.getenv('CLIENT_URL')}/auth/verify?token={token}"
                },
                "secondary_text": "This link will expire in 24 hours. If you did not create this account, please ignore this email."
            }
        }
        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.SEND_EMAIL,
            payload=email_data
        )

        await publisher.publish_event(
            event_type=EventType.USER_CREATED,
            payload={"user_id": str(user.id), "email": user.email}
        )
        
        return user, token
    
    async def verify_account(self, token: str) -> User:
        """Verify user account with token"""
        auth_token = self.db.query(AuthToken).filter(
            AuthToken.hash_tokens == self._hash_token(token)
        ).first()
        
        if not auth_token:
            raise HTTPException(400, "Invalid or expired token")
        
        # Check token expiry (24 hours)
        if datetime.utcnow() - auth_token.sent_at > timedelta(hours=24):
            raise HTTPException(400, "Token expired")
        
        user = auth_token.user
        user.verified = True
        user.verified_at = datetime.utcnow()
        
        # Delete used token
        self.db.delete(auth_token)
        self.db.commit()
        self.db.refresh(user)
        
        # Publish USER_VERIFIED event
        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.USER_VERIFIED,
            payload={"user_id": str(user.id), "email": user.email}
        )
        
        return user
    
    async def login(self, data: LoginRequest) -> Tuple[User, Token]:
        """Login user and generate tokens"""
        # Find user by email or phone
        user = self.db.query(User).filter(
            (User.email == data.identifier) | 
            (User.phone == data.identifier) |
            (User.username == data.identifier)
        ).filter(User.auth_type == AuthType(data.auth_type)).first()
        
        if not user:
            raise HTTPException(401, "Invalid credentials")
        
        # Verify password
        if not verify_password(data.password, user.password_hash):
            # Log failed attempt
            self._log_event("login_failed", user.id)
            self.db.commit()
            raise HTTPException(401, "Invalid credentials")
        
        # Check if verified
        if not user.verified:
            #delete old tokens for this user
            self.db.query(AuthToken).filter(AuthToken.user_id == user.id).delete()
            self.db.flush()

            #generate token and commit before sending email
            token = self._generate_verification_token(user.id)
            self.db.commit()
            self.db.refresh(user)
            
            # Publish welcome/verification notification
            email_data = {
                "to": user.email,
                "subject": "Welcome to Copycat LMS",
                "body": f"Welcome! Here's your verification token {token}",
                "template": "notification.html",
                "template_data": {
                    "title": "Welcome to Copycat LMS!",
                    "name": user.email,
                    "body": (
                        f"We're excited to have you on board. Please verify your account using the button below to get started.<br><br>"
                        f"Your verification token is: <strong>{token}</strong>"
                    ),
                    "accent_color": "#000000",
                    "button": {
                        "text": "Verify Account",
                        "url": f"{os.getenv('CLIENT_URL')}/auth/verify?token={token}"
                    },
                    "secondary_text": "This link will expire in 24 hours. If you did not create this account, please ignore this email."
                }
            }
            publisher = BasePublisher(service_name="auth-service")
            await publisher.publish_event(
                event_type=EventType.SEND_EMAIL,
                payload=email_data
            )
            # return user, token

            raise HTTPException(403, "Please verify your account first, we've sent another verification token to your email")
        
        # Generate tokens
        tokens = self._create_tokens(user)
        
        # Log successful login
        self._log_event("login_success", user.id)
        self.db.commit()
        
        return user, tokens
    
    async def resend_verification(self, email: str) -> None:
        """Resend verification email to unverified user"""
        user = self.db.query(User).filter(User.email == email).first()
        
        if not user:
            # Don't reveal if email exists for security
            return
        
        if user.verified:
            raise HTTPException(400, "Account already verified")
        
        # Delete old tokens for this user
        self.db.query(AuthToken).filter(AuthToken.user_id == user.id).delete()
        self.db.flush()
        
        # Generate new verification token
        token = self._generate_verification_token(user.id)
        self.db.commit()
        self.db.refresh(user)
        
        # Send verification email
        email_data = {
            "to": user.email,
            "subject": "Verify Your Account - SMS",
            "body": f"Here's your verification token {token}",
            "template": "notification.html",
            "template_data": {
                "title": "Verify Your Account",
                "name": user.email,
                "body": (
                    f"You requested a new verification link. "
                    f"Please verify your account using the button below to get started.<br><br>"
                ),
                "accent_color": "#000000",
                "button": {
                    "text": "Verify Account",
                    "url": f"{os.getenv('CLIENT_URL')}/auth/verify?token={token}"
                },
                "secondary_text": "This link will expire in 24 hours. If you did not request this, please ignore this email."
            }
        }
        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.SEND_EMAIL,
            payload=email_data
        )
        
        self._log_event("verification_resent", user.id)
        self.db.commit()
        
    async def logout(self, user_id: uuid.UUID, refresh_token: str) -> None:
        """Logout user by invalidating refresh token"""
        # In production, you'd store refresh tokens in DB or Redis
        # and delete them here. For now, just log the event
        self._log_event("logout", user_id)
        self.db.commit()
        
        # TODO: Implement token blacklisting if needed
        pass
    
    async def refresh_tokens(self, refresh_token: str) -> Token:
        """Generate new access token from refresh token"""
        payload = decode_token(refresh_token)
        
        if not payload or payload.get("type") != "refresh":
            raise HTTPException(401, "Invalid refresh token")
        
        user_id = payload.get("sub")
        user = self.db.query(User).filter(User.id == user_id).first()
        
        if not user or not user.verified:
            raise HTTPException(401, "User not found or not verified")
        
        # Add delay to ensure different timestamp in new token
        time.sleep(1)
        
        return self._create_tokens(user)
    
    async def change_password(
        self, 
        user_id: uuid.UUID, 
        old_password: str, 
        new_password: str
    ) -> None:
        """Change user password"""
        user = self.db.query(User).filter(User.id == user_id).first()
        
        if not verify_password(old_password, user.password_hash):
            raise HTTPException(400, "Incorrect current password")
        
        user.password_hash = get_password_hash(new_password)
        self._log_event("password_changed", user_id)
        self.db.commit()
    
    async def request_password_reset(self, email: str) -> str:
        """Generate password reset token"""
        user = self.db.query(User).filter(User.email == email).first()
        
        if not user:
            # Don't reveal if email exists
            return "If email exists, reset link sent"
        
        token = self._generate_verification_token(user.id)
        self._log_event("password_reset_requested", user.id)
        self.db.commit()

        # Publish password reset notification
        email_data = {
            "to": user.email,
            "subject": "Password Reset Request",
            "body": f"Your reset token is: {token}",
            "template": "notification.html",
            "template_data": {
                "title": "Reset Your Password",
                "name": user.email,
                "body": (
                    f"We received a request to reset your password for your Copycat LMS account. "
                    f"Click the button below to set a new password.<br><br>"
                    f"Your reset token is: <strong>{token}</strong>"
                ),
                "accent_color": "#D32F2F",
                "button": {
                    "text": "Reset Password",
                    "url": f"{os.getenv('CLIENT_URL')}/auth/reset-password?token={token}"
                },
                "secondary_text": "If you didn't request this, you can safely ignore this email. This link will expire in 1 hour."
            }
        }
        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.SEND_EMAIL,
            payload=email_data
        )
        
        return token
    
    async def reset_password(self, token: str, new_password: str) -> None:
        """Reset password using token"""
        auth_token = self.db.query(AuthToken).filter(
            AuthToken.hash_tokens == self._hash_token(token)
        ).first()
        
        if not auth_token:
            raise HTTPException(400, "Invalid or expired token")
        
        if datetime.utcnow() - auth_token.sent_at > timedelta(hours=1):
            raise HTTPException(400, "Token expired")
        
        user = auth_token.user
        user.password_hash = get_password_hash(new_password)
        
        self.db.delete(auth_token)
        self._log_event("password_reset_completed", user.id)
        self.db.commit()
    
    # Private helper methods
    def _generate_verification_token(self, user_id: uuid.UUID) -> str:
        """Generate and store verification token"""
        token = secrets.token_urlsafe(32)
        
        auth_token = AuthToken(
            user_id=user_id,
            hash_tokens=self._hash_token(token)
        )
        
        self.db.add(auth_token)
        return token
    
    def _hash_token(self, token: str) -> str:
        """Hash token for storage"""
        return hashlib.sha256(token.encode()).hexdigest()
    
    def _create_tokens(self, user: User) -> Token:
        """Create access and refresh tokens"""
        access_token = create_access_token(
            data={
                "sub": str(user.id),
                "email": user.email,
                "type": "access"
            }
        )
        refresh_token = create_refresh_token(
            data={"sub": str(user.id), "type": "refresh"}
        )
        
        return Token(
            access_token=access_token,
            refresh_token=refresh_token
        )

    def _log_event(self, event_name: str, user_id: uuid.UUID, to_user: Optional[uuid.UUID] = None):
        """Log authentication event"""
        event = EventLog(
            event_name=event_name,
            done_by_user_id=user_id,
            to_user_id=to_user
        )
        self.db.add(event)
