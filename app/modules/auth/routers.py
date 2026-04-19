from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .services import AuthService
from app.core.dependancies import get_current_user, get_db
from .models import User, UserResponse, GoogleLinkResponse
from .schema import AuthResponse, ChangePasswordRequest, ForgotPasswordRequest, LoginRequest, LogoutResponse, RefreshResponse, RefreshTokenRequest, RegisterRequest, ResetPasswordRequest, Token, VerifyAccountRequest, ResendVerificationRequest
from .services import AuthService as auth_service
from app.core.security import COOKIE_ACCESS_TOKEN_NAME, COOKIE_REFRESH_TOKEN_NAME
from app.core.config import settings

auth_router = APIRouter(tags=["Authentication"])

def _set_auth_cookies(response: Response, tokens: Token):
    """Helper to set secure tokens in cookies for web clients"""
    response.set_cookie(
        key=COOKIE_ACCESS_TOKEN_NAME,
        value=tokens.access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        domain=settings.COOKIE_DOMAIN
    )
    
    response.set_cookie(
        key=COOKIE_REFRESH_TOKEN_NAME,
        value=tokens.refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        domain=settings.COOKIE_DOMAIN
    )

@auth_router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    data: RegisterRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Register new user account and log them in automatically"""
    service = AuthService(db)
    user, token, session_tokens = await service.register_user(data)
    
    # Set cookies for seamless onboarding
    _set_auth_cookies(response, session_tokens)
    
    # Send verification email in background
    # background_tasks.add_task(send_verification_email, user.email, token)
    
    return AuthResponse(user=user, tokens=session_tokens)

@auth_router.post("/verify", response_model=AuthResponse)
async def verify_account(
    data: VerifyAccountRequest,
    response: Response,
    db: Session = Depends(get_db)
):
    """Verify user account and refresh session tokens"""
    service = AuthService(db)
    user, tokens = await service.verify_account(data.token)
    
    # Refresh cookies after successful verification
    _set_auth_cookies(response, tokens)
    
    return AuthResponse(user=user, tokens=tokens)

# new endpoint
@auth_router.post("/resend-verification")
async def resend_verification(
    data: ResendVerificationRequest,
    db: Session = Depends(get_db)
):
    """Resend verification email to unverified user"""
    service = AuthService(db)
    await service.resend_verification(data.identifier)
    return {"message": "If the account exists and is unverified, a new verification link has been sent"}

@auth_router.post("/login", response_model=AuthResponse)
async def login(
    data: LoginRequest,
    response: Response,
    db: Session = Depends(get_db)
):
    """Login to get access tokens"""
    service = AuthService(db)
    user, tokens = await service.login(data)
    
    # Set HttpOnly cookies for web clients
    _set_auth_cookies(response, tokens)
    
    return AuthResponse(
        user=user,
        tokens=tokens
    )


@auth_router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    data: Optional[RefreshTokenRequest] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Logout and invalidate tokens"""
    service = AuthService(db)
    
    # Get refresh token from request body or cookie
    refresh_token = data.refresh_token if data else None
    if not refresh_token:
        refresh_token = request.cookies.get(COOKIE_REFRESH_TOKEN_NAME)
        
    if refresh_token:
        await service.logout(current_user.id, refresh_token)
    
    # Clear cookies
    response.delete_cookie(
        key=COOKIE_ACCESS_TOKEN_NAME,
        domain=settings.COOKIE_DOMAIN,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE
    )
    
    response.delete_cookie(
        key=COOKIE_REFRESH_TOKEN_NAME,
        domain=settings.COOKIE_DOMAIN,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE
    )
    
    return LogoutResponse()

@auth_router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    data: Optional[RefreshTokenRequest] = None,
    db: Session = Depends(get_db)
):
    """Refresh access token"""
    service = AuthService(db)
    
    # Get refresh token from request body or cookie
    refresh_token = data.refresh_token if data else None
    if not refresh_token:
        # Try to get from cookie
        refresh_token = request.cookies.get(COOKIE_REFRESH_TOKEN_NAME)
    
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required"
        )
    
    tokens = await service.refresh_tokens(refresh_token)
    
    # Set new cookies for web clients
    _set_auth_cookies(response, tokens)
    
    return RefreshResponse()

@auth_router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Change user password"""
    service = AuthService(db)
    await service.change_password(
        current_user.id, 
        data.old_password, 
        data.new_password
    )
    return {"message": "Password changed successfully"}

@auth_router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Request password reset"""
    service = AuthService(db)
    token = await service.request_password_reset(data.identifier)
    
    # Send reset email in background
    # background_tasks.add_task(send_password_reset_email, data.identifier, token)
    
    return {"message": "If account exists, reset link sent"}

@auth_router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest,
    db: Session = Depends(get_db)
):
    """Reset password with token"""
    service = AuthService(db)
    await service.reset_password(data.token, data.new_password)
    return {"message": "Password reset successful"}

@auth_router.post("/mfa/request")
async def request_mfa(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Request a Two-Step Verification (MFA) token for sensitive actions."""
    # Delete old tokens for clean slate
    from app.modules.auth.models import AuthToken
    db.query(AuthToken).filter(AuthToken.user_id == current_user.id).delete()
    db.flush()
    
    service = AuthService(db)
    await service.request_mfa_token(current_user.id)
    return {"message": "Verification code sent successfully"}

@auth_router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """Get current user information"""
    return current_user


# ------------------------------------------------------------------ #
# Google OAuth endpoints                                               #
# ------------------------------------------------------------------ #

@auth_router.get("/google", include_in_schema=True)
async def google_login():
    """
    Initiate Google OAuth 2.0 sign-in / sign-up flow.
    Redirects the browser to Google's consent screen.
    After approval, Google redirects to /api/v1/auth/google/authorized.
    """
    service = AuthService.__new__(AuthService)  # no db needed for URL building
    url = service.build_google_auth_url(state="login")
    return RedirectResponse(url=url, status_code=302)


@auth_router.get("/google/authorized", include_in_schema=True)
async def google_authorized(
    request: Request,
    response: Response,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Google OAuth 2.0 callback endpoint.
    Exchanges the authorization code for user info, upserts the user,
    sets session cookies, and redirects to the frontend dashboard.
    """
    if error:
        # User denied access or something went wrong on Google's side
        return RedirectResponse(
            url=f"{settings.CLIENT_URL}/auth/login?error=google_denied",
            status_code=302
        )

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code from Google")

    # If state begins with a JWT-like string we treat it as an account-link callback
    # (handled separately via /google/link → /google/authorized)
    # For plain login the state is just 'login'
    service = AuthService(db)

    if state and state != "login" and len(state) > 20:
        # Account-linking callback: extract user from the state JWT
        from app.core.security import decode_token
        payload = decode_token(state)
        if not payload or payload.get("type") != "access":
            return RedirectResponse(
                url=f"{settings.CLIENT_URL}/auth/login?error=invalid_state",
                status_code=302
            )
        user_id = payload.get("sub")
        result = await service.link_google_to_account(user_id, code)
        # Redirect to profile/settings page after successful link
        return RedirectResponse(
            url=f"{settings.CLIENT_URL}/dashboard/settings?google_linked=true",
            status_code=302
        )

    # Standard login / sign-up
    user, tokens, is_new_user = await service.handle_google_callback(code=code, state=state or "login")

    # Build redirect response and embed session cookies
    # Redirect to onboarding if they are a brand new user, otherwise dashboard
    redirect_dest = f"{settings.CLIENT_URL}/onboarding" if is_new_user else f"{settings.CLIENT_URL}/dashboard"
    
    redirect = RedirectResponse(
        url=redirect_dest,
        status_code=302,
    )
    redirect.set_cookie(
        key=COOKIE_ACCESS_TOKEN_NAME,
        value=tokens.access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        domain=settings.COOKIE_DOMAIN,
    )
    redirect.set_cookie(
        key=COOKIE_REFRESH_TOKEN_NAME,
        value=tokens.refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        domain=settings.COOKIE_DOMAIN,
    )
    return redirect


@auth_router.get("/google/link", response_model=None)
async def google_link(
    current_user: User = Depends(get_current_user),
):
    """
    Initiate Google account-linking for an already authenticated user.
    Passes the user's access token as OAuth `state` so the callback
    can identify which account to link.
    """
    from app.core.security import create_access_token
    # Create a short-lived token to use as OAuth state
    link_state = create_access_token(
        data={"sub": str(current_user.id), "email": current_user.email, "type": "access"}
    )
    service = AuthService.__new__(AuthService)
    url = service.build_google_auth_url(state=link_state)
    return RedirectResponse(url=url, status_code=302)
