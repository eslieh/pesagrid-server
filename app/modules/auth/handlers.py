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
from app.modules.notifications.services.renderer import wrap_in_template
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
    from_email = _build_from_email("PesaGrid", settings.RESEND_FROM_EMAIL, platform=True)

    sent = False
    if auth_type == "phone" and phone:
        try:
            await send_sms(phone, sms_body)
            sent = True
        except Exception as e:
            logger.warning(f"SMS notify failed: {e}")

    if auth_type == "email" and email:
        try:
            wrapped_body = wrap_in_template(html_body, business_name="PesaGrid")
            await send_email(email, subject, wrapped_body, from_email=from_email)
            sent = True
        except Exception as e:
            logger.warning(f"Email notify failed: {e}")

    # Fallback — try whichever contact is available
    if not sent:
        if email:
            try:
                wrapped_body = wrap_in_template(html_body, business_name="PesaGrid")
                await send_email(email, subject, wrapped_body, from_email=from_email)
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
        f"<div style='margin: 24px 0;'><a href='{settings.CLIENT_URL}/auth/verify?token={token}' class='button-black'>Verify Account</a></div>"
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
        f"<div style='margin: 24px 0;'><a href='{settings.CLIENT_URL}/auth/reset-password?token={token}' class='button-black'>Reset Password</a></div>"
        f"<p>Or use code: <strong>{token}</strong></p>"
        f"<p>Expires in 1 hour. If you didn't request this, ignore this message.</p>"
    )
    await _send_auth_notification(payload, subject, sms_body, html_body)

async def handle_auth_mfa(envelope: MessageEnvelope) -> None:
    """Send a Two-Step Verification OTP to the user for sensitive actions."""
    logger.info("🔒 auth.mfa_request — sending MFA code")
    payload = envelope.payload
    token = payload.get("token", "")

    subject = "Your PesaGrid Verification Code"
    sms_body = f"PesaGrid Verification Code: {token}. Valid for 15 minutes."
    html_body = (
        f"<h2>Sensitive Action Verification</h2>"
        f"<p>You are about to modify sensitive business configurations on PesaGrid.</p>"
        f"<p>Your verification code is: <strong>{token}</strong></p>"
        f"<p>This code expires in 15 minutes. Do not share this code with anyone.</p>"
    )
    await _send_auth_notification(payload, subject, sms_body, html_body)
