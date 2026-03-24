import aio_pika
import json
import logging
from typing import Optional
from datetime import datetime

from .client import RabbitMQClient
from .types import MessageEnvelope, EventType, Priority, EXCHANGE_NAME

logger = logging.getLogger(__name__)

class BasePublisher:
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.client = RabbitMQClient()

    async def publish(self, envelope: MessageEnvelope, routing_key: Optional[str] = None):
        """
        Publish a message to the exchange.
        If routing_key is not provided, it defaults to envelope.event_type.
        """
        import os
        if not self.client.connection or self.client.connection.is_closed:
             connection_string = os.getenv("RABBITMQ_CONNECTION_STRING", "amqp://guest:guest@localhost:5672/")
             await self.client.connect(connection_string)

        channel = await self.client.get_channel()
        
        # Ensure exchange exists
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME, 
            aio_pika.ExchangeType.TOPIC,
            durable=True
        )

        key = routing_key or envelope.event_type.value
        
        message = aio_pika.Message(
            body=envelope.json().encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            priority=envelope.priority.value,
            headers={
                "source": self.service_name,
                "event_type": envelope.event_type,
            }
        )
        
        await exchange.publish(message, routing_key=key)
        logger.info(f"Published event {envelope.event_type} to {EXCHANGE_NAME} with key {key}")

    async def publish_event(self, event_type: EventType, payload: dict, priority: Priority = Priority.MEDIUM):
        """Helper to create envelope and publish"""
        envelope = MessageEnvelope(
            event_type=event_type,
            payload=payload,
            source_service=self.service_name,
            priority=priority
        )
        await self.publish(envelope)
