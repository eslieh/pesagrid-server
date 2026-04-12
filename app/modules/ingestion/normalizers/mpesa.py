"""
M-PESA payload normalizers.

Two callback types:
  C2B  — fired by Safaricom when a customer pays via paybill (most common)
  STK  — Lipa Na M-PESA (push prompt) callback result

Both are converted to a NormalizedPayment dataclass before storage.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Any, Dict


@dataclass
class NormalizedPayment:
    psp_ref:    str                  # unique transaction ID from PSP
    amount:     float
    currency:   str
    phone:      str                  # normalized: 254XXXXXXXXX
    account_no: str                  # reconciliation key (BillRefNumber)
    payer_name: Optional[str]
    raw_payload: Dict[str, Any]
    transacted_at: Optional[datetime] = None
    narration: Optional[str] = None


def _normalize_phone(msisdn: str) -> str:
    """Normalize M-PESA phone to 254XXXXXXXXX format."""
    msisdn = str(msisdn).strip().replace("+", "").replace(" ", "")
    if msisdn.startswith("0"):
        msisdn = "254" + msisdn[1:]
    return msisdn


def _parse_mpesa_datetime(raw: str) -> Optional[datetime]:
    """Parse M-PESA TransTime format: 20250401104247 → datetime."""
    try:
        return datetime.strptime(str(raw), "%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return None


def normalize_c2b(payload: Dict[str, Any]) -> NormalizedPayment:
    """
    Normalize a Safaricom C2B callback.

    Expected fields:
        TransID, TransAmount, MSISDN, BillRefNumber,
        FirstName, MiddleName, LastName, TransTime, BusinessShortCode
    """
    first  = payload.get("FirstName", "") or ""
    middle = payload.get("MiddleName", "") or ""
    last   = payload.get("LastName", "")  or ""
    payer_name = " ".join(filter(None, [first, middle, last])).strip() or None

    return NormalizedPayment(
        psp_ref=payload["TransID"],
        amount=float(payload.get("TransAmount", 0)),
        currency="KES",
        phone=_normalize_phone(payload.get("MSISDN", "")),
        account_no=str(payload.get("BillRefNumber", "")).strip().upper(),
        payer_name=payer_name,
        raw_payload=payload,
        transacted_at=_parse_mpesa_datetime(payload.get("TransTime")),
        narration=str(payload.get("BillRefNumber", "")).strip(),
    )


def normalize_stk(payload: Dict[str, Any]) -> Optional[NormalizedPayment]:
    """
    Normalize an STK Push callback result.
    Returns None if ResultCode != 0 (failed / cancelled payment).

    Expected structure:
        Body.stkCallback.{ResultCode, CallbackMetadata.Item[...]}
    """
    callback = payload.get("Body", {}).get("stkCallback", {})
    result_code = callback.get("ResultCode", -1)

    if result_code != 0:
        # Payment was not completed — cancelled or failed
        return None

    # Extract items from CallbackMetadata
    items: Dict[str, Any] = {}
    for item in callback.get("CallbackMetadata", {}).get("Item", []):
        name  = item.get("Name")
        value = item.get("Value")
        if name:
            items[name] = value

    psp_ref    = str(items.get("MpesaReceiptNumber", ""))
    amount     = float(items.get("Amount", 0))
    phone      = _normalize_phone(str(items.get("PhoneNumber", "")))
    trans_date = _parse_mpesa_datetime(items.get("TransactionDate"))

    return NormalizedPayment(
        psp_ref=psp_ref,
        amount=amount,
        currency="KES",
        phone=phone,
        account_no="",          # STK push has no BillRefNumber — must be resolved separately
        payer_name=None,
        raw_payload=payload,
        transacted_at=trans_date,
    )
