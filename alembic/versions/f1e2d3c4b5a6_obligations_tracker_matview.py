"""obligations tracker — materialized view + indexes

Revision ID: f1e2d3c4b5a6
Revises: 6699952c7583
Create Date: 2026-04-12 14:36:00

Adds:
  1. Composite index on obligations (collection_id, status, due_date) — powers
     date-range + status filters without full-table scans.
  2. Materialized view obligations.ledger_summary — pre-aggregated per-payer
     totals (total_due, total_paid, balance, overdue/pending/settled counts).
     Refreshed CONCURRENTLY every 5 minutes via cron so reads are never blocked.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "f1e2d3c4b5a6"
down_revision = "6699952c7583"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Extra composite indexes on base tables ──────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_obligations_collection_status_due
        ON obligations.obligations (collection_id, status, due_date);
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_payers_collection_group_active
        ON obligations.payers (collection_id, group_id, is_active);
    """)

    # ── 2. Materialized view ───────────────────────────────────────────────────
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS obligations.ledger_summary AS
        SELECT
            o.collection_id,
            p.group_id,
            o.payer_id,
            p.name              AS payer_name,
            p.phone             AS payer_phone,
            p.account_no        AS payer_account_no,
            p.is_active         AS payer_is_active,
            COUNT(o.id)         AS total_obligations,
            COALESCE(SUM(o.amount_due),  0)  AS total_due,
            COALESCE(SUM(o.amount_paid), 0)  AS total_paid,
            COALESCE(SUM(o.balance),     0)  AS total_balance,
            COUNT(*) FILTER (WHERE o.status::text = 'overdue')                          AS overdue_count,
            COUNT(*) FILTER (WHERE o.status::text IN ('pending', 'partial'))             AS pending_count,
            COUNT(*) FILTER (WHERE o.status::text = 'settled')                          AS settled_count,
            MIN(o.due_date) FILTER (
                WHERE o.status::text IN ('pending', 'partial', 'overdue')
                  AND o.due_date IS NOT NULL
            )                   AS next_due_date,
            MAX(o.updated_at)   AS last_activity
        FROM obligations.obligations o
        JOIN obligations.payers p ON p.id = o.payer_id
        GROUP BY
            o.collection_id,
            p.group_id,
            o.payer_id,
            p.name,
            p.phone,
            p.account_no,
            p.is_active
        WITH DATA;
    """)

    # Unique index required for CONCURRENT refresh
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_summary_pk
        ON obligations.ledger_summary (collection_id, payer_id);
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ledger_summary_group
        ON obligations.ledger_summary (collection_id, group_id);
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ledger_summary_overdue
        ON obligations.ledger_summary (collection_id, overdue_count DESC);
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ledger_summary_balance
        ON obligations.ledger_summary (collection_id, total_balance DESC);
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS obligations.ledger_summary;")
    op.execute("DROP INDEX IF EXISTS obligations.idx_obligations_collection_status_due;")
    op.execute("DROP INDEX IF EXISTS obligations.idx_payers_collection_group_active;")
