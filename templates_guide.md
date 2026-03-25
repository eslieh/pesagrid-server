# Pesagrid: Unified Notification Templates

Use these payloads to create templates for all core obligation events. By using `channel: "all"`, you can set up one template that covers both SMS and Email.

## 1. Obligation Created
**Template Type:** `obligation_created`  
**Events:** `obligation.created`

```json
{
  "name": "New Bill Alert",
  "template_type": "obligation_created",
  "channel": "all",
  "subject": "New Bill: {{description}} Pending for {{account_no}}",
  "body": "Hi {{payer_name}}, a new bill of {{currency}} {{amount_due}} for {{description}} is due on {{due_date}}. Pay via Paybill {{paybill}} using account {{account_no}}.",
  "is_default": true
}
```

---

## 2. Payment Reminder
**Template Type:** `payment_reminder`  
**Events:** `obligation.due`

```json
{
  "name": "Payment Reminder",
  "template_type": "payment_reminder",
  "channel": "all",
  "subject": "Reminder: Bill for {{description}} due soon",
  "body": "Hi {{payer_name}}, reminder that your bill of {{currency}} {{balance}} for {{description}} (Acc: {{account_no}}) is due on {{due_date}}.",
  "is_default": true
}
```

---

## 3. Partial Payment Received
**Template Type:** `payment_receipt`  
**Events:** `payment.partial`

```json
{
  "name": "Partial Payment Receipt",
  "template_type": "payment_receipt",
  "channel": "all",
  "subject": "Payment Received: {{currency}} {{amount_paid}} (Balance Remains)",
  "body": "Confirmed: {{currency}} {{amount_paid}} received for {{description}}. Your new balance is {{currency}} {{balance}}.",
  "is_default": true
}
```

---

## 4. Full Payment Acknowledgment
**Template Type:** `payment_receipt_full`  
**Events:** `payment.matched`

```json
{
  "name": "Full Settlement Receipt",
  "template_type": "payment_receipt_full",
  "channel": "all",
  "subject": "Payment Fully Settled: {{description}}",
  "body": "Hi {{payer_name}}, your payment of {{currency}} {{amount_paid}} for {{description}} has been fully settled. Thank you!",
  "is_default": true
}
```

---

## 5. Obligation Cancelled
**Template Type:** `obligation_cancelled`  
**Events:** `obligation.cancelled`

```json
{
  "name": "Obligation Cancelled Notice",
  "template_type": "obligation_cancelled",
  "channel": "all",
  "subject": "Notice: Obligation Cancelled - {{account_no}}",
  "body": "Hi {{payer_name}}, this is to inform you that your obligation for {{description}} (Acc: {{account_no}}) has been cancelled.",
  "is_default": true
}
```

> [!TIP]
> Even with `channel: "all"`, Emails will still use your professional HTML layout automatically.
