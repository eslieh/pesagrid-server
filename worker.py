"""
PesaGrid Worker — entry point.

This file contains ONLY setup and lifecycle code.
All business logic lives in the handler modules of each respective app module:

  app/modules/ingestion/handlers.py          → webhook.mpesa, payment.received
  app/modules/notifications/services/handlers.py → payment.matched/partial/unmatched, obligation.created
  app/modules/auth/handlers.py               → auth.welcome, auth.password_reset

Start with:
    python worker.py
"""
import asyncio
import logging
import signal

from app.rabbitmq import BaseConsumer, MessageEnvelope, EventType

# ─── Handler imports ───────────────────────────────────────────────────────────
from app.modules.ingestion.handlers import (
    handle_webhook_mpesa,
    handle_payment_received,
)
from app.modules.notifications.services.handlers import (
    handle_payment_matched,
    handle_payment_partial,
    handle_payment_unmatched,
    handle_obligation_created,
    handle_obligation_due,
)
from app.modules.auth.handlers import (
    handle_auth_welcome,
    handle_auth_password_reset,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)


class PesagridWorker:
    """
    Async worker — runs separately from the FastAPI server.

    Events consumed:
      webhook.mpesa       →  normalize → ingest → reconcile M-PESA callback
      payment.received    →  reconcile an already-ingested transaction
      payment.matched     →  send payment receipt to payer
      payment.partial     →  send partial payment notice to payer
      payment.unmatched   →  alert business owner
      obligation.created  →  notify payer of new obligation
      auth.welcome        →  send verification code to new user
      auth.password_reset →  send password reset code
    """

    def __init__(self):
        self.consumer = BaseConsumer(service_name="pesagrid-worker")
        self.running = False

    async def start(self):
        logger.info("🚀 Starting Pesagrid Worker...")

        self.consumer.register_handler(EventType.WEBHOOK_MPESA,        handle_webhook_mpesa)
        self.consumer.register_handler(EventType.PAYMENT_RECEIVED,     handle_payment_received)
        self.consumer.register_handler(EventType.PAYMENT_MATCHED,      handle_payment_matched)
        self.consumer.register_handler(EventType.PAYMENT_PARTIAL,      handle_payment_partial)
        self.consumer.register_handler(EventType.PAYMENT_UNMATCHED,    handle_payment_unmatched)
        self.consumer.register_handler(EventType.OBLIGATION_CREATED,   handle_obligation_created)
        self.consumer.register_handler(EventType.OBLIGATION_DUE,       handle_obligation_due)
        self.consumer.register_handler(EventType.AUTH_WELCOME,         handle_auth_welcome)
        self.consumer.register_handler(EventType.AUTH_PASSWORD_RESET,  handle_auth_password_reset)

        self.running = True
        await self.consumer.start()
        
        # Start background recurring billing engine
        from app.modules.obligations.cron import cron_worker_loop
        asyncio.create_task(cron_worker_loop())

        logger.info("✅ Worker listening & Cron Job active. Waiting for events...")
        while self.running:
            await asyncio.sleep(1)

    async def stop(self):
        logger.info("🛑 Stopping Pesagrid Worker...")
        self.running = False
        await self.consumer.client.close()


async def main():
    worker = PesagridWorker()

    def signal_handler():
        asyncio.create_task(worker.stop())

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT,  signal_handler)
    loop.add_signal_handler(signal.SIGTERM, signal_handler)

    try:
        await worker.start()
    except asyncio.CancelledError:
        pass
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
