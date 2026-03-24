import asyncio
import requests
import logging

from app.core.phone_utils import normalise_for_sms, InvalidPhoneNumberError
from app.core.config import settings

logger = logging.getLogger(__name__)


class SendSms:
    def __init__(self, phone: str, message: str, default_country: str = "KE"):
        self.raw_phone = phone
        self.phone = normalise_for_sms(phone, default_country=default_country)
        self.message = message

    def post(self) -> dict:
        url = "https://smsportal.hostpinnacle.co.ke/SMSApi/send"
        payload = {
            "userid":          settings.SMS_USER_ID,
            "password":        settings.SMS_PASSWORD,
            "mobile":          self.phone,
            "msg":             self.message,
            "senderid":        settings.SMS_SENDER_ID,
            "msgType":         "text",
            "duplicatecheck":  "true",
            "output":          "json",
            "sendMethod":      "quick",
        }
        headers = {
            "apikey":        settings.SMS_API_KEY,
            "cache-control": "no-cache",
            "content-type":  "application/x-www-form-urlencoded",
        }
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=10)
            response.raise_for_status()
            logger.info(f"📱 SMS sent to {self.phone}")
            return {"success": True}
        except requests.HTTPError as http_err:
            logger.error(f"SMS HTTP error to {self.phone}: {http_err}")
            return {"error": f"HTTP error: {http_err}"}
        except requests.RequestException as e:
            logger.error(f"SMS request failed to {self.phone}: {e}")
            return {"error": str(e)}


async def send_sms(phone: str, message: str, default_country: str = "KE") -> dict:
    """
    Async wrapper — parses + normalises the number internationally,
    then runs the blocking HTTP call in a thread pool.

    Args:
        phone:           Raw phone string in any common format.
        message:         SMS body text.
        default_country: ISO 3166-1 alpha-2 hint used when no country code
                         is present (e.g. '0712...' → KE → '+254712...').
                         Defaults to 'KE'.

    Raises:
        InvalidPhoneNumberError: If the number is unparseable or invalid.
    """
    def _send():
        return SendSms(phone, message, default_country=default_country).post()

    return await asyncio.to_thread(_send)
