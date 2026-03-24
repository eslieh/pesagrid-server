---
name: pesagrid_architecture
description: Core architecture conventions for the Pesagrid (PayIntel) backend — a multi-tenant async payment reconciliation system.
---

# Pesagrid Backend Architecture

## Overview
Pesagrid is a **multi-tenant payment reconciliation platform** built on:
- **FastAPI** (async HTTP layer)
- **SQLAlchemy** (sync ORM with PostgreSQL)
- **RabbitMQ via aio_pika** (async event bus — fire-and-forget side effects)
- **Redis** (caching, rate limiting)
- **Alembic** (schema migrations, one schema per module)

---

## Layer Architecture

```
Ingestion Layer  →  Intelligence Layer  →  Delivery Layer
(Webhooks)          (Matching Engine)       (Dashboard / APIs / Notifications)
```

### Three Core Layers
| Layer | Purpose | Key Modules |
|---|---|---|
| **Ingestion** | Receive & normalize payment callbacks | `ingestion/` |
| **Intelligence** | Match payments to obligations, anomaly detection | `obligations/`, `accounts/` |
| **Delivery** | Dashboard, REST APIs, WhatsApp/SMS reminders | `dashboard/`, `notifications/` |

---

## Module Structure Convention
Every module follows this layout:
```
app/modules/<name>/
  models.py    # SQLAlchemy ORM models + Pydantic schemas together
  schema.py    # Pydantic request/response types (not in models)
  services.py  # Business logic class, one service per module
  routers.py   # FastAPI APIRouter, thin — delegates to service
```

### Schema Convention
- **PostgreSQL schema per module**: every model's `__table_args__` includes `{"schema": "<module_name>"}`.
- Foreign keys use fully qualified names: `"obligations.obligations.id"`.

### Service Convention
- Services are **async classes** instantiated per request with `db: Session`.
- Side effects (notifications, analytics events) are published to **RabbitMQ**, never done inline.
- Services should raise `HTTPException` with appropriate status codes.

---

## Async Event Bus (RabbitMQ)

### When to publish events
Publish a RabbitMQ event for any **significant state change** that has side effects outside the current module:
- Obligation created → notify payer via WhatsApp/SMS
- Payment received → trigger reconciliation engine
- Obligation overdue → trigger reminder flow

### How to publish
```python
from app.rabbitmq.publisher import BasePublisher
from app.rabbitmq.types import EventType, Priority

publisher = BasePublisher(service_name="obligations-service")
await publisher.publish_event(
    event_type=EventType.OBLIGATION_CREATED,
    payload={"obligation_id": str(obligation.id), "payer_phone": payer_phone},
    priority=Priority.MEDIUM
)
```

### Event Naming Convention
All EventType values follow the pattern: `<domain>.<subject>.<verb>`:
- `obligation.created`
- `obligation.overdue`
- `payment.matched`
- `notification.sms.send`
- `notification.email.send`

### Message Envelope
Every event is wrapped in `MessageEnvelope` (from `app.rabbitmq.types`):
```python
class MessageEnvelope(BaseModel):
    event_id: str          # Auto UUID
    event_type: EventType
    timestamp: datetime
    payload: Dict[str, Any]
    source_service: str
    target_service: Optional[str]
    priority: Priority
```

---

## Multi-Tenancy
Every resource belongs to a **Collection** (the tenant workspace). Rules:
1. All queries MUST filter by `collection_id` derived from the authenticated user's workspace.
2. Never expose cross-tenant data — always scope queries.
3. The `accounts` module owns the `SubAccount` (account_no ↔ collection mapping).

---

## Authentication
- JWT-based, tokens delivered via HttpOnly cookies (web) or `Authorization: Bearer` header (API/S2S).
- Use `get_current_verified_user` for all protected routes.
- User model lives in `auth.users` (PostgreSQL schema `auth`).

---

## Key Entities & Relationships

```
Collection (tenant workspace)
  └── SubAccount (account_no, label, owner)
        └── Obligation (amount_due, due_date, payer, status)
              └── Payment (incoming transaction, matched_to obligation)
```

### Obligation Statuses
| Status | Meaning |
|---|---|
| `pending` | Not yet due or awaiting payment |
| `partial` | Payment received but insufficient |
| `paid` | Fully matched |
| `overdue` | Past due date, still unpaid |
| `cancelled` | Obligation cancelled by operator |

### Recurrence Types (for recurring obligations)
| Type | Example |
|---|---|
| `monthly` | Rent (1st of month) |
| `weekly` | Bus fare reporting |
| `term` | School fees per term |
| `custom` | Configurable interval |

---

## Database Patterns
- Use `app.core.db_types.UUID` (cross-db compatible) and `uuid4` factory for all primary keys.
- All timestamps use `datetime.utcnow` default.
- Use `Index(...)` for any column queried frequently.
- Always add `__table_args__` with the module schema and composite indexes.

---

## Router Registration
Every new module router must be registered in `main.py`:
```python
from app.modules.<name>.routers import <name>_router
app.include_router(<name>_router, prefix="/api/v1/<name>", tags=["<Name>"])
```
