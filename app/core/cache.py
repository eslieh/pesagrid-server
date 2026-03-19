import redis.asyncio as redis
from typing import Optional
import json
from app.core.config import settings

class RedisCache:
    """Redis cache for token blacklist and rate limiting"""
    
    def __init__(self, redis_url: str = settings.REDIS_URL):
        self.redis_url = redis_url
        self.client: Optional[redis.Redis] = None
    
    async def connect(self):
        """Connect to Redis"""
        self.client = await redis.from_url(
            self.redis_url,
            encoding="utf-8",
            decode_responses=True
        )
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self.client:
            await self.client.close()
    
    async def blacklist_token(self, token: str, expires_in: int):
        """Add token to blacklist"""
        await self.client.setex(
            f"blacklist:{token}",
            expires_in,
            "1"
        )
    
    async def is_token_blacklisted(self, token: str) -> bool:
        """Check if token is blacklisted"""
        result = await self.client.get(f"blacklist:{token}")
        return result is not None
    
    async def cache_user_permissions(
        self, 
        user_id: str, 
        permissions: list,
        ttl: int = 300
    ):
        """Cache user permissions"""
        await self.client.setex(
            f"perms:{user_id}",
            ttl,
            json.dumps(permissions)
        )
    
    async def get_cached_permissions(self, user_id: str) -> Optional[list]:
        """Get cached user permissions"""
        result = await self.client.get(f"perms:{user_id}")
        if result:
            return json.loads(result)
        return None

# Global cache instance
cache = RedisCache()