from typing import List, Dict, Any, Optional
from app.modules.obligations.models import TemplateType, TemplateChannel

# Structure for a library item
# We'll expose this via the API

class LibraryTemplate:
    def __init__(
        self,
        name: str,
        template_type: TemplateType,
        channel: TemplateChannel,
        body: str,
        subject: Optional[str] = None,
        category: str = "General",
        description: Optional[str] = None
    ):
        self.name = name
        self.template_type = template_type
        self.channel = channel
        self.body = body
        self.subject = subject
        self.category = category
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "template_type": self.template_type,
            "channel": self.channel,
            "subject": self.subject,
            "body": self.body,
            "category": self.category,
            "description": self.description
        }


SYSTEM_TEMPLATES: List[LibraryTemplate] = [
    # ─── OBLIGATION_CREATED ──────────────────────────────────────────────────
    LibraryTemplate(
        name="New Invoice Confirmation (Professional)",
        category="Invoicing",
        template_type=TemplateType.OBLIGATION_CREATED,
        channel=TemplateChannel.SMS,
        body="Hi {{payer_name}}, a new invoice for {{description}} of {{currency}} {{amount_due}} has been created for {{account_no}}. Due date: {{due_date}}. Pay via Paybill {{paybill}}. Thank you!",
        description="Standard professional SMS for newly created obligations."
    ),
    LibraryTemplate(
        name="New Invoice Welcome (Email)",
        category="Invoicing",
        template_type=TemplateType.OBLIGATION_CREATED,
        channel=TemplateChannel.EMAIL,
        subject="New Invoice: {{description}} ({{account_no}})",
        body="""<h2>New Invoice Created</h2>
<p>Hello <b>{{payer_name}}</b>,</p>
<p>A new invoice has been generated for your account <b>{{account_no}}</b>.</p>

<div class="details-box">
    <table style="width: 100%; border-collapse: collapse;">
        <tr><td style="padding: 8px 0; color: #6b7280;">Description:</td><td><b>{{description}}</b></td></tr>
        <tr><td style="padding: 8px 0; color: #6b7280;">Amount Due:</td><td><b style="font-size: 18px; color: #111827;">{{currency}} {{amount_due}}</b></td></tr>
        <tr><td style="padding: 8px 0; color: #6b7280;">Due Date:</td><td><b>{{due_date}}</b></td></tr>
    </table>
</div>

<p>To settle this invoice, please use the following payment details:</p>
<div style="background: #f3f4f6; padding: 16px; border-radius: 12px; font-family: monospace; text-align: center;">
    MPESA PAYBILL: <b>{{paybill}}</b><br>
    ACCOUNT NO: <b>{{account_no}}</b>
</div>

<p>Thank you for choosing {{sender_name}}.</p>""",
        description="Detailed email welcome for a new invoice."
    ),

    # ─── PAYMENT_REMINDER ────────────────────────────────────────────────────
    LibraryTemplate(
        name="Gentle Payment Reminder (SMS)",
        category="Reminders",
        template_type=TemplateType.PAYMENT_REMINDER,
        channel=TemplateChannel.SMS,
        body="Hi {{payer_name}}, a gentle reminder that your payment of {{currency}} {{balance}} for {{description}} is due on {{due_date}}. Please settle via Paybill {{paybill}} using Account: {{account_no}}.",
        description="Polite SMS reminder before the due date."
    ),
    LibraryTemplate(
        name="Standard Payment Reminder (Email)",
        category="Reminders",
        template_type=TemplateType.PAYMENT_REMINDER,
        channel=TemplateChannel.EMAIL,
        subject="Payment Due Reminder: {{description}}",
        body="""<h2>Payment Reminder</h2>
<p>Hi {{payer_name}},</p>
<p>This is a reminder that you have an upcoming payment due for your <b>{{description}}</b>.</p>

<div class="details-box" style="border-left: 4px solid #adff2f;">
    <p style="margin: 0;"><b>Outcome Balance: {{currency}} {{balance}}</b></p>
    <p style="margin: 4px 0 0 0; font-size: 14px; color: #6b7280;">Due on: {{due_date}}</p>
</div>

<p>Please ensure payment is made via <b>Paybill {{paybill}}</b> using your unique account number <b>{{account_no}}</b>.</p>
<p>If you have already made this payment, please disregard this message.</p>""",
        description="Professional email reminder."
    ),

    # ─── OVERDUE_NOTICE ──────────────────────────────────────────────────────
    LibraryTemplate(
        name="Overdue Notice (Urgent SMS)",
        category="Reminders",
        template_type=TemplateType.OVERDUE_NOTICE,
        channel=TemplateChannel.SMS,
        body="URGENT: Hi {{payer_name}}, your payment of {{currency}} {{balance}} for {{description}} was due on {{due_date}} and is now OVERDUE. Please settle immediately via Paybill {{paybill}} to avoid service interruption or penalties.",
        description="Firm SMS for overdue payments."
    ),
    LibraryTemplate(
        name="Final Notice: Overdue Payment (Email)",
        category="Reminders",
        template_type=TemplateType.OVERDUE_NOTICE,
        channel=TemplateChannel.EMAIL,
        subject="URGENT: Your payment is overdue ({{account_no}})",
        body="""<h2 style="color: #ef4444;">Overdue Payment Alert</h2>
<p>Hello {{payer_name}},</p>
<p>Our records indicate that your payment for <b>{{description}}</b> is now <b>overdue</b>.</p>

<div style="background: #fef2f2; border: 1px solid #fee2e2; border-radius: 16px; padding: 20px; margin: 24px 0;">
    <p style="margin: 0; color: #991b1b; font-weight: bold;">Amount Outstanding: {{currency}} {{balance}}</p>
    <p style="margin: 4px 0 0 0; font-size: 14px; color: #b91c1c;">Original Due Date: {{due_date}}</p>
</div>

<p>Please settle this balance immediately to maintain your account in good standing. Use <b>Paybill {{paybill}}</b> and Account <b>{{account_no}}</b>.</p>
<p>If you are experiencing any difficulties, please contact our support team immediately.</p>""",
        description="High-priority email for overdue balances."
    ),

    # ─── PAYMENT_RECEIPT (Partial) ───────────────────────────────────────────
    LibraryTemplate(
        name="Partial Payment Receipt (SMS)",
        category="Receipts",
        template_type=TemplateType.PAYMENT_RECEIPT,
        channel=TemplateChannel.SMS,
        body="Hi {{payer_name}}, we've received {{currency}} {{amount_paid}} for {{account_no}}. Ref: {{psp_ref}}. Your remaining balance is {{currency}} {{balance}}. Thank you!",
        description="SMS acknowledgement for partial payments."
    ),
    LibraryTemplate(
        name="Partial Payment Received (Email)",
        category="Receipts",
        template_type=TemplateType.PAYMENT_RECEIPT,
        channel=TemplateChannel.EMAIL,
        subject="Payment Received (Partial): {{currency}} {{amount_paid}}",
        body="""<h2>Partial Payment Received</h2>
<p>Hi {{payer_name}},</p>
<p>We've successfully processed your payment of <b>{{currency}} {{amount_paid}}</b> for <b>{{description}}</b>.</p>

<div class="details-box">
    <p style="margin: 0;"><b>Remaining Balance: {{currency}} {{balance}}</b></p>
</div>

{{digital_receipt_html}}

<p>Your payment has been applied to account <b>{{account_no}}</b>. Please ensure the remaining balance is settled by the next cycle.</p>""",
        description="Detailed partial payment email acknowledgement."
    ),

    # ─── PAYMENT_RECEIPT_FULL ────────────────────────────────────────────────
    LibraryTemplate(
        name="Full Payment Receipt (Professional SMS)",
        category="Receipts",
        template_type=TemplateType.PAYMENT_RECEIPT_FULL,
        channel=TemplateChannel.SMS,
        body="Hi {{payer_name}}, your payment of {{currency}} {{amount_paid}} has been received. Your invoice for {{description}} is now FULLY SETTLED. Ref: {{psp_ref}}. Thank you for your prompt payment!",
        description="Clear SMS confirming full settlement."
    ),
    LibraryTemplate(
        name="Invoice Fully Settled (Email)",
        category="Receipts",
        template_type=TemplateType.PAYMENT_RECEIPT_FULL,
        channel=TemplateChannel.EMAIL,
        subject="Payment Successful: Invoice Settled ({{account_no}})",
        body="""<h2 style="color: #10b981;">Invoice Fully Settled</h2>
<p>Hello {{payer_name}},</p>
<p>Great news! Your payment of <b>{{currency}} {{amount_paid}}</b> has been received and your invoice for <b>{{description}}</b> is now fully settled.</p>

{{digital_receipt_html}}

<p>Thank you for your payment. We appreciate your continued business!</p>
<div style="margin: 32px 0;">
    <a href="#" class="button">View My History</a>
</div>""",
        description="Celebratory email for full invoice settlement."
    ),

    # ─── OBLIGATION_CANCELLED ────────────────────────────────────────────────
    LibraryTemplate(
        name="Invoice Cancellation Notice (SMS)",
        category="System",
        template_type=TemplateType.OBLIGATION_CANCELLED,
        channel=TemplateChannel.SMS,
        body="Hi {{payer_name}}, the invoice for {{description}} ({{account_no}}) has been cancelled by {{sender_name}}. Any outstanding balance for this item has been cleared. Thank you.",
        description="Clean SMS for voided invoices."
    ),

    # ─── COLLECTION_RECEIPT ──────────────────────────────────────────────────
    LibraryTemplate(
        name="Quick Collection Receipt (SMS)",
        category="Collections",
        template_type=TemplateType.COLLECTION_RECEIPT,
        channel=TemplateChannel.SMS,
        body="Recieved: {{currency}} {{amount_paid}} for {{collection_point_name}}. Ref: {{psp_ref}}. Thank you for choosing {{sender_name}}!",
        description="Short, efficient SMS for counter/point collection receipts."
    ),

    # ─── STATEMENT ───────────────────────────────────────────────────────────
    LibraryTemplate(
        name="Monthly Account Statement (Email)",
        category="Accounts",
        template_type=TemplateType.STATEMENT,
        channel=TemplateChannel.EMAIL,
        subject="Account Statement: {{collection_name}} ({{account_no}})",
        body="""<h2>Your Account Statement</h2>
<p>Hi {{payer_name}},</p>
<p>Please find below your summary statement for <b>{{collection_name}}</b> as of {{transaction_date}}.</p>

<div class="details-box">
    <table style="width: 100%; border-collapse: collapse;">
        <tr><td style="padding: 8px 0; color: #6b7280;">Total Paid to Date:</td><td style="text-align: right;"><b>{{currency}} {{total_paid}}</b></td></tr>
        <tr><td style="padding: 8px 0; color: #6b7280;">Current Outstanding:</td><td style="text-align: right; color: #ef4444;"><b>{{currency}} {{balance}}</b></td></tr>
    </table>
</div>

<p>To settle your balance, pay via <b>Paybill {{paybill}}</b> using Account <b>{{account_no}}</b>.</p>
<p>Thank you for your business!</p>""",
        description="A summary statement email for periodic account reviews."
    ),
]


def get_system_templates(
    template_type: Optional[TemplateType] = None,
    channel: Optional[TemplateChannel] = None
) -> List[Dict[str, Any]]:
    """Filter and return the library as a list of dictionaries."""
    filtered = SYSTEM_TEMPLATES
    if template_type:
        filtered = [t for t in filtered if t.template_type == template_type]
    if channel:
        filtered = [t for t in filtered if t.channel == channel]
    
    return [t.to_dict() for t in filtered]


def get_system_default(template_type: TemplateType, channel: TemplateChannel) -> Optional[LibraryTemplate]:
    """Find the specific system default for a given type and channel."""
    # We take the first one found in the library for that type+channel combination
    for t in SYSTEM_TEMPLATES:
        if t.template_type == template_type and t.channel == channel:
            return t
    return None
