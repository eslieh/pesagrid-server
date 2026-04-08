# PesaGrid Billing & Subscription Workflow (API Integration Guide)

This guide documents the full lifecycle of a tenant on the PesaGrid platform, demonstrating how the frontend client interacts with the required billing modules via the standard REST API.

## 1. Onboarding & Trial Period

When a new business signs up and creates a collection, the system automatically runs the bootstrap setup. It assigns them a **30-Day Free Trial** on the **Starter Plan**, along with a pre-paid wallet (initialized at KES 0.00).

- **API Access**: All API calls operate without billing restrictions or top-up requirements during this 30-day window (`status = TRIAL`).

## 2. Choosing a Subscription Plan

Once the trial expires (or if the user voluntarily decides to upgrade early), the tenant must select an active subscription plan.

### Fetching Plans
The client app calls the public API to list available plans:
```http
GET /api/v1/billing/plans
```
**Response Details**: Returns the active platform plans (e.g. Starter, Growth, Enterprise).

```json
{
  "items": [
    {
      "id": "9e348edd-a52f-43a9-99c6-f8942ca9e493",
      "slug": "starter",
      "name": "Starter",
      "monthly_fee_kes": "3000.00",
      "recon_fee_kes": "0.3000",
      "notification_fee_kes": "0.5000",
      "wallet_minimum_kes": "5000.00",
      "max_branches": 1,
      "max_psps": 2,
      "requires_custom_quote": false,
      "features": {
        "detailed_ledger": false,
        "real_time_recon": false,
        "automated_mpesa_import": true,
        "weekly_reporting_emails": true
      }
    }
    // ... (Growth, Enterprise)
  ]
}
```

### Subscribing / Changing Plans
When the user picks a plan from the frontend pricing view, the client POSTs the slug:
```http
POST /api/v1/billing/subscribe
Content-Type: application/json

{
  "plan_slug": "growth"
}
```

**Response**:
```json
{
  "id": "18f9a3ee-8e...",
  "collection_id": "77f...",
  "plan": {
    "slug": "growth",
    "name": "Growth",
    "monthly_fee_kes": "12000.00"
    // ...
  },
  "status": "active",
  "current_period_start": "2026-04-08T12:00:00Z",
  "current_period_end": "2026-05-01T00:00:00Z",
  "recon_count": 0,
  "notification_count": 0,
  "created_at": "2026-04-08T12:00:00Z"
}
```

> **Note**: Moving to a paid plan sets the tenant's ongoing status to `ACTIVE`. The frontend should strictly prompt the user to load their wallet after choosing a plan because paid tiers require a hard **wallet minimum** rule (e.g., KES 15,000 for Growth) for automatic fees.

---

## 3. The Prepaid Wallet & Top-ups

PesaGrid operates on a **Prepaid Wallet Engine**. Before sending SMS reminders or receiving automated reconciliation matches, the business must have funded ledger credits.

### Checking Wallet & Usage Summary
To see a complete dashboard overview of the tenant's exact balance alongside how much credit they've burnt in the current month, standardly poll the summary endpoint:
```http
GET /api/v1/billing/summary
```

**Response**:
```json
{
  "subscription": {
    "plan": { "slug": "growth", "name": "Growth", "monthly_fee_kes": "12000.00" },
    "status": "active",
    "recon_count": 1500,
    "notification_count": 800
  },
  "wallet": {
    "balance_kes": "850.00",
    "lifetime_topup": "20000.00",
    "is_auto_deduct_enabled": true
  },
  "recon_est": "300.00",
  "notification_est": "400.00",
  "current_month_est": "700.00" 
}
```
*`current_month_est` identifies the total estimated cost of all background usage accumulated in the current billing cycle. To keep the financial statement clean, individual 50-cent deductions are **not** logged as separate ledger rows—instead, the wallet balance is updated in real-time, and granular audits can be performed via the summary view or raw notification logs.*

### Loading the Wallet (Paystack Checkout)
When the user clicks "Top Up Wallet", follow this 3-step Paystack flow:

1. **Initiate Payment Request**: Submit the amount to top up into the system.
   ```http
   POST /api/v1/billing/wallet/topup
   Content-Type: application/json

   { 
     "amount_kes": 20000.00, 
     "email": "dashboard@pesagrid.com",
     "callback_url": "https://dashboard.pesagrid.com/wallet"
   }
   ```
   **Response**:
   ```json
   {
     "payment_url": "https://checkout.paystack.com/xxyyzz",
     "reference": "PG-TOPUP-XXXXX",
     "amount_kes": "20000.00"
   }
   ```

2. **Redirect Flow**: The API returns a `payment_url`. The client should redirect the user to this Paystack checkout URL string to fulfill via browser.
3. **Verify Payment**: Upon returning to the dashboard from Paystack via the callback URL, extract the `reference` query parameter and verify the purchase permanently.
   ```http
   POST /api/v1/billing/wallet/topup/verify
   Content-Type: application/json

   { 
     "reference": "PG-TOPUP-XXXXX" 
   }
   ```
   
   **Response**:
   ```json
   {
     "success": true,
     "amount_credited": "20000.00",
     "balance_kes": "20850.00",
     "message": "KES 20000.00 credited to your wallet."
   }
   ```

> **Tip**: The backend architecture simultaneously runs a silent webhook (`POST /api/v1/billing/paystack/webhook`) that listens to Paystack strictly out-of-band. Even if a user closes their browser randomly during Step 2 before the redirect back to the app, the money is still deposited safely into their wallet automatically!

---

## 4. Usage Deductions

Once the wallet has balance, the platform charges usage continuously **in the background**. The client frontend **does not** need to make manual API calls or math for these deductions.

Here is what happens under the backend hood:
- **Reconciliation Engine**: When an M-PESA payment flows through webhooks and matches successfully, the engine fires a `BILLING_RECON_DONE` internal trace event. `KES 0.20` is quietly deducted from the wallet balance and the `recon_count` is incremented.
- **SMS/Email Notifications**: Every time the notification dispatcher pushes an outgoing email receipt or scheduled overdue SMS successfully, it registers a `BILLING_NOTIFICATION_SENT` internal trace. `KES 0.50` is deducted from the wallet and the `notification_count` is incremented.

> **Clutter Management**: To keep the Wallet Transactions list readable, these individual small deductions are **not** recorded as separate transaction rows. The balance is updated silently in real-time for enforcement, and the total usage is summarized in the monthly platform invoice.

### Viewing Wallet Ledger History
To display the deduction history (a transparent live statement of how the wallet generates debits or top-ups), the frontend UI queries:
```http
GET /api/v1/billing/wallet/transactions?limit=50
```

**Response**:
```json
{
  "total": 1,
  "balance_kes": "20850.00",
  "items": [
    {
      "id": "bb44...",
      "tx_type": "deduction",
      "event_type": "sms",
      "amount_kes": "0.5000",
      "balance_after": "20849.50",
      "description": "Sms × 1 @ KES 0.5000",
      "reference": null,
      "meta": {
        "channel": "sms",
        "payer_id": "99f..."
      },
      "created_at": "2026-04-08T14:02:00Z"
    }
  ]
}
```

---

## 5. Grace Periods & Suspensions Handling

If the internal wallet balance progressively drops below the plan's `wallet_minimum_kes` (e.g., dropping below KES 5,000 on the Starter plan), an internal Cron job triggers backend safety protocols.

> **Warning Status (`SUSPENDED`)**  
> The tenant's account enters a suspended state. They are given a **7-Day Grace Period** to top up their wallet back to minimums. During this time, functional features (like automatic SMS logs) technically continue to work, but the frontend should detect the `SUSPENDED` flag on calls to `/api/v1/billing/subscription` and display a prominent red **alert banner** urging the user to deposit funds safely.

If the 7 days pass fully and the minimum balance limits aren't resolved securely, the account switches into a hard **`BLOCKED`** state.
- All API modification calls across the platform modules logic will automatically return a strict `403 Forbidden` (`"Your subscription is blocked due to insufficient wallet balance..."`).
- System-heavy engine features like real-time M-PESA matching reconciliations are officially paused for the account.

---

## 6. Monthly Invoices (Fixed Tiers & Auto-Payment)

On the **1st of every calendar month** exactly at 00:05 AM East African Time, a core backend Cron Job activates a sweeping calculation across the entire platform.

1. **Generation Workflow**: Calculates the pending monthly `subscription_fee` for every plan tier (e.g., KES 3,000 base). It additionally reads all numeric usage counters metered globally across the previous 30 calendar days. 
2. **SaaS Invoice**: Generates formal `PlatformInvoice` mappings storing exact usage lines.
3. **Automated Deductions**: The backend looks at the user's `TenantWallet`. If the active wallet safely covers the resulting baseline costs, it automatically executes the required deductions internally to "Settle the Platform" mapping strings. It updates the monthly invoice to a fully `PAID` state immediately!
4. **Dashboard Flag**: If the auto-deduction fails to execute correctly (because of absolute zero funds or massive usage metrics exceeding internal balances), the respective invoice maintains the visible `SENT` or `OVERDUE` state, awaiting user intervention.

### Fetching App Invoices
The frontend UI lists these persistent records for business owners to audit and maintain accurate reporting around their platform SaaS spending:
```http
GET /api/v1/billing/invoices
```
*(This payload returns a cleanly formatted multi-dimensional array mapping out: platform fixed recurring costs + exact reconciliation iteration counts + detailed notification quantities, plus overall final payment dispositions.)*

**Response**:
```json
{
  "total": 1,
  "items": [
    {
      "id": "e22a...",
      "invoice_number": "INV-2026-04-00001",
      "period_start": "2026-03-01T00:00:00Z",
      "period_end": "2026-04-01T00:00:00Z",
      "subscription_fee_kes": "3000.00",
      "recon_count": 1500,
      "recon_fee_total_kes": "450.0000",
      "notification_count": 800,
      "notification_fee_total_kes": "400.0000",
      "total_amount_kes": "3850.00",
      "status": "paid",
      "paystack_payment_link": null,
      "paid_at": "2026-04-01T00:05:10Z",
      "sent_at": "2026-04-01T00:05:01Z",
      "created_at": "2026-04-01T00:05:00Z"
    }
  ]
}
```
