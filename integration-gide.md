Pesagrid Integration Guide: Core Entities
This guide walks you through the lifecycle of setting up your workspace on Pesagrid—from registering a payment channel (PSP) to creating recurring obligations for your customers.

1. Registering a Payment Channel (PSP)
A Payment Channel (or 
PSPConfig
) is how your business connects to payment providers like M-PESA. Once registered, the system generates a unique webhook URL that you must register with the provider's portal (e.g., Safaricom Daraja).

Endpoint: POST /accounts/psp

Sample Payload (M-PESA)
json
{
  "psp_type": "mpesa",
  "display_name": "Safaricom M-PESA Paybill",
  "paybill": "123456",
  "credentials": {
    "consumer_key": "your_key",
    "consumer_secret": "your_secret",
    "passkey": "your_passkey",
    "environment": "sandbox"
  },
  "meta": {
    "store_number": "789012"
  }
}
2. Organizing Customers: Payer Groups
Payer Groups allow you to categorize your customers (e.g., by apartment block, school grade, or bus route).

Endpoint: POST /obligations/groups

Sample Payload
json
{
  "name": "Block A - Apartments",
  "group_type": "apartment_block",
  "description": "Tenants in the first block",
  "meta": {
    "floor_count": 5,
    "caretaker_phone": "254700000000"
  }
}
3. Registering a Payer
A Payer is the individual who will be making payments. The account_no field is critical—it's the "Account Number" or "Ref" the customer must enter when paying via M-PESA.

Endpoint: POST /obligations/payers

Sample Payload
json
{
  "name": "John Doe",
  "phone": "254712345678",
  "email": "john.doe@example.com",
  "account_no": "UNIT-101",
  "group_id": "00000000-0000-0000-0000-000000000000", // Optional: UUID from step 2
  "identifier": "TENANT-001",
  "meta": {
    "rent_amount": 25000,
    "lease_expiry": "2026-12-31"
  }
}
4. Creating Message Templates
Before creating obligations, you should set up templates for automated notifications (Reminders, Receipts, etc.).

Endpoint: POST /obligations/templates

Sample Payload (WhatsApp Reminder)
json
{
  "name": "Monthly Rent Reminder",
  "template_type": "payment_reminder",
  "channel": "whatsapp",
  "body": "Hi {{payer_name}}, your rent of KSh {{amount_due}} for {{account_no}} is due on {{due_date}}. Balance: KSh {{balance}}. Pay via Paybill 123456.",
  "is_default": true
}
5. Creating an Obligation
An obligation represents an expected payment. It can be a one-off (like a deposit) or recurring (like monthly rent).

Endpoint: POST /obligations/

Sample Payload (Monthly Recurring)
json
{
  "payer_id": "00000000-0000-0000-0000-000000000000", // UUID from step 3
  "description": "April 2025 Rent",
  "amount_due": 25000,
  "currency": "KES",
  "is_recurring": true,
  "recurring": {
    "recurrence_type": "monthly",
    "day_of_month": 1,
    "start_date": "2025-04-01T00:00:00",
    "grace_period_days": 5
  },
  "meta": {
    "invoice_no": "INV-101-APR"
  }
}