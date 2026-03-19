from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional
import os
from dotenv import load_dotenv
load_dotenv()

class Settings(BaseSettings):
    # App
    PROJECT_NAME: str = "PesaGrid Appp"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"

    # Security / JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str

    SQLALCHEMY_ENGINE_OPTIONS: dict = {
        "pool_size": 20,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
    }

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = ["*"]

    # Redis cache
    CACHE_TYPE: str = "RedisCache"
    REDIS_URL: str = os.getenv("REDIS_URL")
    CACHE_DEFAULT_TIMEOUT: int = 300

    # Rate limiting (example)
    RATE_LIMIT_MAX_REQUESTS: int = 5
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # RabbitMQ
    RABBITMQ_CONNECTION_STRING: str = os.getenv("RABBITMQ_CONNECTION_STRING")

    CLIENT_URL: str = os.getenv("CLIENT_URL")

    # Cookie Configuration
    COOKIE_DOMAIN: Optional[str] = None  # None for same-origin
    COOKIE_SECURE: bool = False  # Require HTTPS but of for now
    COOKIE_SAMESITE: Optional[str] = None  # CSRF protection

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
