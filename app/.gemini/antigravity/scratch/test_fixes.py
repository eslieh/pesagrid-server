import asyncio
import uuid
import unittest
from unittest.mock import MagicMock, AsyncMock

# Mocking parts of the app to test BaseConsumer
import sys
from types import ModuleType

# Create dummy modules to satisfy imports
app_core = ModuleType("app.core")
sys.modules["app.core"] = app_core
app_core_config = ModuleType("app.core.config")
sys.modules["app.core.config"] = app_core_config
app_core_config.settings = MagicMock()

app_rabbitmq = ModuleType("app.rabbitmq")
sys.modules["app.rabbitmq"] = app_rabbitmq

from app.rabbitmq.consumer import BaseConsumer
from app.rabbitmq.types import MessageEnvelope, EventType

class TestRabbitMQConsumer(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_handlers(self):
        consumer = BaseConsumer("test-service")
        
        handler1_called = False
        handler2_called = False
        
        async def handler1(envelope):
            nonlocal handler1_called
            handler1_called = True
            
        async def handler2(envelope):
            nonlocal handler2_called
            handler2_called = True
            
        consumer.register_handler(EventType.OBLIGATION_CREATED, handler1)
        consumer.register_handler(EventType.OBLIGATION_CREATED, handler2)
        
        self.assertEqual(len(consumer.handlers[EventType.OBLIGATION_CREATED.value]), 2)
        
        envelope = MessageEnvelope(
            event_type=EventType.OBLIGATION_CREATED,
            payload={"test": "data"}
        )
        
        message = MagicMock()
        message.body = b'{"event_type": "obligation.created", "payload": {"test": "data"}}'
        message.process.return_value.__aenter__ = AsyncMock()
        message.process.return_value.__aexit__ = AsyncMock()
        
        await consumer.process_message(message)
        
        self.assertTrue(handler1_called)
        self.assertTrue(handler2_called)

if __name__ == "__main__":
    unittest.main()
