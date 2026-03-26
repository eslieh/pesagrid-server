import pytest
import uuid
from datetime import datetime, timedelta
from app.modules.ingestion.models import Transaction, TransactionStatus, CollectionPoint
from app.core.timezone import now_nairobi

@pytest.mark.asyncio
async def test_dashboard_analytics(client, db, test_collection_id, auth_headers):
    # 1. Setup Collection Point
    cp = CollectionPoint(
        collection_id=test_collection_id,
        name="Test Bus",
        account_no="BUS-001",
        is_active=True
    )
    db.add(cp)
    db.commit()
    cp_id = cp.id

    # 2. Add some transactions
    now = now_nairobi()
    t1 = Transaction(
        collection_id=test_collection_id,
        collection_point_id=cp_id,
        amount=1000,
        status=TransactionStatus.MATCHED,
        psp_ref="REF1",
        psp_type="mpesa",
        ingested_at=now - timedelta(hours=2)
    )
    t2 = Transaction(
        collection_id=test_collection_id,
        collection_point_id=cp_id,
        amount=500,
        status=TransactionStatus.UNMATCHED,
        psp_ref="REF2",
        psp_type="mpesa",
        ingested_at=now - timedelta(days=1)
    )
    t3 = Transaction(
        collection_id=test_collection_id,
        amount=2000,
        status=TransactionStatus.MATCHED,
        psp_ref="REF3",
        psp_type="mpesa",
        ingested_at=now
    )
    db.add_all([t1, t2, t3])
    db.commit()

    # 3. Test /metrics with filter
    response = client.get(
        "/api/v1/dashboard/metrics",
        params={"collection_point_id": str(cp_id)},
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_collected"] == 1500.0
    assert data["total_matched"] == 1000.0
    assert data["total_unmatched"] == 500.0

    # 4. Test /collections/trends
    response = client.get(
        "/api/v1/dashboard/collections/trends",
        params={"interval": "day"},
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["interval"] == "day"
    assert len(data["trends"]) >= 1

    # With filter
    response = client.get(
        "/api/v1/dashboard/collections/trends",
        params={"interval": "day", "collection_point_id": str(cp_id)},
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    # Should only see 1500 total for this CP
    total_in_trends = sum(t["total"] for t in data["trends"])
    assert total_in_trends == 1500.0

    # 5. Test /collections/peak-times
    response = client.get(
        "/api/v1/dashboard/collections/peak-times",
        params={"collection_point_id": str(cp_id)},
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["peaks"]) >= 1
    # Check if the hours are correct (now-2h and now-24h)
    hours = [p["hour"] for p in data["peaks"]]
    assert (now - timedelta(hours=2)).hour in hours
    assert (now - timedelta(days=1)).hour in hours
