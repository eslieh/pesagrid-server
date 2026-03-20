Pesagrid MVP — Functional Requirements
1. Authentication & Workspace
* Business can register an account
* Business can login/logout
* Each business has its own workspace (multi-tenant)
2. Accounts & Obligations
* Business can create sub-accounts (account numbers)
* Business can create payment obligations
* Obligation has amount, due date, account number, payer
* Business can edit / delete obligations
3. Payment Ingestion
* System receives payment notifications via webhook
* System stores payment transactions
* System prevents duplicate transactions
* Business can manually add payments
4. Matching / Reconciliation
* System matches payment to obligation using account number + amount
* System marks obligation as paid / partial / unpaid
* System shows unmatched payments
5. Dashboard & Reports
* Business can see total collected
* Business can see outstanding balances
* Business can see payments per account
* Business can see payment history
6. Notifications
* Send acknowledgement after payment
* Send reminder before due date
* Send overdue reminder
(SMS / WhatsApp can be mocked in MVP)
7. SaaS Billing
* Business subscribes to plan
* System tracks active subscription
* Only active subscription can use system

obligation → payment → match → notify → view → repeat

