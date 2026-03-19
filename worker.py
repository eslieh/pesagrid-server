import asyncio
import logging
import signal
from app.rabbitmq import BaseConsumer, MessageEnvelope, EventType
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AppWorker:
    def __init__(self):
        self.consumer = BaseConsumer(service_name="app-service")
        self.running = False

    # async def handle_user_verified(self, envelope: MessageEnvelope):
    #     logger.info(f"🔐 Processing USER_VERIFIED event: {envelope.payload}")
    #     await asyncio.sleep(0.1)

    # async def handle_password_reset(self, envelope: MessageEnvelope):
    #     logger.info(f"🔑 Processing PASSWORD_RESET_REQUESTED event: {envelope.payload}")
    #     # Logic here
    #     await asyncio.sleep(0.1)

    # async def handle_role_removed(self, envelope: MessageEnvelope):
    #     logger.info(f"🎭 Processing USER_ROLE_REMOVED event: {envelope.payload}")
    #     if not envelope.payload:
    #         logger.error("❌ Payload is empty or None")
    #         return

    #     user_id = envelope.payload.get("user_id")
    #     role_name = envelope.payload.get("role_name")
    #     school_id = envelope.payload.get("school_id")
        
    #     logger.info(f"🔍 DEBUG: user_id={user_id} (type={type(user_id)}), role_name={role_name} (type={type(role_name)}), school_id={school_id} (type={type(school_id)})")
        
    #     if not user_id or not role_name:
    #         logger.error("Missing user_id or role_name in payload")
    #         return
            
    #     db = SessionLocal()
    #     try:
    #         auth_service = AuthService(db)
    #         await auth_service.remove_role_from_user(
    #             user_id=uuid.UUID(user_id),
    #             role_name=role_name,
    #             school_id=uuid.UUID(school_id) if school_id else None
    #         )
    #         logger.info(f"✅ Successfully removed role '{role_name}' from user {user_id}")
    #     except Exception as e:
    #         logger.error(f"❌ Failed to remove role: {str(e)}")
    #     finally:
    #         db.close()

    # async def handle_role_assigned(self, envelope: MessageEnvelope):
    #     logger.info(f"🎭 Processing USER_ROLE_ASSIGNED event: {envelope.payload}")
    #     if not envelope.payload:
    #         logger.error("❌ Payload is empty or None")
    #         return

    #     user_id = envelope.payload.get("user_id")
    #     role_name = envelope.payload.get("role_name")
    #     school_id = envelope.payload.get("school_id")
        
    #     logger.info(f"🔍 DEBUG: user_id={user_id} (type={type(user_id)}), role_name={role_name} (type={type(role_name)}), school_id={school_id} (type={type(school_id)})")
        
    #     if not user_id or not role_name:
    #         logger.error("Missing user_id or role_name in payload")
    #         return
            
    #     db = SessionLocal()
    #     try:
    #         auth_service = AuthService(db)
    #         await auth_service.assign_role_to_user(
    #             user_id=uuid.UUID(user_id),
    #             role_name=role_name,
    #             school_id=uuid.UUID(school_id) if school_id else None
    #         )
    #         logger.info(f"✅ Successfully assigned role '{role_name}' to user {user_id}")
    #     except Exception as e:
    #         logger.error(f"❌ Failed to assign role: {str(e)}")
    #     finally:
    #         db.close()


    # async def handle_student_enrolled(self, envelope: MessageEnvelope):
    #     logger.info(f"Student enrolled request: {envelope.payload}")

    #     payload = envelope.payload

    #     data = StudentOnboardingRequest.model_validate(payload)
    #     db = SessionLocal()
    #     try:
    #         auto_onboarding_service = AutoOnboardingService(db)
    #         response = await auto_onboarding_service.create_student_account(data=data)
    #         logger.info(f"✅ Successfully created student {response}")
    #     except Exception as e:
    #         logger.error(f"❌ Failed to create student: {str(e)}")
    #     finally:
    #         db.close()        

    async def start(self):
        logger.info("Starting Auth Worker...")
        
        # # Register Handlers
        # self.consumer.register_handler(EventType.USER_VERIFIED, self.handle_user_verified)
        # self.consumer.register_handler(EventType.PASSWORD_RESET_REQUESTED, self.handle_password_reset)
        # self.consumer.register_handler(EventType.USER_ROLE_ASSIGNED, self.handle_role_assigned)
        # self.consumer.register_handler(EventType.USER_ROLE_REMOVED, self.handle_role_removed)
        # self.consumer.register_handler(EventType.STUDENT_ENROLLED, self.handle_student_enrolled)
        # Start Consumer
        self.running = True
        await self.consumer.start()
        
        while self.running:
            await asyncio.sleep(1)

    async def stop(self):
        logger.info("Stopping Auth Worker...")
        self.running = False
        await self.consumer.client.close()

async def main():
    worker = AppWorker()
    
    def signal_handler():
        asyncio.create_task(worker.stop())

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, signal_handler)
    loop.add_signal_handler(signal.SIGTERM, signal_handler)

    try:
        await worker.start()
    except asyncio.CancelledError:
        pass
    finally:
        await worker.stop()

if __name__ == "__main__":
    asyncio.run(main())
