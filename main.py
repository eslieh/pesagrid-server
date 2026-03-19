from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.core.cache import cache
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    await cache.connect()
    logger.info("✅ Connected to Redis")
    yield
    # Shutdown
    await cache.disconnect()
    logger.info("👋 Disconnected from Redis")

app = FastAPI(
    title="Auth Service API",
    description="Authentication and Authorization Service",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://172.16.20.207",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# # Include routers
# from app.api.v1.routers import auth_router, permission_router, role_router, user_router, auto_onboarding_router
# from app.api.v1.internal import internal_router

# app.include_router(auth_router.router_auth, prefix="/api/v1", tags=["Authentication"])
# app.include_router(permission_router.router_perm, prefix="/api/v1/permissions", tags=["Permission"])
# app.include_router(role_router.router_role, prefix="/api/v1/roles", tags=["Role"])
# app.include_router(user_router.router_user, prefix="/api/v1/users", tags=["User"])
# app.include_router(internal_router.router_internal, prefix="/api/v1", tags=["Internal"])
# app.include_router(auto_onboarding_router.router, prefix="/api/v1")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    redis_status = "connected" if cache.client and await cache.client.ping() else "disconnected"
    return {"status": "healthy", "service": "auth-service", "redis": redis_status}

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "app": "PesaGrid App",
        "version": "1.0.0",
        "docs": "/docs"
    }