from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

EXCHANGE_NAME = "pesagrid.events"

class EventType(str, Enum):
    # Notification Events
    SEND_EMAIL = "notification.email.send"
    SEND_SMS = "notification.sms.send"
    SEND_PUSH = "notification.push.send"

class Priority(int, Enum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3

class MessageEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: Dict[str, Any]
    source_service: str
    target_service: Optional[str] = None
    priority: Priority = Priority.MEDIUM
