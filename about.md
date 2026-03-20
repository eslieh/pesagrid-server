// Payment Intelligence System
PayIntel
Reconciliation infrastructure for businesses collecting payments across multiple channels & accounts

Layer 1 — Ingestion
Webhook Gateway
Listens to M-PESA, KCB, Equity, Co-op, IntaSend, SWIFT-WALLET callbacks. Normalizes them all into one unified event schema — regardless of source format.

Layer 2 — Intelligence
Processing Engine
Matches incoming payments to expected obligations. Detects anomalies, runs ML forecasting, fires alerts when payments are late, partial, or go to wrong accounts.

Layer 3 — Delivery
Dashboard & APIs
Multi-tenant web UI (think Notion — each business configures their own view). REST APIs for ERP sync. WhatsApp/SMS/email reminders sent automatically.

// System Architecture — Data Flow

⬇ Ingestion Layer
Payment sources sending callbacks:

M-PESA STK / C2B 
KCB BUNI 
Equity 
Co-op 
IntaSend 
SmartPay 
SWIFT-WALLET
🔗
Webhook Normalization
Every source's payload → unified event schema with amount, ref, account, timestamp
🔑
Account Routing
Paybill 123456 + Account "BUS-07" maps to Mwangi's bus. Account "UNIT-3B" maps to house unit.
⚡
Idempotency
Deduplication prevents double-counting same transaction
Normalized Events
▶
⚙ Intelligence Layer
Core reconciliation logic:

🔍
Obligation Matching
"Kamau owes KSh 12,000 rent for Unit 3B in April" → did payment arrive? Exact / partial / excess?
⏰
Due Date Engine
Knows rent is due 1st of month. Bus fare is real-time. Invoices have 30-day terms.
🧠
Anomaly Detection
Wrong account, amount mismatch, unusual payer, duplicate ref, payment to wrong bus account
📈
Cash Flow Forecasting
ML predicts collection rates: "Bus 7 expected KSh 45K this week, currently at KSh 28K"
📋
Custom Rules
Tenant configures: grace period, late fee %, reminder schedule, escalation paths
Reconciled Data
▶
📤 Delivery Layer
How insights reach users:

🖥
Multi-tenant Dashboard
Each business sees their own workspace. Landlord sees units. SACCO sees buses. Like Notion — you build your view.
💬
WhatsApp Reminders
"Hi Kamau, your rent of KSh 12,000 for Unit 3B is due in 3 days. Pay via Paybill 123456 Acc 3B."
📱
SMS / Email Alerts
Overdue escalations, payment confirmations, weekly collection summaries
🔌
REST API / ERP Sync
Push reconciled data to QuickBooks, Sage, Excel, custom accounting tools
// Real-World Use Cases — Who Uses This

🏠
Rental Management
Landlord has 40 units all paying to one M-PESA paybill with different account numbers per unit. System knows who paid, who hasn't, who paid short, sends reminders automatically.

M-PESA Paybill + Account No.
🚌
Bus / SACCO Fares
SACCO owns 15 buses. Each bus gets an account number on the same paybill. System tracks daily collections per bus, flags if a bus is underreporting, forecasts monthly earnings.

Fleet Revenue Tracking
🏫
School Fees
School tracks fee balance per student. Parents pay via multiple channels. System reconciles partial payments, tracks outstanding balances, notifies parents of unpaid fees before term ends.

Multi-installment Tracking
🏪
Wholesale / B2B Invoices
Distributor issues invoices to 200 retailers with 30-day credit terms. Tracks which invoices are paid, partially paid, or overdue. Auto-sends statement reminders.

Invoice Reconciliation
⚽
Chama / Group Savings
Investment group tracks monthly contributions per member. Knows who's current, who's in arrears. Records loans issued and repayments. Generates statements for AGM.

Member Contribution Tracking
💧
Utilities / Water Bills
Water company issues monthly bills per meter. Tracks consumption-based obligations vs payments received. Flags disconnection candidates. Handles prepaid vs postpaid models.

Recurring Billing
// Core Data Model

4 Key Entities
The system revolves around matching Obligations (who should pay what, by when) against Payments (what actually came in, via which channel, to which account).

🏢 Collection (Tenant Workspace)
e.g. "Sunrise Apartments"
id
uuid
name
string
paybill
string
channels
array
rules
json
members
array
📋 Obligation (Expected Payment)
e.g. "Kamau — Unit 3B — April rent"
payer_id
ref
amount_due
number
due_date
date
account_no
string
status
enum
balance
number
💳 Payment (Actual Transaction)
from M-PESA callback
mpesa_ref
string
amount
number
phone
string
account_no
string
timestamp
datetime
channel
enum
matched_to
ref
anomaly_flags
array
🏦 Account (Sub-account / Unit)
e.g. "BUS-07" or "UNIT-3B"
account_no
string
label
string
owner_id
ref
collection_id
ref
balance
number
last_payment
datetime
The magic: When M-PESA sends "Paybill 123456, Acc BUS-07, KSh 850, from 0712345678" — the system looks up BUS-07, finds the obligation for Bus 7's daily fare collection, marks it matched, updates the balance, and logs it to that bus owner's dashboard. All automatic.

// Multi-Tenant Architecture

"Like Notion — each business builds their own view"
One platform, infinite configurations. A landlord's workspace looks completely different from a SACCO's or a school's — but they all run on the same reconciliation engine underneath.

🏠
Landlord Workspace
Units grid → per-unit payment status → outstanding balances → auto-reminder schedule → eviction escalation tracking

🚌
SACCO Workspace
Fleet dashboard → per-bus daily collections → driver accountability → weekly revenue forecast → route performance

🏫
School Workspace
Class-wise fee tracking → student balance ledger → term deadline alerts → parent communication log

🏪
Distributor Workspace
Invoice aging report → customer credit limits → overdue escalation → collections team performance

// Reconciliation Flow — What Happens When a Payment Arrives

01
Payment Received
M-PESA fires webhook. System receives raw callback: ref, amount, phone, account_no, timestamp.

INGESTED
02
Account Lookup
Account "BUS-07" matched to Mwangi's Bus 7 in Sunrise SACCO workspace. Tenant identified.

ROUTED
03
Obligation Match
Find open obligation for this account. Check: is amount correct? Is it on time? Partial or excess?

CHECKED
04
Anomaly Scan
ML checks: Is this payer known? Is amount unusual? Duplicate ref? Wrong account? Flag if suspicious.

SCANNED
05
Notify & Update
Update dashboard. Send WhatsApp receipt to payer. Alert owner. Queue reminder if still outstanding.

DELIVERED
PayIntel
Payment Reconciliation Infrastructure
Ingestion → Intelligence → Delivery
M-PESA · KCB · Equity · Co-op · IntaSend · SWIFT