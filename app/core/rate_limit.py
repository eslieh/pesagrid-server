from os import stat
from fastapi import Request, HTTPException
from datetime import datetime, timedelta
from typing import Dict
import asyncio
from app.core.config import settings

class RateLimiter:
    """Simple in-memory rate limiter"""
    
    def __init__(self):
        self.requests: Dict[str, list] = {}
        self.lock = asyncio.Lock()
    
    async def check_rate_limit(
        self, 
        key: str, 
        max_requests: int = 5, 
        window_seconds: int = 300
    ) -> bool:
        """
        Check if request is within rate limit
        
        Args:
            key: Identifier (IP, user_id, email, etc.)
            max_requests: Maximum requests allowed
            window_seconds: Time window in seconds
        
        Returns:
            True if allowed, raises HTTPException if rate limited
        """
        async with self.lock:
            now = datetime.utcnow()
            window_start = now - timedelta(seconds=window_seconds)
            
            # Initialize or clean old requests
            if key not in self.requests:
                self.requests[key] = []
            
            # Remove requests outside window
            self.requests[key] = [
                req_time for req_time in self.requests[key]
                if req_time > window_start
            ]
            
            # Check limit
            if len(self.requests[key]) >= max_requests:
                raise HTTPException(
                    status_code=stat.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again later."
                )
            
            # Add current request
            self.requests[key].append(now)
            return True

# Global rate limiter instance
rate_limiter = RateLimiter()

async def rate_limit_login(request: Request):
    """Rate limit login attempts by IP"""
    client_ip = request.client.host
    await rate_limiter.check_rate_limit(
        f"login:{client_ip}",
        max_requests=settings.RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS
    )

async def rate_limit_register(request: Request):
    """Rate limit registration by IP"""
    client_ip = request.client.host
    await rate_limiter.check_rate_limit(
        f"register:{client_ip}",
        max_requests=3,
        window_seconds=3600  # 3 attempts per hour
    )
