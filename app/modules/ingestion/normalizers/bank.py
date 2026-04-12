"""
Generic bank notification normalizers.
Supports Bank-K (KCB) Till and Account notifications.
"""
from datetime import datetime
from typing import Optional, Any, Dict
from app.modules.ingestion.normalizers.mpesa import NormalizedPayment, _normalize_phone


def _parse_bank_datetime(raw: str) -> Optional[datetime]:
    """
    Parses various bank date formats.
    Till: "Mon May 19 13:30:54 EAT 2025"
    Account: "202111110305"
    """
    if not raw:
        return None
    
    # Try Account format: 202111110305 (YYYYMMDDHHMM)
    if len(raw) == 12 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d%H%M")
        except ValueError:
            pass

    # Try Till format: Mon May 19 13:30:54 EAT 2025
    try:
        # Standard ctime-like format but with EAT
        # We strip the timezone name for simplicity in strptime
        clean_raw = raw.replace(" EAT ", " ")
        return datetime.strptime(clean_raw, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        pass

    return None


def normalize_bank_k_till(payload: Dict[str, Any]) -> NormalizedPayment:
    """
    Normalize a Bank-K (KCB) Till / Vooma notification.
    
    Structure: requestPayload.additionalData.notificationData
    """
    outer = payload.get("requestPayload", {})
    data = outer.get("additionalData", {}).get("notificationData", {})
    
    first  = data.get("firstName", "") or ""
    middle = data.get("middleName", "") or ""
    last   = data.get("lastName", "") or ""
    payer_name = " ".join(filter(None, [first, middle, last])).strip() or None

    # For Till notifications, the narration is often used as the reconciliation key
    narration = str(data.get("narration", "")).strip()
    
    return NormalizedPayment(
        psp_ref=str(data.get("transactionID", "")),
        amount=float(data.get("transactionAmt", 0)),
        currency=str(data.get("currency", "KES")),
        phone=_normalize_phone(str(data.get("debitMSISDN", ""))),
        account_no=narration.upper(), # Reconcile using narration
        payer_name=payer_name,
        raw_payload=payload,
        transacted_at=_parse_bank_datetime(data.get("transactionDate")),
        narration=narration
    )


def normalize_bank_k_account(payload: Dict[str, Any]) -> NormalizedPayment:
    """
    Normalize a Bank-K (KCB) Account notification.
    """
    payer_name = str(payload.get("customerName", "")).strip() or None
    narration = str(payload.get("narration", "")).strip()

    return NormalizedPayment(
        psp_ref=str(payload.get("transactionReference", "")),
        amount=float(payload.get("transactionAmount", 0)),
        currency=str(payload.get("currency", "KES")),
        phone=_normalize_phone(str(payload.get("customerMobileNumber", ""))),
        account_no=narration.upper(), # Reconcile using narration
        payer_name=payer_name,
        raw_payload=payload,
        transacted_at=_parse_bank_datetime(payload.get("timestamp")),
        narration=narration
    )
