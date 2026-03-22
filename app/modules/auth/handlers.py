"""
Auth event handlers — called by the RabbitMQ worker.

Handles:
  auth.welcome        → send verification code via SMS or Email
  auth.password_reset → send password reset code via SMS or Email
"""
import logging
from app.rabbitmq import MessageEnvelope
from app.core.config import settings
from app.modules.notifications.services.send_sms import send_sms
from app.modules.notifications.services.send_email import send_email
from app.modules.notifications.services.dispatcher import _build_from_email

logger = logging.getLogger(__name__)


async def _send_auth_notification(
    payload: dict,
    subject: str,
    sms_body: str,
    html_body: str,
) -> None:
    """Send auth notification via the user's primary channel with email/sms fallback."""
    email = payload.get("email")
    phone = payload.get("phone")
    auth_type = payload.get("auth_type")
    from_email = _build_from_email("pesagrid", settings.RESEND_FROM_EMAIL, platform=True)

    sent = False
    if auth_type == "phone" and phone:
        try:
            await send_sms(phone, sms_body)
            sent = True
        except Exception as e:
            logger.warning(f"SMS notify failed: {e}")

    if auth_type == "email" and email:
        try:
            await send_email(email, subject, html_body, from_email=from_email)
            sent = True
        except Exception as e:
            logger.warning(f"Email notify failed: {e}")

    # Fallback — try whichever contact is available
    if not sent:
        if email:
            try:
                await send_email(email, subject, html_body, from_email=from_email)
                sent = True
            except Exception as e:
                logger.warning(f"Fallback email failed: {e}")
        if not sent and phone:
            try:
                await send_sms(phone, sms_body)
            except Exception as e:
                logger.warning(f"Fallback SMS failed: {e}")


async def handle_auth_welcome(envelope: MessageEnvelope) -> None:
    """Send welcome / verification code to a newly registered user."""
    logger.info("👋 auth.welcome — sending welcome email/sms")
    payload = envelope.payload
    token = payload.get("token", "")

    subject = "Welcome to PesaGrid — verify your account"
    sms_body = f"Welcome to PesaGrid! Your verification code: {token}. Expires in 24 hours."
    html_body = (
        f"<h2>Welcome to PesaGrid!</h2>"
        f"<p>Your account is almost ready. Verify it here:</p>"
        f"<p><a href='{settings.CLIENT_URL}/auth/verify?token={token}'>Verify Account</a></p>"
        f"<p>Or use code: <strong>{token}</strong></p>"
        f"<p>This link expires in 24 hours.</p>"
    )
    await _send_auth_notification(payload, subject, sms_body, html_body)


async def handle_auth_password_reset(envelope: MessageEnvelope) -> None:
    """Send a password reset code to the user."""
    logger.info("🔑 auth.password_reset — sending reset link/sms")
    payload = envelope.payload
    token = payload.get("token", "")

    subject = "Reset your PesaGrid password"
    sms_body = f"PesaGrid password reset code: {token}. Expires in 1 hour. Ignore if you didn't request this."
    html_body = (
        f"<h2>Reset your password</h2>"
        f"<p>Click to set a new password: "
        f"<a href='{settings.CLIENT_URL}/auth/reset-password?token={token}'>Reset Password</a></p>"
        f"<p>Or use code: <strong>{token}</strong></p>"
        f"<p>Expires in 1 hour. If you didn't request this, ignore this message.</p>"
    )
    await _send_auth_notification(payload, subject, sms_body, html_body)
