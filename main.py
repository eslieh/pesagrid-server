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


# Include routers
from app.modules.auth.routers import auth_router
from app.modules.obligations.routers import obligations_router
from app.modules.accounts.routers import accounts_router
from app.modules.ingestion.routers import webhook_router, transactions_router, collection_points_router
from app.modules.notifications.routers import notifications_router
from app.modules.dashboard.routers import dashboard_router

app.include_router(auth_router,          prefix="/api/v1/auth",         tags=["Authentication"])
app.include_router(obligations_router,   prefix="/api/v1/obligations",   tags=["Obligations"])
app.include_router(accounts_router,      prefix="/api/v1/accounts",      tags=["Accounts / PSP Settings"])
app.include_router(dashboard_router,     prefix="/api/v1/dashboard",     tags=["Dashboard"])
app.include_router(webhook_router,       prefix="/api/v1/ingest",        tags=["Webhooks"])
app.include_router(transactions_router,  prefix="/api/v1/transactions",  tags=["Transactions"])
app.include_router(collection_points_router, prefix="/api/v1/collection-points", tags=["Collection Points"])
app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["Notifications"])

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