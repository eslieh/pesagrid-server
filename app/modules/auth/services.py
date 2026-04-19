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
import base64
import json
import httpx
from urllib.parse import urlencode
from dotenv import load_dotenv
load_dotenv()
import logging

logger = logging.getLogger(__name__)

from .models import User, AuthToken, EventLog, AuthType
from .schema import RegisterRequest, LoginRequest, Token, UserSetupRequest
from app.core.timezone import now_nairobi
from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash as generate_hash
)
from app.core.config import settings
from app.rabbitmq import BasePublisher, EventType, Priority
from app.modules.notifications.services.dispatcher import _build_from_email

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
    
    async def register_user(self, data: RegisterRequest) -> Tuple[User, str, Token]:
        """Register a new user and generate verification token + session tokens"""
        # Check existing user — only filter by fields that are actually provided
        filters = []
        if data.email:
            filters.append(User.email == data.email)
        if data.phone:
            filters.append(User.phone == data.phone)
        if data.username:
            filters.append(User.username == data.username)

        existing = None
        if filters:
            existing = self.db.query(User).filter(
                sqlalchemy.or_(*filters)
            ).first()
        
        if existing:
            # If user exists with this email but is NOT verified, resend verification
            if existing.email == data.email or existing.phone == data.phone or existing.username == data.username:
                if not existing.verified:
                    # Delete old tokens for this user
                    self.db.query(AuthToken).filter(AuthToken.user_id == existing.id).delete()
                    self.db.flush()
                    
                    # Generate new verification token
                    token = self._generate_verification_token(existing.id)
                    self.db.commit()
                    self.db.refresh(existing)

                    # Create session tokens (automatic login even if unverified)
                    session_tokens = self._create_tokens(existing)

                    # Publish AUTH_WELCOME event
                    publisher = BasePublisher(service_name="auth-service")
                    await publisher.publish_event(
                        event_type=EventType.AUTH_WELCOME,
                        payload={
                            "user_id": str(existing.id),
                            "email": existing.email,
                            "phone": existing.phone,
                            "auth_type": existing.auth_type.value,
                            "token": token
                        }
                    )
                    return existing, token, session_tokens
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
        logger.info(f"Verification token: {token}")

        # Create session tokens
        session_tokens = self._create_tokens(user)

        self.db.commit()
        self.db.refresh(user)

        # Publish AUTH_WELCOME event
        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.AUTH_WELCOME,
            payload={
                "user_id": str(user.id),
                "email": user.email,
                "phone": user.phone,
                "auth_type": user.auth_type.value,
                "token": token
            }
        )

        return user, token, session_tokens
    
    async def verify_account(self, token: str) -> Tuple[User, Token]:
        """Verify user account with token and return fresh session tokens"""
        
        auth_token = self.db.query(AuthToken).filter(
            AuthToken.hash_tokens == self._hash_token(token)
        ).first()
        
        if not auth_token:
            raise HTTPException(400, "Invalid or expired token")
        
        from app.core.timezone import make_aware
        # Check token expiry (24 hours)
        if now_nairobi() - make_aware(auth_token.sent_at) > timedelta(hours=24):
            raise HTTPException(400, "Token expired")
        
        user = auth_token.user
        user.verified = True
        user.verified_at = now_nairobi()
        
        # Delete used token
        self.db.delete(auth_token)
        
        # Create fresh session tokens
        session_tokens = self._create_tokens(user)

        self.db.commit()
        self.db.refresh(user)
        
        # Publish USER_VERIFIED event
        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.USER_VERIFIED,
            payload={"user_id": str(user.id), "email": user.email}
        )
        
        return user, session_tokens
    
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
            logger.info(f"Verification token: {token}")
            self.db.commit()
            self.db.refresh(user)

            # Publish AUTH_WELCOME event to resend verification
            publisher = BasePublisher(service_name="auth-service")
            await publisher.publish_event(
                event_type=EventType.AUTH_WELCOME,
                payload={
                    "user_id": str(user.id),
                    "email": user.email,
                    "phone": user.phone,
                    "auth_type": user.auth_type.value,
                    "token": token
                }
            )

            raise HTTPException(403, "Please verify your account first — we've sent another verification code")
        
        # Generate tokens
        tokens = self._create_tokens(user)
        
        # Log successful login
        self._log_event("login_success", user.id)
        self.db.commit()
        
        return user, tokens
    
    async def resend_verification(self, identifier: str) -> None:
        """Resend verification email to unverified user"""
        user = self.db.query(User).filter(
            (User.email == identifier) | (User.phone == identifier)
        ).first()
        
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
        logger.info(f"Verification token: {token}")
        self.db.commit()
        self.db.refresh(user)

        # Publish AUTH_WELCOME event to resend verification
        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.AUTH_WELCOME,
            payload={
                "user_id": str(user.id),
                "email": user.email,
                "phone": user.phone,
                "auth_type": user.auth_type.value,
                "token": token
            }
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
    
    async def request_password_reset(self, identifier: str) -> str:
        """Generate password reset token"""
        user = self.db.query(User).filter(
            (User.email == identifier) | (User.phone == identifier)
        ).first()
        
        if not user:
            # Don't reveal if email exists
            return "If email exists, reset link sent"
        
        token = self._generate_verification_token(user.id)
        self._log_event("password_reset_requested", user.id)
        self.db.commit()

        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.AUTH_PASSWORD_RESET,
            payload={
                "user_id": str(user.id),
                "email": user.email,
                "phone": user.phone,
                "auth_type": user.auth_type.value,
                "token": token
            }
        )

        return token
    
    async def request_mfa_token(self, user_id: uuid.UUID) -> None:
        """Generate and send an MFA verification code."""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(404, "User not found")
            
        token = self._generate_verification_token(user.id)
        self._log_event("mfa_requested", user.id)
        self.db.commit()

        publisher = BasePublisher(service_name="auth-service")
        await publisher.publish_event(
            event_type=EventType.AUTH_MFA_REQUEST,
            payload={
                "user_id": str(user.id),
                "email": user.email,
                "phone": user.phone,
                "auth_type": user.auth_type.value,
                "token": token
            }
        )

    async def reset_password(self, token: str, new_password: str) -> None:
        """Reset password using token"""
        auth_token = self.db.query(AuthToken).filter(
            AuthToken.hash_tokens == self._hash_token(token)
        ).first()
        
        if not auth_token:
            raise HTTPException(400, "Invalid or expired token")
        
        from app.core.timezone import make_aware
        
        if now_nairobi() - make_aware(auth_token.sent_at) > timedelta(hours=1):
            raise HTTPException(400, "Token expired")
        
        user = auth_token.user
        user.password_hash = get_password_hash(new_password)
        
        self.db.delete(auth_token)
        self._log_event("password_reset_completed", user.id)
        self.db.commit()
    
    # Private helper methods
    def _generate_verification_token(self, user_id: uuid.UUID) -> str:
        """Generate and store verification token"""
        import string
        token = "".join(secrets.choice(string.digits) for _ in range(6))
        
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

    # ------------------------------------------------------------------ #
    # Google OAuth helpers                                                 #
    # ------------------------------------------------------------------ #

    GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

    def build_google_auth_url(self, state: str) -> str:
        """
        Build the Google OAuth 2.0 consent-page URL.
        `state` should be 'login' for new sign-ins, or a JWT for account linking.
        """
        params = {
            "client_id": settings.GOOGLE_AUTH_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline",
            "prompt": "select_account",
            "state": state,
        }
        return f"{self.GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def _exchange_google_code(self, code: str) -> dict:
        """Exchange authorization code for Google tokens and return decoded user info."""
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                self.GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_AUTH_CLIENT_ID,
                    "client_secret": settings.GOOGLE_AUTH_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0,
            )

        if token_resp.status_code != 200:
            logger.error("Google token exchange failed: %s", token_resp.text)
            raise HTTPException(400, "Google authentication failed — could not exchange code")

        token_data = token_resp.json()
        id_token_raw = token_data.get("id_token")
        if not id_token_raw:
            raise HTTPException(400, "Google did not return an id_token")

        # Decode the JWT payload without signature verification
        # (Google already verified it; we trust the HTTPS response)
        try:
            payload_b64 = id_token_raw.split(".")[1]
            # Add padding if necessary
            payload_b64 += "=" * (-len(payload_b64) % 4)
            google_user = json.loads(base64.urlsafe_b64decode(payload_b64))
        except Exception as exc:
            logger.exception("Failed to decode Google id_token")
            raise HTTPException(400, "Invalid id_token from Google")

        return google_user  # keys: sub, email, name, picture, email_verified, ...

    async def handle_google_callback(
        self, code: str, state: str
    ) -> Tuple["User", Token, bool]:
        """
        Handle the OAuth callback for sign-in / sign-up.
        - Exchanges `code` for Google user info.
        - Upserts the user (creates if new, logs in if existing).
        - Returns the user, session tokens, and a boolean indicating if the user is new.
        """
        google_user = await self._exchange_google_code(code)

        google_id   = google_user.get("sub")
        email       = google_user.get("email")
        name        = google_user.get("name", "")
        verified_ok = google_user.get("email_verified", False)

        if not google_id or not email:
            raise HTTPException(400, "Incomplete user info returned from Google")

        # 1. Look up by google_id first (fastest path)
        user = self.db.query(User).filter(User.google_id == google_id).first()
        is_new_user = False

        if not user:
            # 2. Look up by email — might be an existing email/password account
            user = self.db.query(User).filter(User.email == email).first()
            if user:
                # Attach google_id to existing account and switch auth_type
                user.google_id = google_id
                if not user.verified and verified_ok:
                    user.verified = True
                    user.verified_at = now_nairobi()
                self.db.flush()
            else:
                # 3. Brand-new user — create account
                is_new_user = True
                user = User(
                    email=email,
                    username=None,
                    phone=None,
                    password_hash=None,  # No password for Google users
                    google_id=google_id,
                    auth_type=AuthType.GOOGLE,
                    verified=bool(verified_ok),
                    verified_at=now_nairobi() if verified_ok else None,
                )
                self.db.add(user)
                self.db.flush()

                # Publish welcome event for brand-new Google users
                publisher = BasePublisher(service_name="auth-service")
                await publisher.publish_event(
                    event_type=EventType.AUTH_WELCOME,
                    payload={
                        "user_id": str(user.id),
                        "email": user.email,
                        "phone": user.phone,
                        "auth_type": user.auth_type.value,
                        "token": None,  # No OTP needed for Google-verified accounts
                    }
                )

        tokens = self._create_tokens(user)
        self._log_event("google_login", user.id)
        self.db.commit()
        self.db.refresh(user)

        return user, tokens, is_new_user

    async def link_google_to_account(
        self, user_id: uuid.UUID, code: str
    ) -> dict:
        """
        Link a Google account to an already-authenticated user.
        Returns the Google email that was linked.
        """
        google_user = await self._exchange_google_code(code)

        google_id = google_user.get("sub")
        google_email = google_user.get("email")

        if not google_id:
            raise HTTPException(400, "Could not retrieve Google account info")

        # Check if this google_id is already linked to another account
        existing = self.db.query(User).filter(
            User.google_id == google_id,
            User.id != user_id
        ).first()
        if existing:
            raise HTTPException(409, "This Google account is already linked to another user")

        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(404, "User not found")

        user.google_id = google_id
        self._log_event("google_linked", user_id)
        self.db.commit()

        return {"google_email": google_email}
