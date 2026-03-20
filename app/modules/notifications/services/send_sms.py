import asyncio
import requests
import os
import logging

logger = logging.getLogger(__name__)

class SendSms:
    def __init__(self, phone, message):
        # Normalize the phone number
        self.phone = self.format_phone_number(phone)
        self.message = message

    def format_phone_number(self, phone):
        # Ensure phone is a string
        phone = str(phone).strip()

        # If phone starts with '0', remove the '0' and prepend '254'
        if phone.startswith('0'):
            formatted_phone = f'254{phone[1:]}'
        # If phone starts with '+254', remove the '+' and leave the rest
        elif phone.startswith('+254'):
            formatted_phone = f'254{phone[4:]}'
        # If phone starts with '254', leave it as is
        elif phone.startswith('254'):
            formatted_phone = phone
        # Handle local numbers starting with '7' or '01'
        elif phone.startswith('7') and len(phone) == 9:  # local number like 712345678
            formatted_phone = f'254{phone}'
        elif phone.startswith('1') and len(phone) == 9:  # local number like 0123456789
            formatted_phone = f'254{phone}'
        else:
            raise ValueError("Invalid phone number format. Ensure it's either +254, 254, or starts with 0.")

        return formatted_phone

    def post(self):
        # Remove leading zero(s) from phone number
        formatted_phone = self.phone
        full_phone = formatted_phone

        # URL for SMS portal
        url = "https://smsportal.hostpinnacle.co.ke/SMSApi/send"

        # Prepare payload
        payload = {
            'userid': os.getenv("SMS_USER_ID"),
            'password': os.getenv("SMS_PASSWORD"),
            'mobile': full_phone,
            'msg': self.message,
            'senderid': 'Intacom',
            'msgType': 'text',
            'duplicatecheck': 'true',
            'output': 'json',
            'sendMethod': 'quick'
        }

        # Headers
        headers = {
            'apikey': os.getenv("SMS_API_KEY"),
            'cache-control': 'no-cache',
            'content-type': 'application/x-www-form-urlencoded'
        }

        try:
            response = requests.post(url, data=payload, headers=headers, timeout=10)
            response.raise_for_status()
            logger.info(f"📱 SMS sent to {self.phone}")
            return {"success": True}
        except requests.HTTPError as http_err:
            logger.error(f"SMS HTTP error to {self.phone}: {http_err}")
            return {"error": f"HTTP error occurred: {http_err}"}
        except requests.RequestException as e:
            logger.error(f"SMS request failed to {self.phone}: {e}")
            return {"error": str(e)}


async def send_sms(phone: str, message: str) -> dict:
    """
    Async wrapper — runs the blocking HTTP call in a thread pool.
    Safe to await from the async worker / dispatcher.
    """
    def _send():
        return SendSms(phone, message).post()

    return await asyncio.to_thread(_send)
