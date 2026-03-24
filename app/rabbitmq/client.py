import aio_pika
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

class RabbitMQClient:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RabbitMQClient, cls).__new__(cls)
            cls._instance.connection = None
            cls._instance.channel = None
        return cls._instance

    def __init__(self):
        # Singleton init is handled in __new__ mostly, but we can verify here
        pass

    async def connect(self, connection_string: str, prefetch_count: int = 10) -> aio_pika.Channel:
        """
        Connect to RabbitMQ and return a channel.
        If already connected, returns existing channel.
        """
        if self.connection and not self.connection.is_closed:
            return self.channel

        try:
            logger.info("Connecting to RabbitMQ...")
            self.connection = await aio_pika.connect_robust(connection_string)
            self.channel = await self.connection.channel()
            await self.channel.set_qos(prefetch_count=prefetch_count)
            logger.info("Successfully connected to RabbitMQ")
            return self.channel
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {str(e)}")
            raise

    async def close(self):
        """Close RabbitMQ connection"""
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
            logger.info("RabbitMQ connection closed")

    async def get_channel(self) -> aio_pika.Channel:
        """Get the current active channel, assuming connect() has been called"""
        if not self.channel or self.connection.is_closed:
            raise RuntimeError("RabbitMQ connection not established. Call connect() first.")
        return self.channel
