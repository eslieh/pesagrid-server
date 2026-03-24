import pytest
import uuid
import asyncio
from typing import Generator
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock

from app.core.base import Base
from app.core.dependancies import get_db, get_current_user
from main import app as fastapi_app


from app.modules.auth.models import User, AuthType
from app.modules.ingestion.models import Transaction, CollectionPoint
from app.modules.obligations.models import Obligation, Payer, PayerGroup
from app.modules.accounts.models import PSPConfig, BusinessProfile
from app.rabbitmq.publisher import BasePublisher

# Patch the global SessionLocal before other imports use it
import app.core.dependancies



from sqlalchemy import event, Table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


# Use SQLite in-memory for fast testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app.core.dependancies.SessionLocal = TestingSessionLocal


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

@pytest.fixture(scope="session")
def db_engine():
    # Hack: Strip schemas from all tables in metadata for SQLite compatibility
    for table in Base.metadata.tables.values():
        table.schema = None
        
    # Create all tables
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)



@pytest.fixture
def db(db_engine) -> Generator:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture
def client(db: Session, mock_publisher, test_collection_id) -> Generator:

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_current_user():
        # Create a test user in the DB with the target collection_id
        user = db.query(User).filter(User.id == test_collection_id).first()
        if not user:
            user = User(
                id=test_collection_id,
                email=f"test_{test_collection_id}@example.com",
                username=f"testuser_{test_collection_id}",

                auth_type=AuthType.EMAIL,
                verified=True
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user


    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_user] = override_get_current_user
    
    with TestClient(fastapi_app) as c:
        yield c
    
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def mock_publisher(monkeypatch):
    """Mock the RabbitMQ publisher to avoid network calls and track events."""
    mock = MagicMock(spec=BasePublisher)
    mock.publish = AsyncMock()
    mock.publish_event = AsyncMock()
    
    # 1. Patch the class itself so ANY instantiation returns our mock
    monkeypatch.setattr("app.rabbitmq.publisher.BasePublisher", MagicMock(return_value=mock))
    
    # 2. Patch already-instantiated instances at module level
    import app.modules.ingestion.services
    monkeypatch.setattr(app.modules.ingestion.services, "publisher", mock)
    
    return mock


@pytest.fixture
def test_collection_id():
    return uuid.uuid4()

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer mock-token"}
