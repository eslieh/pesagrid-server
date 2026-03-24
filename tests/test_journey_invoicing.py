import pytest
import uuid
from decimal import Decimal
from app.modules.ingestion.models import Transaction, TransactionStatus
from app.modules.accounts.models import PSPConfig, PSPType
from app.modules.obligations.models import Obligation, ObligationStatus, Payer

from app.modules.ingestion.handlers import handle_webhook_mpesa
from app.rabbitmq import MessageEnvelope, EventType

@pytest.mark.asyncio
async def test_invoicing_payment_journey(client, db, mock_publisher, test_collection_id, auth_headers):
    # ─── 1. Register a Payer ──────────────────────────────────────────────────
    payer_data = {
        "name": "Jane Smith",
        "phone": "254700000101",
        "email": "jane@example.com",
        "account_no": "UNIT-101",
        "identifier": "TEN-101"
    }
    
    response = client.post(
        "/api/v1/obligations/payers",
        json=payer_data,
        headers=auth_headers
    )
    assert response.status_code == 201
    payer_id = response.json()["id"]
    
    # ─── 1b. Mock PSP Config ────────────────────────────────────────────────
    psp = PSPConfig(
        collection_id=test_collection_id,
        psp_type=PSPType.MPESA,
        display_name="M-PESA Test",
        paybill="600000",
        webhook_url="http://testserver",
        created_by=test_collection_id
    )
    db.add(psp)
    db.commit()

    
    # ─── 2. Create an Obligation (Invoice) ────────────────────────────────────
    ob_data = {
        "payer_id": payer_id,
        "amount_due": 5000.0,
        "description": "April 2025 Rent",
        "due_date": "2025-04-05T00:00:00"
    }
    
    response = client.post(
        "/api/v1/obligations/",
        json=ob_data,
        headers=auth_headers
    )
    assert response.status_code == 201
    ob_id = response.json()["id"]
    
    # Verify obligation is PENDING
    ob = db.query(Obligation).filter(Obligation.id == uuid.UUID(ob_id)).first()
    assert ob.status == ObligationStatus.PENDING
    assert ob.balance == Decimal("5000.00")
    
    # ─── 3. Simulate M-PESA Webhook (Full Payment) ───────────────────────────
    mpesa_payload = {
        "TransID": "MPESAREF456",
        "TransAmount": "5000.00",
        "MSISDN": "254700000101",
        "BillRefNumber": "UNIT-101",
        "FirstName": "Jane",
        "LastName": "Smith",
        "TransTime": "20260322120000",
        "BusinessShortCode": "600000"
    }
    
    envelope = MessageEnvelope(
        event_type=EventType.WEBHOOK_MPESA,
        payload={
             "collection_id": str(test_collection_id),
             "raw": mpesa_payload
        },
        source_service="mpesa-gateway"
    )
    
    await handle_webhook_mpesa(envelope)
    
    # ─── 4. Verify Settlement ────────────────────────────────────────────────
    # Check transaction
    txn = db.query(Transaction).filter(Transaction.psp_ref == "MPESAREF456").first()
    assert txn is not None
    assert txn.status == TransactionStatus.MATCHED
    assert str(txn.matched_obligation_id) == ob_id
    
    # Check obligation
    db.refresh(ob)
    assert ob.status == ObligationStatus.PAID
    assert ob.amount_paid == Decimal("5000.00")
    assert ob.balance == Decimal("0.00")
    
    # ─── 5. Verify Notifications ──────────────────────────────────────────────
    # Check that payment.matched was published
    event_types = [call.kwargs['event_type'].value for call in mock_publisher.publish_event.call_args_list]
    assert "payment.matched" in event_types
    
    # Find the matched event and check payload
    matched_call = next(c for c in mock_publisher.publish_event.call_args_list if c.kwargs['event_type'] == EventType.PAYMENT_MATCHED)
    assert matched_call.kwargs['payload']['obligation_id'] == ob_id
    assert matched_call.kwargs['payload']['amount'] == 5000.0
    
    # ─── 6. Verify Reporting ──────────────────────────────────────────────────
    response = client.get(
        f"/api/v1/transactions/",
        params={"account_no": "UNIT-101"},
        headers=auth_headers
    )
    assert response.status_code == 200
    txns = response.json()["items"]
    assert len(txns) == 1
    assert txns[0]["status"] == TransactionStatus.MATCHED
