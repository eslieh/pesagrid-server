from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

EXCHANGE_NAME = "pesagrid.events"

class EventType(str, Enum):
    # Direct send events (notification worker dispatches these)
    SEND_EMAIL = "notification.email.send"
    SEND_SMS   = "notification.sms.send"
    SEND_PUSH  = "notification.push.send"
    # Ingestion
    WEBHOOK_MPESA     = "webhook.mpesa"           # raw callback → worker ingests + reconciles
    WEBHOOK_BANK_K_TILL    = "webhook.bank_k.till"     # raw Bank-K till callback
    WEBHOOK_BANK_K_ACCOUNT = "webhook.bank_k.account"  # raw Bank-K account callback
    PAYMENT_RECEIVED  = "payment.received"        # fired after a transaction is ingested
    # Reconciliation outcomes
    PAYMENT_MATCHED   = "payment.matched"     # obligation → PAID
    PAYMENT_PARTIAL   = "payment.partial"     # obligation → PARTIAL
    PAYMENT_UNMATCHED    = "payment.unmatched"     # no obligation found
    PAYMENT_CATEGORIZED  = "payment.categorized"   # collection point payment — opt-in acknowledgement
    PAYMENT_MANUAL_MATCH = "payment.manual.match"  # manually force matching an unmatched transaction
    # Obligations lifecycle
    OBLIGATION_CREATED = "obligation.created"
    OBLIGATION_DUE     = "obligation.due"     # scheduled reminder (future)
    OBLIGATION_CANCELLED = "obligation.cancelled"
    # Auth
    AUTH_WELCOME        = "auth.welcome"
    AUTH_PASSWORD_RESET = "auth.password_reset"
    AUTH_MFA_REQUEST    = "auth.mfa_request"      # send MFA code
    USER_VERIFIED       = "auth.user_verified"

    # Configuration changes 
    CONFIG_PSP_CREATED              = "config.psp.created"               # Notify owner of new PSP
    CONFIG_PSP_DELETED              = "config.psp.deleted"               # Notify owner of deleted PSP
    CONFIG_COLLECTION_POINT_CREATED = "config.collection_point.created"  # Notify owner of new Collection Point

    # Billing & wallet events
    BILLING_RECON_DONE         = "billing.reconciliation.done"   # deduct per-reconciliation fee
    BILLING_NOTIFICATION_SENT  = "billing.notification.sent"     # deduct per-notification fee
    BILLING_WALLET_LOW         = "billing.wallet.low_balance"    # alert owner wallet is low
    BILLING_INVOICE_GENERATED  = "billing.invoice.generated"     # monthly invoice ready
    BILLING_SUBSCRIPTION_TRIAL = "billing.subscription.trial"    # new trial subscription started
    BILLING_TOPUP_SUCCESS      = "billing.wallet.topup_success"  # notify owner of successful topup

class Priority(int, Enum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3

class MessageEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: Dict[str, Any]
    source_service: str
    target_service: Optional[str] = None
    priority: Priority = Priority.MEDIUM
