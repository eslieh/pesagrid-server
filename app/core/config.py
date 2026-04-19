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
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")

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

    # Notifications — Resend (email)
    RESEND_API_KEY:   str = os.getenv("RESEND_API_KEY", "")
    RESEND_FROM_EMAIL: str = os.getenv("RESEND_FROM_EMAIL", "pesagrid@mails.ryfty.net")

    # Notifications — Hostpinnacle (SMS)
    SMS_USER_ID:   str = os.getenv("SMS_USER_ID", "")
    SMS_PASSWORD:  str = os.getenv("SMS_PASSWORD", "")
    SMS_API_KEY:   str = os.getenv("SMS_API_KEY", "")
    SMS_SENDER_ID: str = os.getenv("SMS_SENDER_ID", "Intacom")

    # Paystack payment gateway
    PAYSTACK_SECRET_KEY:  str = os.getenv("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_PUBLIC_KEY:  str = os.getenv("PAYSTACK_PUBLIC_KEY", "")
    PAYSTACK_BASE_URL:    str = "https://api.paystack.co"

    # Google OAuth
    GOOGLE_AUTH_CLIENT_ID:     str = os.getenv("GOOGLE_AUTH_CLIENT_ID", "")
    GOOGLE_AUTH_CLIENT_SECRET: str = os.getenv("GOOGLE_AUTH_CLIENT_SECRET", "")

    @property
    def GOOGLE_REDIRECT_URI(self) -> str:
        """Canonical redirect URI registered in Google Cloud Console"""
        return f"{self.BASE_URL}/api/v1/auth/google/authorized"

    # Cookie Configuration
    COOKIE_DOMAIN: Optional[str] = None  # None for same-origin
    COOKIE_SECURE: bool = True  # Require HTTPS but of for now
    COOKIE_SAMESITE: Optional[str] = "none"  # CSRF protection

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
