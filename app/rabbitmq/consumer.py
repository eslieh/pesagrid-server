import aio_pika
import json
import logging
import asyncio
from typing import Dict, Callable, Awaitable, Any
from functools import partial

from .client import RabbitMQClient
from .types import MessageEnvelope, EventType, EXCHANGE_NAME

logger = logging.getLogger(__name__)

HandlerType = Callable[[MessageEnvelope], Awaitable[None]]

class BaseConsumer:
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.queue_name = f"{service_name}.worker"
        self.client = RabbitMQClient()
        self.handlers: Dict[str, HandlerType] = {}

    def register_handler(self, event_type: EventType, handler: HandlerType):
        """Register a handler for a specific event type"""
        self.handlers[event_type.value] = handler
        logger.info(f"Registered handler for {event_type.value}")

    async def start(self):
        """Start consuming messages"""
        import os
        connection_string = os.getenv("RABBITMQ_CONNECTION_STRING", "amqp://guest:guest@localhost:5672/")
        await self.client.connect(connection_string)
        
        channel = await self.client.get_channel()
        
        # Declare Exchange
        exchange = await channel.declare_exchange(
            EXCHANGE_NAME, 
            aio_pika.ExchangeType.TOPIC,
            durable=True
        )

        # Declare Queue
        queue = await channel.declare_queue(
            self.queue_name,
            durable=True
        )

        # Bind Queue to Exchange for all registered event types
        for event_type in self.handlers.keys():
            await queue.bind(exchange, routing_key=event_type)
            logger.info(f"Bound {self.queue_name} to {event_type}")

        # Start consuming
        await queue.consume(self.process_message)
        logger.info(f"Started consuming from {self.queue_name}")

    async def process_message(self, message: aio_pika.IncomingMessage):
        """Process incoming RabbitMQ message"""
        async with message.process(ignore_processed=True):
            try:
                # Parse message
                body = message.body.decode()
                data = json.loads(body)
                envelope = MessageEnvelope(**data)
                
                # Find Handler
                handler = self.handlers.get(envelope.event_type.value)
                
                if handler:
                    logger.info(f"Processing {envelope.event_type} event")
                    await handler(envelope)
                    await message.ack()
                else:
                    # Log warning but ack if we don't have a handler (prevent infinite loop)
                    # Ideally we binds strictly so this shouldn't happen often
                    logger.warning(f"No handler found for {envelope.event_type}")
                    await message.ack()

            except Exception as e:
                logger.error(f"Error processing message: {str(e)}")
                # Simple retry logic: reject and requeue if count < max
                retry_count = message.headers.get("x-retry-count", 0) if message.headers else 0
                max_retries = 3
                
                if retry_count < max_retries:
                    # We can't easily increment header in reject, so this is a simplified view
                    # Real retry usually involves dead-lettering or publishing to a retry queue
                    # For now, we will just Log and Nack(requeue=False) to avoid poison pill loops
                    # until we implement full DLQ
                    logger.error("Message failed. Dropping to avoid infinite loop (TODO: DLQ)")
                    await message.ack() 
                else:
                    await message.ack()
