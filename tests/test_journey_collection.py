import pytest
import uuid
from app.modules.ingestion.models import Transaction, TransactionStatus
from app.modules.accounts.models import PSPConfig, PSPType
from app.modules.ingestion.reconciliation import reconcile_transaction

from app.modules.ingestion.handlers import handle_webhook_mpesa
from app.rabbitmq import MessageEnvelope, EventType

@pytest.mark.asyncio
async def test_fleet_collection_journey(client, db, mock_publisher, test_collection_id, auth_headers):
    # ─── 1. Register a Fleet Vehicle (Collection Point) ─────────────────────
    cp_data = {
        "name": "Bus KAB-123C",
        "account_no": "KAB123C",
        "description": "City Circle Route",
        "is_active": True,
        "meta": {"route": "CBD-Westlands"}
    }
    
    response = client.post(
        f"/api/v1/collection-points/",  # Tenant ID inherited from mocked current_user
        json=cp_data,
        headers=auth_headers
    )
    assert response.status_code == 201
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

    cp_id = response.json()["id"]
    
    # ─── 2. Simulate M-PESA Webhook (Payment for this Bus) ───────────────────
    mpesa_payload = {
        "TransID": "LGR019G3J4",
        "TransAmount": "1500.00",
        "MSISDN": "254712345678",
        "BillRefNumber": "KAB123C",
        "FirstName": "John",
        "LastName": "Doe",
        "TransTime": "20260322100000",
        "BusinessShortCode": "600000"
    }
    
    # This endpoint just queues the message
    response = client.post(
        f"/api/v1/ingest/{test_collection_id}/mpesa/callback",
        json=mpesa_payload
    )
    assert response.status_code == 200
    assert response.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}
    
    # ─── 3. Simulate Worker Processing ───────────────────────────────────────
    # We call the handler directly with an envelope
    envelope = MessageEnvelope(
        event_type=EventType.WEBHOOK_MPESA,
        payload={
             "collection_id": str(test_collection_id),
             "raw": mpesa_payload
        },
        source_service="mpesa-gateway"
    )
    
    await handle_webhook_mpesa(envelope)
    
    # Verify transaction in DB
    txn = db.query(Transaction).filter(Transaction.psp_ref == "LGR019G3J4").first()
    assert txn is not None
    assert txn.status == TransactionStatus.CATEGORIZED
    assert str(txn.collection_point_id) == cp_id
    
    # ─── 4. Verify Dashboard/Reporting ────────────────────────────────────────
    # Check aggregate totals
    response = client.get(
        f"/api/v1/collection-points/{cp_id}/totals",
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_collected"] == 1500.0
    assert data["name"] == "Bus KAB-123C"
    
    # Check transaction list
    response = client.get(
        f"/api/v1/transactions/",
        params={"collection_point_id": cp_id},
        headers=auth_headers
    )
    assert response.status_code == 200
    txns = response.json()["items"]
    assert len(txns) == 1
    assert txns[0]["psp_ref"] == "LGR019G3J4"
