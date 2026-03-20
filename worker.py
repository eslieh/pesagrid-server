import asyncio
import logging
import signal
from app.rabbitmq import BaseConsumer, MessageEnvelope, EventType
from app.modules.ingestion.reconciliation import reconcile_transaction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)


async def _dispatch(event_type: str, payload: dict):
    """Open a DB session and call the NotificationDispatcher."""
    from app.core.dependancies import SessionLocal
    from app.modules.notifications.services.dispatcher import NotificationDispatcher
    from app.modules.notifications.services.renderer import build_context
    import uuid

    db = SessionLocal()
    try:
        dispatcher = NotificationDispatcher(db)
        collection_id = uuid.UUID(payload.get("collection_id", ""))
        payer_id_raw = payload.get("payer_id")
        payer_id = uuid.UUID(payer_id_raw) if payer_id_raw else None
        phone = payload.get("phone") or None
        email = payload.get("email") or None

        context = build_context(
            payer_name=payload.get("payer_name", ""),
            amount_due=payload.get("amount_due", 0),
            amount_paid=payload.get("amount_paid", 0),
            balance=payload.get("balance", 0),
            due_date=payload.get("due_date", ""),
            account_no=payload.get("account_no", ""),
            description=payload.get("description", ""),
            currency=payload.get("currency", "KES"),
            psp_ref=payload.get("psp_ref", ""),
            transaction_date=payload.get("ingested_at", ""),
            phone=phone or "",
            # extra fields forwarded to renderer
            login_url=payload.get("login_url", ""),
            otp=payload.get("otp", ""),
            reset_url=payload.get("reset_url", ""),
        )

        await dispatcher.dispatch(
            event_type=event_type,
            collection_id=collection_id,
            context=context,
            phone=phone,
            email=email,
            payer_id=payer_id,
        )
    except Exception as e:
        logger.error(f"❌ Dispatch failed for {event_type}: {e}")
    finally:
        db.close()


class PesagridWorker:
    """
    Async worker — runs separately from the FastAPI server.

    Events consumed:
      payment.received    →  reconcile transaction
      payment.matched     →  send payment receipt to payer
      payment.partial     →  send partial payment notice to payer
      payment.unmatched   →  alert business owner
      obligation.created  →  notify payer of new obligation
      auth.welcome        →  welcome email to new user
      auth.password_reset →  OTP / reset link

    Start with:
        python worker.py
    """

    def __init__(self):
        self.consumer = BaseConsumer(service_name="pesagrid-worker")
        self.running = False

    # ─── Handlers ─────────────────────────────────────────────────────────────

    async def handle_payment_received(self, envelope: MessageEnvelope):
        """Reconcile the transaction → will publish payment.matched / .partial / .unmatched."""
        transaction_id = envelope.payload.get("transaction_id")
        if not transaction_id:
            logger.error(f"payment.received missing transaction_id: {envelope.payload}")
            return
        logger.info(f"💳 Reconciling transaction {transaction_id}")
        await reconcile_transaction(transaction_id)

    async def handle_payment_matched(self, envelope: MessageEnvelope):
        logger.info(f"✅ payment.matched — sending receipt to payer")
        await _dispatch("payment.matched", envelope.payload)

    async def handle_payment_partial(self, envelope: MessageEnvelope):
        logger.info(f"⚡ payment.partial — sending partial notice to payer")
        await _dispatch("payment.partial", envelope.payload)

    async def handle_payment_unmatched(self, envelope: MessageEnvelope):
        logger.info(f"⚠️  payment.unmatched — alerting business owner")
        # For unmatched we notify the owner, not the payer
        payload = dict(envelope.payload)
        # Owner contact info not in payload — skip for now (future: lookup user by collection_id)
        await _dispatch("payment.unmatched", payload)

    async def handle_obligation_created(self, envelope: MessageEnvelope):
        logger.info(f"📋 obligation.created — notifying payer")
        await _dispatch("obligation.created", envelope.payload)

    async def handle_auth_welcome(self, envelope: MessageEnvelope):
        logger.info(f"👋 auth.welcome — sending welcome email")
        await _dispatch("auth.welcome", envelope.payload)

    async def handle_auth_password_reset(self, envelope: MessageEnvelope):
        logger.info(f"🔑 auth.password_reset — sending reset link")
        await _dispatch("auth.password_reset", envelope.payload)

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self):
        logger.info("🚀 Starting Pesagrid Worker...")

        self.consumer.register_handler(EventType.PAYMENT_RECEIVED,    self.handle_payment_received)
        self.consumer.register_handler(EventType.PAYMENT_MATCHED,     self.handle_payment_matched)
        self.consumer.register_handler(EventType.PAYMENT_PARTIAL,     self.handle_payment_partial)
        self.consumer.register_handler(EventType.PAYMENT_UNMATCHED,   self.handle_payment_unmatched)
        self.consumer.register_handler(EventType.OBLIGATION_CREATED,  self.handle_obligation_created)
        self.consumer.register_handler(EventType.AUTH_WELCOME,        self.handle_auth_welcome)
        self.consumer.register_handler(EventType.AUTH_PASSWORD_RESET, self.handle_auth_password_reset)

        self.running = True
        await self.consumer.start()

        logger.info("✅ Worker listening. Waiting for events...")
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
