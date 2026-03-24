"""
International phone number normaliser.

Wraps the `phonenumbers` library to provide clean E.164 output.

Usage:
    from app.core.phone_utils import normalise_phone, normalise_for_sms

    # E.164 with + (RFC standard, good for storage)
    normalise_phone("0712345678", default_country="KE")
    # → "+254712345678"

    # E.164 without + (what most SMS APIs want, including Hostpinnacle)
    normalise_for_sms("0712345678", default_country="KE")
    # → "254712345678"
"""

import phonenumbers
from phonenumbers import PhoneNumberFormat, NumberParseException
from typing import Optional


# Default region used when no country code is present in the number.
# Override per-call or change this constant for your primary market.
_DEFAULT_COUNTRY = "KE"


class InvalidPhoneNumberError(ValueError):
    """Raised when a phone number cannot be parsed or is invalid."""
    pass


def normalise_phone(
    raw: str,
    default_country: str = _DEFAULT_COUNTRY,
) -> str:
    """
    Parse and return the phone number in full E.164 format (with leading +).

    Examples:
        "0712345678"       (KE) → "+254712345678"
        "+254712345678"    (KE) → "+254712345678"
        "712345678"        (KE) → "+254712345678"
        "+447911123456"    (GB) → "+447911123456"
        "07911123456"      (GB) → "+447911123456"

    Args:
        raw:            The input phone string (any common format).
        default_country: ISO 3166-1 alpha-2 country code hint used when the
                         number has no international prefix.  Defaults to "KE".

    Raises:
        InvalidPhoneNumberError: If the number cannot be parsed or is invalid.
    """
    raw = raw.strip()
    if not raw:
        raise InvalidPhoneNumberError("Phone number is empty")

    try:
        parsed = phonenumbers.parse(raw, default_country)
    except NumberParseException as e:
        raise InvalidPhoneNumberError(f"Cannot parse phone number '{raw}': {e}")

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneNumberError(
            f"Phone number '{raw}' is not a valid number for region "
            f"'{phonenumbers.region_code_for_number(parsed)}'"
        )

    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def normalise_for_sms(
    raw: str,
    default_country: str = _DEFAULT_COUNTRY,
) -> str:
    """
    Like normalise_phone() but strips the leading '+' for SMS gateway APIs
    that expect a plain numeric string (e.g. Hostpinnacle uses '254...').
    """
    e164 = normalise_phone(raw, default_country)
    return e164.lstrip("+")


def is_valid_phone(raw: str, default_country: str = _DEFAULT_COUNTRY) -> bool:
    """Return True if the number is parseable and valid, False otherwise."""
    try:
        normalise_phone(raw, default_country)
        return True
    except InvalidPhoneNumberError:
        return False
