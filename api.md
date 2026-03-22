# PesaGrid Billing API Flow

This guide walks through configuring a business, setting up customers (payers), and automatically collecting recurring payments (like rent due on the 5th of every month or weekly chama contributions).

## 1. Environment & Authentication
All API requests must pass a Bearer Token.

```bash
Authorization: Bearer <your_jwt_token>
```

---

## 2. Setting Up The Payment Gateway
First, link your M-PESA Paybill or Till to your workspace so PesaGrid can listen to payments.

**`POST /api/v1/accounts/psps`**
```json
{
  "psp_type": "mpesa",
  "display_name": "Main Rent Collection",
  "paybill": "123456",
  "credentials": {"consumer_key": "...", "consumer_secret": "..."}
}
```
*Note: This generates a unique Webhook URL that you plug into your Safaricom Daraja Portal.*

---

## 3. Registering a Customer (Payer)
Next, create the person paying you. The `account_no` is critical: it’s what they type into the M-PESA "Account Number" prompt.

**`POST /api/v1/obligations/payers`**
```json
{
  "name": "Jane Doe",
  "phone": "254711223344",
  "account_no": "ROOM-04",
  "identifier": "ID-88392",
  "meta": { "building": "Sunset Apts", "floor": 1 }
}
```

---

## 4. Creating the Recurring Billing Schedule
Now, generate their recurring invoice. E.g., Rent of KES 15,000, **due on the 5th of every month**.

**`POST /api/v1/obligations/`**
```json
{
  "payer_id": "payer_uuid_here",
  "account_no": "ROOM-04",
  "description": "Monthly Rent",
  "amount_due": 15000.00,
  "currency": "KES",
  "due_date": "2026-04-05T00:00:00",
  "is_recurring": true,
  "recurring": {
    "recurrence_type": "monthly",
    "day_of_month": 5,
    "start_date": "2026-04-01T00:00:00",
    "grace_period_days": 2,
    "auto_generate": true
  },
  "meta": {
    "penalty_rate": 0.05
  }
}
```

### Flexible Recurring Options:
1. **Specific Day of Month**: `"recurrence_type": "monthly", "day_of_month": 5`
   *(Invoices generate and are due on the 5th of every month).*
2. **Specific Day of Week**: `"recurrence_type": "weekly", "day_of_week": 0` 
   *(0 = Monday, 6 = Sunday. E.g., Weekly Thursday Chama).*
3. **Custom Intervals**: `"recurrence_type": "custom", "interval_days": 14` 
   *(Bi-weekly payments).*

---

## 5. What Happens Automatically (The System Flow)

### Scenario A: The Payer Pays (M-PESA Callback)
Jane Doe goes to M-PESA, enters Paybill `123456`, Account `ROOM-04`, and pays KES 15,000.
1. Safaricom pings your **Webhook URL**.
2. PesaGrid instantly acknowledges the payment (to prevent Safaricom timeouts).
3. The background **Worker** reads the transaction, searches for the oldest open bill for `ROOM-04`, and marks it `PAID`.
4. It fires an SMS saying *"Thank you for your full payment!"*.

### Scenario B: The Payer Under-pays
Jane Doe pays KES 10,000 instead of 15,000.
1. The **Worker** matches the KES 10,000 and calculates the balance (`5,000`).
2. The obligation status is set to `PARTIAL`.
3. It fires an SMS saying *"We received a partial payment. Outstanding KES 5,000."*

### Scenario C: The 5th of Next Month Arrives (Cron Job)
On the 5th of May, at exactly midnight:
1. The **Worker Cron** wakes up and checks schedules. 
2. It generates May's new invoice for KES 15,000.
3. *If Jane never paid April's 5,000 balance*, the worker pulls it forward. 
   *(Arrears KES 5,000 + 5% penalty KES 250 + New Rent KES 15,000 = Total May Invoice: KES 20,250).*
4. It sets the old April obligation to `CANCELLED` (with a tag `"rolled_over_to": "..."`).
5. It fires an SMS saying *"Your invoice for May is ready. Total due: 20,250"*.

---

## 6. How to Stop Invoices from Generating
If Jane Doe moves out, or the Chama concludes:

**`POST /api/v1/obligations/{obligation_id}/cancel`**
Manually knocking it to `CANCELLED` turns off the `auto_generate` engine indefinitely. No more bills will be created.
