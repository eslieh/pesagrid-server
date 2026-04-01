from fastapi import Depends, HTTPException, status, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Generator, Optional, List, Union, Tuple, Literal
from sqlalchemy import create_engine
import uuid
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.core.security import decode_token, COOKIE_ACCESS_TOKEN_NAME, COOKIE_REFRESH_TOKEN_NAME
from app.modules.auth.models import User

# Database setup
engine = create_engine(settings.DATABASE_URL, **settings.SQLALCHEMY_ENGINE_OPTIONS)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

security = HTTPBearer(auto_error=False)


def get_db() -> Generator:
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_token_from_request(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Tuple[Optional[str], Literal["cookie", "header", "none"]]:
    """
    Extract authentication token from request.
    Checks cookies first (for web clients), then Authorization header (for S2S).
    Returns tuple of (token, source).
    """
    # Check for token in cookies first (web clients)
    access_token = request.cookies.get(COOKIE_ACCESS_TOKEN_NAME)
    if access_token:
        return (access_token, "cookie")
    
    # Fall back to Authorization header (S2S)
    if credentials:
        return (credentials.credentials, "header")
    
    return (None, "none")


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token (cookie or header)"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Get token from cookie or header
    token, source = get_token_from_request(request, credentials)
    
    if not token:
        raise credentials_exception
    
    payload = decode_token(token)
    
    if payload is None:
        raise credentials_exception
    
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type"
        )
    
    # Get user_id from token payload
    user_id: str = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    
    # Query user by ID (or email if that's what you're storing in 'sub')
    user = db.query(User).filter(User.id == user_id).first()
    
    # Alternative: if 'sub' contains email instead of user_id
    # user = db.query(User).filter(User.email == user_id).first()
    
    if user is None:
        raise credentials_exception
    
    return user


def get_current_verified_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Ensure the current user is verified"""
    if not current_user.verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User email/phone not verified"
        )
    return current_user


def get_current_active_verified_user(
    current_user: User = Depends(get_current_verified_user)
) -> User:
    """Alias for get_current_verified_user (for consistency with your original code)"""
    return current_user

def get_optional_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Get current user if authenticated, otherwise return None (for optional auth)"""
    # Get token from cookie or header
    token, source = get_token_from_request(request, credentials)
    
    if not token:
        return None
    
    try:
        payload = decode_token(token)
        
        if payload is None or payload.get("type") != "access":
            return None
        
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
        
        user = db.query(User).filter(User.id == user_id).first()
        return user
    except Exception:
        return None

def verify_mfa(
    x_mfa_token: Optional[str] = Header(None, alias="X-MFA-Token"),
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db)
) -> User:
    """Ensure the user provided a valid MFA code for a sensitive action."""
    if not x_mfa_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA token required for this action")
        
    import hashlib
    from app.modules.auth.models import AuthToken
    from app.core.timezone import now_nairobi, make_aware
    from datetime import timedelta
    
    hashed_token = hashlib.sha256(x_mfa_token.encode()).hexdigest()
    
    # Check if there is a matching token
    auth_token = db.query(AuthToken).filter(
        AuthToken.user_id == current_user.id,
        AuthToken.hash_tokens == hashed_token
    ).first()
    
    if not auth_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid MFA token")
        
    # Tokens for actions expire quickly (e.g. 15 minutes)
    if now_nairobi() - make_aware(auth_token.sent_at) > timedelta(minutes=15):
        db.delete(auth_token)
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA token expired")
        
    # Consume token so it can't be reused
    db.delete(auth_token)
    db.commit()
    
    return current_user